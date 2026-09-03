#include "dms1_core.hpp"

#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <vector>

namespace {

void write_patch(dms1::RealtimeCore& core, uint8_t channel) {
    constexpr uint8_t control = 0x80 | 0x07; // centre, algorithm 8, feedback 0
    core.write_register(static_cast<uint16_t>(0x0030 + channel), 0x01);
    for (uint8_t group = 0; group < 4; ++group) {
        const uint8_t offset = static_cast<uint8_t>(channel + group * 8);
        core.write_register(static_cast<uint16_t>(0x0040 + offset), 0x01);
        core.write_register(static_cast<uint16_t>(0x0040 + offset), 0x80);
        core.write_register(static_cast<uint16_t>(0x0060 + offset), 0x18);
        core.write_register(static_cast<uint16_t>(0x0080 + offset), 0x1f);
        core.write_register(static_cast<uint16_t>(0x00a0 + offset), 0x08);
        core.write_register(static_cast<uint16_t>(0x00c0 + offset), 0x04);
        core.write_register(static_cast<uint16_t>(0x00c0 + offset), 0x22);
        core.write_register(static_cast<uint16_t>(0x00e0 + offset), 0x57);
    }
    core.write_register(static_cast<uint16_t>(0x0020 + channel), control);
}

void note_on(dms1::RealtimeCore& core, uint8_t channel) {
    constexpr uint8_t control = 0x80 | 0x07;
    core.write_register(static_cast<uint16_t>(0x0028 + channel), 0x3e); // MIDI C4
    core.write_register(static_cast<uint16_t>(0x0030 + channel), 0x01);
    core.write_register(static_cast<uint16_t>(0x0020 + channel), control | 0x40);
}

void write_tuning_patch(dms1::RealtimeCore& core, uint8_t channel) {
    constexpr uint8_t control = 0x80 | 0x07; // centre, four parallel carriers
    core.write_register(static_cast<uint16_t>(0x0030 + channel), 0x01);
    for (uint8_t group = 0; group < 4; ++group) {
        const uint8_t offset = static_cast<uint8_t>(channel + group * 8);
        core.write_register(static_cast<uint16_t>(0x0040 + offset), 0x01); // x1
        core.write_register(static_cast<uint16_t>(0x0040 + offset), 0x80); // sine, fine 0
        core.write_register(static_cast<uint16_t>(0x0060 + offset),
                            group == 0 ? 0x00 : 0x7f); // isolate one carrier
        core.write_register(static_cast<uint16_t>(0x0080 + offset), 0x1f); // instant attack
        core.write_register(static_cast<uint16_t>(0x00a0 + offset), 0x00); // hold level
        core.write_register(static_cast<uint16_t>(0x00c0 + offset), 0x00);
        core.write_register(static_cast<uint16_t>(0x00c0 + offset), 0x20);
        core.write_register(static_cast<uint16_t>(0x00e0 + offset), 0x0f);
    }
    core.write_register(static_cast<uint16_t>(0x0020 + channel), control);
}

bool has_signal(const std::vector<float>& samples) {
    return std::any_of(samples.begin(), samples.end(), [](float value) {
        return std::abs(value) > 1.0e-5f;
    });
}

int rising_edges(const std::vector<float>& samples) {
    const float peak = *std::max_element(samples.begin(), samples.end());
    const float threshold = peak * 0.5f;
    int edges = 0;
    for (std::size_t index = 1; index < samples.size(); ++index) {
        if (samples[index - 1] <= threshold && samples[index] > threshold) ++edges;
    }
    return edges;
}

int positive_zero_crossings(const std::vector<float>& samples, std::size_t start = 0) {
    int edges = 0;
    for (std::size_t index = std::max<std::size_t>(1, start); index < samples.size(); ++index) {
        if (samples[index - 1] <= 0.0f && samples[index] > 0.0f) ++edges;
    }
    return edges;
}

} // namespace

int main() {
    static_assert(static_cast<uint8_t>(dms1::HardwarePan::Left) == 0x80);
    static_assert(static_cast<uint8_t>(dms1::HardwarePan::Right) == 0x40);
    static_assert(static_cast<uint8_t>(dms1::HardwarePan::Center) == 0xc0);
    assert(dms1::HardwareOutputStage::quantize_floating_dac(0) == 0);
    assert(dms1::HardwareOutputStage::quantize_floating_dac(511) == 511);
    assert(dms1::HardwareOutputStage::quantize_floating_dac(-1) == -1);
    assert(dms1::HardwareOutputStage::quantize_floating_dac(32767) == 32704);
    assert(dms1::HardwareOutputStage::quantize_floating_dac(-32767) == -32768);
    // OPZ/ymfm requires register 0x08 to select the channel before a
    // 0x20+channel key transition. DMS hides register 0x08 from DMR and
    // performs that selection internally. Every public FM voice must therefore
    // key on independently from reset; this catches the historical FM1-only
    // DMR player failure.
    for (uint8_t channel = 0; channel < 4; ++channel) {
        dms1::RealtimeCore fm_channel_core(48'000.0);
        fm_channel_core.write_register(0x0188, 0x00); // FM unity
        fm_channel_core.write_register(0x0189, 0x80); // SSG mute
        fm_channel_core.write_register(0x018a, 0x80); // ADPCM-A mute
        fm_channel_core.write_register(0x018b, 0x80); // ADPCM-B mute
        write_patch(fm_channel_core, channel);
        note_on(fm_channel_core, channel);
        std::vector<float> channel_left(4096), channel_right(4096);
        fm_channel_core.render(channel_left.data(), channel_right.data(), channel_left.size());
        assert(has_signal(channel_left));
        assert(has_signal(channel_right));
    }

    dms1::HardwareOutputStage stressed_output(44'100.0);
    dms1::HardwareOutputInput stressed_input;
    stressed_input.fm.fill(32767);
    stressed_input.ssg.fill(16382);
    stressed_input.adpcm_a.fill(32767);
    stressed_input.adpcm_b.fill(32767);
    stressed_input.gain_fm = stressed_input.gain_ssg = 0;
    stressed_input.gain_a = stressed_input.gain_b = 0;
    stressed_input.gain_master = 0;
    dms1::HardwareOutputStats stressed_stats;
    for (uint32_t phase = 0; phase < dms1::HardwareOutputStage::kOversampleFactor;
         ++phase) {
        stressed_output.process_subsample(stressed_input, stressed_stats);
    }
    (void)stressed_output.capture_frame(stressed_stats);
    assert(stressed_stats.dac_overload_samples > 0);
    assert(stressed_stats.mixer_overload_samples > 0);

    dms1::RealtimeCore one_block(48'000.0);
    dms1::RealtimeCore split_blocks(48'000.0);
    for (auto* core : {&one_block, &split_blocks}) {
        write_patch(*core, 0);
        note_on(*core, 0);
    }

    std::vector<float> left_a(4096), right_a(4096);
    std::vector<float> left_b(4096), right_b(4096);
    one_block.render(left_a.data(), right_a.data(), left_a.size());

    constexpr std::array<std::size_t, 7> chunks = {1, 31, 64, 257, 13, 1024, 2706};
    std::size_t cursor = 0;
    for (const auto chunk : chunks) {
        split_blocks.render(left_b.data() + cursor, right_b.data() + cursor, chunk);
        cursor += chunk;
    }
    assert(cursor == left_b.size());
    assert(left_a == left_b);
    assert(right_a == right_b);
    assert(has_signal(left_a));
    assert(has_signal(right_a));
    assert(one_block.current_cycle() == 2'048'000);
    assert(one_block.clipped_samples() == 0);

    dms1::RealtimeCore hardware_one(
        48'000.0, dms1::OutputStage::Hardware);
    dms1::RealtimeCore hardware_split(
        48'000.0, dms1::OutputStage::Hardware);
    for (auto* core : {&hardware_one, &hardware_split}) {
        write_patch(*core, 0);
        note_on(*core, 0);
    }
    std::vector<float> hardware_left_a(4096), hardware_right_a(4096);
    std::vector<float> hardware_left_b(4096), hardware_right_b(4096);
    hardware_one.render(
        hardware_left_a.data(), hardware_right_a.data(), hardware_left_a.size());
    cursor = 0;
    for (const auto chunk : chunks) {
        hardware_split.render(hardware_left_b.data() + cursor,
                              hardware_right_b.data() + cursor, chunk);
        cursor += chunk;
    }
    assert(hardware_left_a == hardware_left_b);
    assert(hardware_right_a == hardware_right_b);
    assert(has_signal(hardware_left_a));
    assert(has_signal(hardware_right_a));
    assert(hardware_left_a != left_a);
    assert(hardware_one.output_stage() == dms1::OutputStage::Hardware);
    assert(hardware_one.current_cycle() == one_block.current_cycle());
    assert(hardware_one.clipped_samples() == 0);
    assert(hardware_one.dac_overload_samples() == 0);
    assert(hardware_one.mixer_overload_samples() == 0);

    dms1::RealtimeCore rate_44100(44'100.0);
    std::vector<float> second_left(44'100), second_right(44'100);
    rate_44100.render(second_left.data(), second_right.data(), second_left.size());
    assert(std::llabs(static_cast<long long>(rate_44100.current_cycle()) - 24'000'000LL) <= 1);

    rate_44100.set_output_rate(96'000.0);
    rate_44100.reset();
    std::vector<float> half_left(96), half_right(96);
    rate_44100.render(half_left.data(), half_right.data(), half_left.size());
    assert(rate_44100.current_cycle() == 24'000);

    dms1::RealtimeCore sample_core(48'000.0);
    sample_core.set_sample_memory(std::vector<uint8_t>(256, 0x11));
    assert(sample_core.sample_memory_size() == 256);
    sample_core.write_register(0x0188, 0x80);
    sample_core.write_register(0x0189, 0x80);
    sample_core.write_register(0x018a, 0x00);
    sample_core.write_register(0x018b, 0x80);
    sample_core.write_register(0x018c, 0x00);
    sample_core.write_register(0x0121, 0xc0);
    sample_core.write_register(0x0122, 0x00);
    sample_core.write_register(0x0124, 0x00);
    sample_core.write_register(0x0125, 0x00);
    sample_core.write_register(0x0126, 0x00);
    sample_core.write_register(0x0127, 0x00);
    sample_core.write_register(0x0120, 0x01);
    std::vector<float> sample_left(1600), sample_right(1600);
    sample_core.render(sample_left.data(), sample_right.data(), sample_left.size());
    assert(has_signal(sample_left));
    assert(has_signal(sample_right));

    dms1::RealtimeCore ssg_core(48'000.0);
    ssg_core.write_register(0x0188, 0x80); // FM mute
    ssg_core.write_register(0x0189, 0x00); // SSG unity
    ssg_core.write_register(0x018a, 0x80); // A mute
    ssg_core.write_register(0x018b, 0x80); // B mute
    ssg_core.write_register(0x018c, 0x00);
    constexpr uint16_t c4Period = 478; // 2 MHz / (16 * 261.626 Hz)
    ssg_core.write_register(0x0100, static_cast<uint8_t>(c4Period));
    ssg_core.write_register(0x0101, static_cast<uint8_t>(c4Period >> 8));
    ssg_core.write_register(0x0107, 0x3e); // tone A only
    ssg_core.write_register(0x0108, 0x0f);
    std::vector<float> ssg_left(48'000), ssg_right(48'000);
    ssg_core.render(ssg_left.data(), ssg_right.data(), ssg_left.size());
    const int c4Edges = rising_edges(ssg_left);
    assert(c4Edges >= 260 && c4Edges <= 263);

    dms1::RealtimeCore fm_pitch_core(48'000.0);
    fm_pitch_core.write_register(0x0188, 0x00);
    fm_pitch_core.write_register(0x0189, 0x80);
    fm_pitch_core.write_register(0x018a, 0x80);
    fm_pitch_core.write_register(0x018b, 0x80);
    fm_pitch_core.write_register(0x018c, 0x00);
    write_tuning_patch(fm_pitch_core, 0);
    note_on(fm_pitch_core, 0);
    std::vector<float> fm_pitch_left(96'000), fm_pitch_right(96'000);
    fm_pitch_core.render(fm_pitch_left.data(), fm_pitch_right.data(), fm_pitch_left.size());
    constexpr std::size_t tuningStart = 4'800;
    const int fmC4Edges = positive_zero_crossings(fm_pitch_left, tuningStart);
    const double fmC4Hz = fmC4Edges * 48'000.0
        / static_cast<double>(fm_pitch_left.size() - tuningStart);
    assert(std::abs(fmC4Hz - 261.626) < 1.0);

    std::cout << "OK: DMS-1 realtime core, RAW/HARDWARE block invariant, "
                 "floating DAC, live sample bus, 44.1/48/96 kHz scheduler, "
                 "FM/SSG C4 tuning\n";
    return 0;
}
