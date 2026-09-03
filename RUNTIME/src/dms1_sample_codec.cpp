#include "dms1_sample_codec.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace dms1 {
namespace {

constexpr long double kPi = 3.141592653589793238462643383279502884L;

long long round_half_even(long double value) {
    const long double lower_value = std::floor(value);
    const long double fraction = value - lower_value;
    const auto lower = static_cast<long long>(lower_value);
    if (fraction < 0.5L) return lower;
    if (fraction > 0.5L) return lower + 1;
    return (lower & 1LL) == 0 ? lower : lower + 1;
}

int signed_12(int value) {
    value &= 0x0fff;
    return (value & 0x0800) != 0 ? value - 0x1000 : value;
}

std::vector<int16_t> padded_pcm(const std::vector<int16_t>& source) {
    std::vector<int16_t> result = source;
    const std::size_t padding =
        (kSamplePageDecodedSamples - result.size() % kSamplePageDecodedSamples)
        % kSamplePageDecodedSamples;
    result.resize(result.size() + padding, 0);
    return result;
}

std::vector<uint8_t> pack_nibbles(const std::vector<uint8_t>& nibbles) {
    if ((nibbles.size() & 1U) != 0) {
        throw std::runtime_error("nombre impair de nibbles ADPCM");
    }
    std::vector<uint8_t> encoded(nibbles.size() / 2);
    for (std::size_t index = 0; index < nibbles.size(); index += 2) {
        encoded[index / 2] = static_cast<uint8_t>((nibbles[index] << 4)
                                                   | nibbles[index + 1]);
    }
    if (encoded.size() % kSamplePageBytes != 0) {
        throw std::runtime_error("encodeur ADPCM hors pages de 256 octets");
    }
    return encoded;
}

} // namespace

std::vector<int16_t> resample_windowed_sinc(const std::vector<int16_t>& source,
                                             double source_rate,
                                             double target_rate,
                                             int radius) {
    if (source.empty()) return {};
    if (!std::isfinite(source_rate) || !std::isfinite(target_rate)
        || source_rate <= 0.0 || target_rate <= 0.0 || radius < 2) {
        throw std::invalid_argument("parametres de reechantillonnage invalides");
    }

    const long double exact_count = static_cast<long double>(source.size())
                                  * static_cast<long double>(target_rate)
                                  / static_cast<long double>(source_rate);
    const auto rounded_count = round_half_even(exact_count);
    const std::size_t target_count = static_cast<std::size_t>(std::max(1LL, rounded_count));
    const long double ratio = static_cast<long double>(target_rate)
                            / static_cast<long double>(source_rate);
    const long double cutoff = 0.5L * std::min(1.0L, ratio) * 0.94L;
    const long double source_step = static_cast<long double>(source_rate)
                                  / static_cast<long double>(target_rate);

    std::vector<int16_t> output;
    output.reserve(target_count);
    for (std::size_t output_index = 0; output_index < target_count; ++output_index) {
        const long double position = static_cast<long double>(output_index) * source_step;
        const auto center = static_cast<long long>(std::floor(position));
        const auto first = std::max(0LL, center - radius + 1LL);
        const auto last = std::min(static_cast<long long>(source.size()) - 1,
                                   center + radius);
        long double weighted = 0.0L;
        long double weight_sum = 0.0L;
        for (long long source_index = first; source_index <= last; ++source_index) {
            const long double distance = static_cast<long double>(source_index) - position;
            if (std::abs(distance) >= static_cast<long double>(radius)) continue;
            const long double argument = 2.0L * cutoff * distance;
            const long double sinc = argument == 0.0L
                ? 1.0L : std::sin(kPi * argument) / (kPi * argument);
            const long double window = 0.5L
                + 0.5L * std::cos(kPi * distance / static_cast<long double>(radius));
            const long double weight = 2.0L * cutoff * sinc * window;
            weighted += static_cast<long double>(source[static_cast<std::size_t>(source_index)])
                      * weight;
            weight_sum += weight;
        }
        const long long value = weight_sum == 0.0L
            ? 0LL : round_half_even(weighted / weight_sum);
        output.push_back(static_cast<int16_t>(std::clamp<long long>(value, -32768, 32767)));
    }
    return output;
}

std::vector<uint8_t> encode_adpcm_a(const std::vector<int16_t>& pcm) {
    static constexpr std::array<int, 49> steps = {
        16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73,
        80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279,
        307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
        1060, 1166, 1282, 1411, 1552};
    static constexpr std::array<int, 8> index_adjust = {-1, -1, -1, -1, 2, 5, 7, 9};

    const auto source = padded_pcm(pcm);
    std::vector<uint8_t> nibbles;
    nibbles.reserve(source.size());
    int accumulator = 0;
    int step_index = 0;
    for (const int pcm_value : source) {
        const int target = std::clamp<int>(
            static_cast<int>(round_half_even(static_cast<long double>(pcm_value) / 16.0L)),
            -2048, 2047);
        const int step = steps[static_cast<std::size_t>(step_index)];
        int best_nibble = 0;
        int best_accumulator = accumulator;
        int best_error = std::numeric_limits<int>::max();
        for (int nibble = 0; nibble < 16; ++nibble) {
            int delta = (2 * (nibble & 7) + 1) * step / 8;
            if ((nibble & 8) != 0) delta = -delta;
            const int candidate = (accumulator + delta) & 0x0fff;
            const int error = std::abs(target - signed_12(candidate));
            if (error < best_error) {
                best_error = error;
                best_nibble = nibble;
                best_accumulator = candidate;
            }
        }
        nibbles.push_back(static_cast<uint8_t>(best_nibble));
        accumulator = best_accumulator;
        step_index = std::clamp(step_index + index_adjust[static_cast<std::size_t>(best_nibble & 7)],
                                0, 48);
    }
    return pack_nibbles(nibbles);
}

std::vector<uint8_t> encode_adpcm_b(const std::vector<int16_t>& pcm) {
    static constexpr std::array<int, 8> scales = {57, 57, 57, 57, 77, 102, 128, 153};
    const auto source = padded_pcm(pcm);
    std::vector<uint8_t> nibbles;
    nibbles.reserve(source.size());
    int accumulator = 0;
    int step = 127;
    for (const int target : source) {
        int best_nibble = 0;
        int best_value = accumulator;
        int best_error = std::numeric_limits<int>::max();
        for (int nibble = 0; nibble < 16; ++nibble) {
            int delta = (2 * (nibble & 7) + 1) * step / 8;
            if ((nibble & 8) != 0) delta = -delta;
            const int candidate = std::clamp(accumulator + delta, -32768, 32767);
            const int error = std::abs(target - candidate);
            if (error < best_error) {
                best_error = error;
                best_nibble = nibble;
                best_value = candidate;
            }
        }
        nibbles.push_back(static_cast<uint8_t>(best_nibble));
        accumulator = best_value;
        step = std::clamp(step * scales[static_cast<std::size_t>(best_nibble & 7)] / 64,
                          127, 24576);
    }
    return pack_nibbles(nibbles);
}

} // namespace dms1
