#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace dms1 {

struct Tx81zImportedPatch {
    std::string name;
    int algorithm = 7;
    int feedback = 0;
    std::array<int, 4> multiples {};
    std::array<int, 4> fine {};
    std::array<int, 4> waves {};
    std::array<int, 4> levels {};
    std::array<int, 4> attacks {};
    std::array<int, 4> decays {};
    std::array<int, 4> sustains {};
    std::array<int, 4> sustain_levels {};
    std::array<int, 4> releases {};
    std::array<int, 4> reverb_rates {};
    std::array<int, 4> detunes {};
    std::array<int, 4> key_scale_rates {};
    std::array<int, 4> fixed_modes {};
    std::array<int, 4> fixed_ranges {};
    std::array<int, 4> fixed_frequencies {};
    std::array<int, 4> am_enables {};
    std::array<int, 4> detune2 {};
    std::array<int, 4> eg_shifts {};
    std::array<int, 4> velocity_sensitivities {};
    int transpose = 24;
    int lfo_speed = 0;
    int lfo_pitch_depth = 0;
    int lfo_amplitude_depth = 0;
    int lfo_pitch_sensitivity = 0;
    int lfo_amplitude_sensitivity = 0;
    int lfo_waveform = 0;
    int lfo_sync = 0;
};

// Parses one native Yamaha TX81Z 32-voice VMEM bulk dump (4104 bytes),
// validates its Yamaha checksum, and maps all sound-producing OPZ fields.
std::vector<Tx81zImportedPatch> import_tx81z_vmem(
    const std::vector<uint8_t>& sysex);

} // namespace dms1
