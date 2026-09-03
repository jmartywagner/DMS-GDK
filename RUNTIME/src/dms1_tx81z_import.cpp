#include "dms1_tx81z_import.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace dms1 {
namespace {

constexpr size_t kMessageBytes = 4104;
constexpr size_t kPayloadBytes = 4096;
constexpr size_t kVoiceBytes = 128;
constexpr size_t kVoiceCount = 32;
constexpr std::array<int, 20> kLowOutputLevels = {
    0, 5, 9, 13, 17, 20, 23, 25, 27, 29,
    31, 33, 35, 37, 39, 41, 42, 44, 46, 47};
constexpr std::array<int, 7> kDetuneRegisters = {7, 6, 5, 0, 1, 2, 3};

void require_range(int value, int maximum, const char* field) {
    if (value < 0 || value > maximum)
        throw std::invalid_argument(std::string("champ TX81Z invalide: ") + field);
}

int output_to_total_level(int output) {
    require_range(output, 99, "operator output");
    const int linear = output < 20 ? kLowOutputLevels[static_cast<size_t>(output)]
                                   : output + 28;
    return 127 - linear;
}

double opz_lfo_frequency(int reg) {
    const int mantissa = 0x10 | (reg & 0x0f);
    const int increment = mantissa << (reg >> 4);
    return (3'579'545.0 / 64.0) * increment / 1'073'741'824.0;
}

int lfo_speed_to_opz(int speed) {
    require_range(speed, 99, "LFO speed");
    if (speed == 0) return 0;
    const double target = speed <= 63
        ? 0.007 + (10.1 - 0.007) * (speed - 1) / 62.0
        : 10.1 + (50.0 - 10.1) * (speed - 63) / 36.0;
    int best = 0;
    double distance = std::abs(opz_lfo_frequency(0) - target);
    for (int reg = 1; reg < 256; ++reg) {
        const double candidate = std::abs(opz_lfo_frequency(reg) - target);
        if (candidate < distance) {
            distance = candidate;
            best = reg;
        }
    }
    return best;
}

std::string decode_name(const uint8_t* raw, int index) {
    std::string name;
    name.reserve(10);
    for (int cursor = 0; cursor < 10; ++cursor) {
        const uint8_t character = raw[57 + cursor];
        if (character < 32 || character > 126)
            throw std::invalid_argument("nom TX81Z non ASCII");
        name.push_back(static_cast<char>(character));
    }
    while (!name.empty() && name.back() == ' ') name.pop_back();
    if (name.empty()) name = "Voice " + std::to_string(index + 1);
    return name;
}

Tx81zImportedPatch decode_voice(const uint8_t* raw, int index) {
    Tx81zImportedPatch patch;
    patch.name = decode_name(raw, index);
    patch.algorithm = raw[40] & 7;
    patch.feedback = (raw[40] >> 3) & 7;
    patch.lfo_sync = (raw[40] >> 6) & 1;
    require_range(raw[41], 99, "LFO speed");
    require_range(raw[43], 99, "pitch modulation depth");
    require_range(raw[44], 99, "amplitude modulation depth");
    require_range(raw[46], 48, "transpose");
    patch.lfo_speed = lfo_speed_to_opz(raw[41]);
    patch.lfo_pitch_depth = std::lround(raw[43] * 127.0 / 99.0);
    patch.lfo_amplitude_depth = std::lround(raw[44] * 127.0 / 99.0);
    patch.lfo_pitch_sensitivity = (raw[45] >> 4) & 7;
    patch.lfo_amplitude_sensitivity = (raw[45] >> 2) & 3;
    patch.lfo_waveform = raw[45] & 3;
    patch.transpose = raw[46];
    const int reverb = raw[81] & 7;

    for (int op = 0; op < 4; ++op) {
        const uint8_t* base = raw + op * 10;
        const int detune = base[9] & 7;
        require_range(base[5], 99, "level scaling");
        require_range(base[7], 99, "operator output");
        require_range(detune, 6, "operator detune");
        patch.attacks[op] = base[0] & 0x1f;
        patch.decays[op] = base[1] & 0x1f;
        patch.sustains[op] = base[2] & 0x1f;
        patch.releases[op] = base[3] & 0x0f;
        patch.sustain_levels[op] = base[4] & 0x0f;
        patch.am_enables[op] = (base[6] >> 6) & 1;
        patch.velocity_sensitivities[op] = base[6] & 7;
        patch.levels[op] = output_to_total_level(base[7]);
        patch.multiples[op] = base[8] & 0x0f;
        patch.fixed_frequencies[op] = patch.multiples[op];
        patch.detune2[op] = (base[8] >> 4) & 3;
        patch.key_scale_rates[op] = (base[9] >> 3) & 3;
        patch.detunes[op] = kDetuneRegisters[static_cast<size_t>(detune)];
        const uint8_t additional_a = raw[73 + op * 2];
        const uint8_t additional_b = raw[74 + op * 2];
        patch.eg_shifts[op] = (additional_a >> 4) & 3;
        patch.fixed_modes[op] = (additional_a >> 3) & 1;
        patch.fixed_ranges[op] = additional_a & 7;
        patch.waves[op] = (additional_b >> 4) & 7;
        patch.fine[op] = additional_b & 0x0f;
        patch.reverb_rates[op] = reverb;
    }
    return patch;
}

} // namespace

std::vector<Tx81zImportedPatch> import_tx81z_vmem(
    const std::vector<uint8_t>& sysex) {
    if (sysex.size() != kMessageBytes)
        throw std::invalid_argument(
            "format non pris en charge: il faut un bulk TX81Z VMEM de 32 voix (4104 octets)");
    if (sysex[0] != 0xf0 || sysex[1] != 0x43 || sysex[2] > 0x0f
        || sysex[3] != 0x04 || sysex[4] != 0x20 || sysex[5] != 0x00
        || sysex.back() != 0xf7) {
        throw std::invalid_argument("entete Yamaha TX81Z VMEM invalide");
    }
    unsigned checksum = sysex[kMessageBytes - 2];
    for (size_t index = 0; index < kPayloadBytes; ++index) {
        const uint8_t byte = sysex[6 + index];
        if ((byte & 0x80) != 0) throw std::invalid_argument("donnee SysEx superieure a 7 bits");
        checksum += byte;
    }
    if ((checksum & 0x7f) != 0)
        throw std::invalid_argument("checksum Yamaha TX81Z invalide; import refuse");

    std::vector<Tx81zImportedPatch> patches;
    patches.reserve(kVoiceCount);
    for (size_t voice = 0; voice < kVoiceCount; ++voice)
        patches.push_back(decode_voice(sysex.data() + 6 + voice * kVoiceBytes,
                                       static_cast<int>(voice)));
    return patches;
}

} // namespace dms1
