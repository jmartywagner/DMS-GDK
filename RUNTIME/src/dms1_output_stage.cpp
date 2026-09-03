#include "dms1_output_stage.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace dms1 {
namespace {

constexpr double kPi = 3.14159265358979323846264338327950288;
constexpr double kOutputGain = 2.8183829312644537; // +9.00 dB
constexpr double kMixerRail = 32'767.0;
constexpr double kMixerKnee = 30'000.0;

} // namespace

const char* output_stage_name(OutputStage stage) noexcept {
    return stage == OutputStage::Hardware ? "DMS-1 HARDWARE" : "RAW DIGITAL";
}

const char* output_stage_cli_name(OutputStage stage) noexcept {
    return stage == OutputStage::Hardware ? "hardware" : "raw";
}

HardwareOutputStage::HardwareOutputStage(double host_rate) {
    reset(host_rate);
}

void HardwareOutputStage::DcBlocker::configure(double sample_rate,
                                                double cutoff_hz) {
    alpha = std::exp(-2.0 * kPi * cutoff_hz / sample_rate);
}

void HardwareOutputStage::DcBlocker::reset() noexcept {
    previous_input = 0.0;
    previous_output = 0.0;
}

double HardwareOutputStage::DcBlocker::process(double input) noexcept {
    const double output = alpha * (previous_output + input - previous_input);
    previous_input = input;
    previous_output = output;
    return output;
}

void HardwareOutputStage::LowPass::configure(double sample_rate,
                                              double cutoff_hz,
                                              double q) {
    const double omega = 2.0 * kPi * cutoff_hz / sample_rate;
    const double cosine = std::cos(omega);
    const double sine = std::sin(omega);
    const double alpha = sine / (2.0 * q);
    const double a0 = 1.0 + alpha;
    b0 = ((1.0 - cosine) * 0.5) / a0;
    b1 = (1.0 - cosine) / a0;
    b2 = b0;
    a1 = (-2.0 * cosine) / a0;
    a2 = (1.0 - alpha) / a0;
}

void HardwareOutputStage::LowPass::reset() noexcept {
    z1 = 0.0;
    z2 = 0.0;
}

double HardwareOutputStage::LowPass::process(double input) noexcept {
    const double output = b0 * input + z1;
    z1 = b1 * input - a1 * output + z2;
    z2 = b2 * input - a2 * output;
    return output;
}

void HardwareOutputStage::reset(double host_rate) {
    if (!std::isfinite(host_rate) || host_rate < 8'000.0 || host_rate > 384'000.0) {
        throw std::invalid_argument("frequence de sortie materielle hors plage 8..384 kHz");
    }
    host_rate_ = host_rate;
    internal_rate_ = host_rate * kOversampleFactor;

    for (auto& filter : fm_coupling_) {
        filter.configure(internal_rate_, 9.5);
        filter.reset();
    }
    for (auto& filter : pcm_coupling_) {
        filter.configure(internal_rate_, 9.5);
        filter.reset();
    }
    for (auto& filter : ssg_coupling_) {
        filter.configure(internal_rate_, 18.0);
        filter.reset();
    }
    const double reconstruction_cutoff = std::min(16'000.0, host_rate * 0.38);
    for (auto& filter : reconstruction_) {
        filter.configure(internal_rate_, reconstruction_cutoff, 0.72);
        filter.reset();
    }

    for (auto& channel : capture_history_) channel.fill(0.0);
    capture_cursor_ = 0;
    configure_capture_filter();
}

int64_t HardwareOutputStage::apply_gain(int64_t value, uint8_t reg) {
    if (reg & 0x80) return 0;
    static const std::array<int32_t, 64> table = [] {
        std::array<int32_t, 64> result{};
        for (size_t step = 0; step < result.size(); ++step) {
            const double db = -0.75 * double(step);
            result[step] = static_cast<int32_t>(
                std::llround(std::pow(10.0, db / 20.0) * (1 << 20)));
        }
        return result;
    }();
    const int64_t product = value * table[reg & 0x3f];
    return product >= 0 ? (product + (1 << 19)) >> 20
                        : -(((-product) + (1 << 19)) >> 20);
}

int16_t HardwareOutputStage::quantize_floating_dac(int16_t sample) noexcept {
    const uint16_t bits = static_cast<uint16_t>(sample);
    const uint16_t sign = bits >> 15;
    int discarded_bits = 6;
    for (int bit = 14; bit >= 9; --bit) {
        if (((bits >> bit) & 1U) != sign) break;
        --discarded_bits;
    }
    const uint16_t mask = discarded_bits == 0
                              ? std::numeric_limits<uint16_t>::max()
                              : static_cast<uint16_t>(~((1U << discarded_bits) - 1U));
    const uint16_t quantized = bits & mask;
    const int32_t signed_value = (quantized & 0x8000U)
                                     ? int32_t(quantized) - 65'536
                                     : int32_t(quantized);
    return static_cast<int16_t>(signed_value);
}

int16_t HardwareOutputStage::saturate_dac(int64_t value,
                                          HardwareOutputStats& stats) noexcept {
    if (value < -32768 || value > 32767) ++stats.dac_overload_samples;
    return static_cast<int16_t>(std::clamp<int64_t>(value, -32768, 32767));
}

double HardwareOutputStage::limit_mixer(double value,
                                        HardwareOutputStats& stats) noexcept {
    const double magnitude = std::abs(value);
    if (magnitude > kMixerRail) ++stats.mixer_overload_samples;
    if (magnitude <= kMixerKnee) return value;
    const double span = kMixerRail - kMixerKnee;
    const double limited = kMixerKnee + span * std::tanh((magnitude - kMixerKnee) / span);
    return std::copysign(limited, value);
}

double HardwareOutputStage::bessel_i0(double value) noexcept {
    const double squared_quarter = value * value * 0.25;
    double sum = 1.0;
    double term = 1.0;
    for (int index = 1; index <= 24; ++index) {
        term *= squared_quarter / double(index * index);
        sum += term;
        if (term < sum * 1.0e-16) break;
    }
    return sum;
}

void HardwareOutputStage::configure_capture_filter() {
    // The WAV/host capture is not part of the colouration. It only prevents
    // ultrasonic reconstruction residue from folding into the 44.1 kHz file.
    constexpr double beta = 5.65;
    constexpr double midpoint = double(kCaptureTaps - 1) * 0.5;
    const double cutoff_hz = std::min(20'000.0, host_rate_ * 0.45);
    const double cutoff = cutoff_hz / internal_rate_;
    const double denominator = bessel_i0(beta);
    double sum = 0.0;
    for (size_t index = 0; index < kCaptureTaps; ++index) {
        const double distance = double(index) - midpoint;
        const double ratio = distance / midpoint;
        const double window = bessel_i0(beta * std::sqrt(std::max(0.0, 1.0 - ratio * ratio))) /
                              denominator;
        const double sinc = distance == 0.0
                                ? 2.0 * cutoff
                                : std::sin(2.0 * kPi * cutoff * distance) /
                                      (kPi * distance);
        capture_coefficients_[index] = sinc * window;
        sum += capture_coefficients_[index];
    }
    for (double& coefficient : capture_coefficients_) coefficient /= sum;
}

void HardwareOutputStage::push_capture(size_t channel, double sample) noexcept {
    capture_history_[channel][capture_cursor_] = sample;
}

double HardwareOutputStage::capture(size_t channel) const noexcept {
    double result = 0.0;
    size_t history_index = (capture_cursor_ + kCaptureTaps - 1) % kCaptureTaps;
    for (size_t tap = 0; tap < kCaptureTaps; ++tap) {
        result += capture_coefficients_[tap] * capture_history_[channel][history_index];
        history_index = history_index == 0 ? kCaptureTaps - 1 : history_index - 1;
    }
    return result;
}

void HardwareOutputStage::process_subsample(const HardwareOutputInput& input,
                                             HardwareOutputStats& stats) {
    std::array<double, 2> fm{};
    std::array<double, 2> pcm{};
    std::array<double, 2> ssg{};
    std::array<int64_t, 2> routed_ssg{};
    for (size_t voice = 0; voice < input.ssg.size(); ++voice) {
        const int64_t signal = apply_gain(input.ssg[voice], input.gain_ssg);
        if (input.ssg_routes[voice] & 0x80) routed_ssg[0] += signal;
        if (input.ssg_routes[voice] & 0x40) routed_ssg[1] += signal;
    }

    for (size_t channel = 0; channel < 2; ++channel) {
        const int16_t fm_word = saturate_dac(apply_gain(input.fm[channel], input.gain_fm), stats);
        fm[channel] = quantize_floating_dac(fm_word);

        const int64_t pcm_bus = apply_gain(input.adpcm_a[channel], input.gain_a) +
                                apply_gain(input.adpcm_b[channel], input.gain_b);
        const int16_t pcm_word = saturate_dac(pcm_bus, stats);
        pcm[channel] = quantize_floating_dac(pcm_word);

        ssg[channel] = double(routed_ssg[channel]);

        const double summed = fm_coupling_[channel].process(fm[channel]) +
                              pcm_coupling_[channel].process(pcm[channel]) +
                              ssg_coupling_[channel].process(ssg[channel]);
        const double mastered = double(apply_gain(
            static_cast<int64_t>(std::llround(summed)), input.gain_master));
        const double limited = limit_mixer(mastered * kOutputGain, stats);
        push_capture(channel, reconstruction_[channel].process(limited));
    }
    capture_cursor_ = (capture_cursor_ + 1) % kCaptureTaps;
}

std::array<int16_t, 2> HardwareOutputStage::capture_frame(
    HardwareOutputStats& stats) const {
    std::array<int16_t, 2> output{};
    for (size_t channel = 0; channel < 2; ++channel) {
        const int64_t rounded = std::llround(capture(channel));
        if (rounded < -32768 || rounded > 32767) ++stats.capture_clipped_samples;
        output[channel] = static_cast<int16_t>(
            std::clamp<int64_t>(rounded, -32768, 32767));
    }
    return output;
}

} // namespace dms1
