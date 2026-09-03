#include "dms1_core.hpp"
#include "dms1_recording.hpp"

#include <algorithm>
#include <array>
#include <cassert>
#include <cstdlib>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <vector>

namespace {

struct ObservedWrite {
    uint64_t cycle = 0;
    uint16_t address = 0;
    uint8_t value = 0;
};

void observe_write(void* context, uint64_t cycle, uint16_t address,
                   uint8_t value) noexcept {
    auto* writes = static_cast<std::vector<ObservedWrite>*>(context);
    if (writes->size() < writes->capacity()) writes->push_back({cycle, address, value});
}

uint16_t be16(const std::vector<uint8_t>& data, size_t offset) {
    return static_cast<uint16_t>((uint16_t(data.at(offset)) << 8) | data.at(offset + 1));
}

uint32_t be32(const std::vector<uint8_t>& data, size_t offset) {
    return (uint32_t(data.at(offset)) << 24) | (uint32_t(data.at(offset + 1)) << 16)
         | (uint32_t(data.at(offset + 2)) << 8) | uint32_t(data.at(offset + 3));
}

} // namespace

int main() {
    std::vector<ObservedWrite> writes;
    writes.reserve(4);
    dms1::RealtimeCore tapped(48'000.0);
    tapped.set_register_write_observer(observe_write, &writes);
    tapped.write_register(0x0188, 0x12);
    std::array<float, 32> left {}, right {};
    tapped.render(left.data(), right.data(), left.size());
    tapped.write_register(0x0107, 0x3f);
    assert(writes.size() == 2);
    assert(writes[0].cycle == 0 && writes[0].address == 0x0188 && writes[0].value == 0x12);
    assert(writes[1].cycle == 16'000 && writes[1].address == 0x0107);

    std::vector<dms1::RecordedEvent> events;
    events.push_back(dms1::RecordedEvent::register_write(0, 0x0188, 0x80));
    events.push_back(dms1::RecordedEvent::register_write(0, 0x0189, 0x80));
    events.push_back(dms1::RecordedEvent::register_write(0, 0x018a, 0x00));
    events.push_back(dms1::RecordedEvent::register_write(0, 0x018b, 0x00));
    events.push_back(dms1::RecordedEvent::register_write(0, 0x018c, 0x00));
    events.push_back(dms1::RecordedEvent::play_a(0, 1, 0, 0x80));
    events.push_back(dms1::RecordedEvent::stop_a(480'000));
    events.push_back(dms1::RecordedEvent::play_b(480'000, 17, 0x77cf, 224, 0xc0, 0));
    events.push_back(dms1::RecordedEvent::stop_b(1'200'000));

    std::vector<dms1::RecordedSampleResource> resources;
    resources.push_back({1, 1, "A test", std::vector<uint8_t>(256, 0x11),
                         18'519, 0, 0x80, 36, 0});
    resources.push_back({17, 2, "B test", std::vector<uint8_t>(256, 0x11),
                         26'000, 224, 0xc0, 60, 0});
    dms1::RecordedRomOptions options;
    options.capture_cycles = 1'440'000;
    options.tail_cycles = 240'000;
    const auto rom = dms1::build_recorded_dmr(events, resources, options);
    assert(rom.size() > 512);
    assert(std::equal(rom.begin(), rom.begin() + 4,
                      std::array<uint8_t, 4>{'D', 'M', 'R', '0'}.begin()));
    assert(be16(rom, 0x06) == 1);
    assert(be32(rom, 0x0c) == rom.size());
    assert(be32(rom, 0x24) == dms1::kDms1SystemClock);
    assert(be16(rom, 0x1c) == 4);

    const char* temporary_path = "dms1_recording_rom_test.dmr";
    {
        std::ofstream output(temporary_path, std::ios::binary);
        assert(output.good());
        output.write(reinterpret_cast<const char*>(rom.data()),
                     static_cast<std::streamsize>(rom.size()));
        assert(output.good());
    }
    dms1::RenderConfig render_config;
    render_config.tail_ms = 0;
    render_config.max_seconds = 2;
    const auto rendered = dms1::render_rom_to_pcm(temporary_path, render_config);
    std::remove(temporary_path);
    assert(rendered.report.frames > 3'000);
    assert(rendered.report.peak > 0);
    int left_peak = 0;
    int right_peak = 0;
    const size_t pan_probe_frames = rendered.report.output_rate * 19 / 1000;
    for (size_t frame = 0;
         frame < std::min<size_t>(pan_probe_frames, rendered.report.frames); ++frame) {
        left_peak = std::max(left_peak,
                             std::abs(int(rendered.interleaved_stereo[frame * 2])));
        right_peak = std::max(right_peak,
                              std::abs(int(rendered.interleaved_stereo[frame * 2 + 1])));
    }
    assert(left_peak > 0 && right_peak == 0);

    std::cout << "OK: exact register tap, genuine DMR, and per-play sample pan\n";
    return 0;
}
