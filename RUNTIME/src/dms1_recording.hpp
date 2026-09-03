#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace dms1 {

constexpr uint32_t kDms1SystemClock = 24'000'000;

enum class RecordedEventKind : uint8_t {
    RegisterWrite,
    PlayAdpcmA,
    StopAdpcmA,
    PlayAdpcmB,
    StopAdpcmB,
};

// A recorder event is deliberately hardware-facing. It stores either one
// effective MMIO write or one cartridge-aware ADPCM command at an exact
// NATIVE89 master-clock cycle.
struct RecordedEvent {
    uint64_t cycle = 0;
    RecordedEventKind kind = RecordedEventKind::RegisterWrite;
    uint16_t address_or_sample = 0;
    uint16_t argument16 = 0;
    uint8_t value = 0;
    uint8_t level = 0;
    uint8_t pan = 0xc0;
    uint8_t flags = 0;

    static RecordedEvent register_write(uint64_t cycle, uint16_t address,
                                        uint8_t data) noexcept;
    static RecordedEvent play_a(uint64_t cycle, uint16_t sample_id,
                                uint8_t level, uint8_t pan) noexcept;
    static RecordedEvent stop_a(uint64_t cycle) noexcept;
    static RecordedEvent play_b(uint64_t cycle, uint16_t sample_id,
                                uint16_t delta_n, uint8_t level,
                                uint8_t pan, uint8_t flags) noexcept;
    static RecordedEvent stop_b(uint64_t cycle) noexcept;
};

struct RecordedSampleResource {
    uint16_t sample_id = 0;
    uint8_t codec = 0; // 1 = ADPCM-A, 2 = ADPCM-B
    std::string name;
    std::vector<uint8_t> data;
    uint32_t source_rate = 0;
    uint8_t level = 0;
    uint8_t pan = 0xc0;
    uint8_t root_note = 60;
    int8_t fine_cents = 0;
};

struct RecordedRomOptions {
    std::string title = "DMS-1 Ableton Capture";
    std::string author = "DAC MASTER";
    uint64_t capture_cycles = 0;
    uint64_t tail_cycles = kDms1SystemClock / 4;
};

// Compiles an exact write stream and its encoded cartridge samples into a
// genuine DMR 0.1 ROM. Throws std::runtime_error/std::invalid_argument when
// the capture cannot be represented within the V0.1 limits.
std::vector<uint8_t> build_recorded_dmr(
    std::vector<RecordedEvent> events,
    const std::vector<RecordedSampleResource>& resources,
    const RecordedRomOptions& options = {});

} // namespace dms1
