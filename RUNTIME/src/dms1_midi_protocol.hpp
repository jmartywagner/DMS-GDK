#pragma once

#include <array>

namespace dms1 {

constexpr int kFmPatchSlots = 8;
constexpr int kFmPatchSelectController = 110;

enum class DriverFxCommand {
    None,
    VibratoSpeed,
    VibratoDepth,
    TremoloSpeed,
    TremoloDepth,
    PitchSlide,
    Portamento,
    Arpeggio,
    VolumeSlide,
    Retrigger,
    NoteCut,
    NoteDelay
};

// DHR-style live driver controls. CC110 remains exclusively the FM slot
// switch, so these commands can be recorded verbatim by the future DMS ROM
// recorder. All values are neutral at zero, except signed slides whose centre
// is MIDI value 64.
constexpr DriverFxCommand driver_fx_command_from_controller(int controller) noexcept {
    switch (controller) {
    case 102: return DriverFxCommand::VibratoSpeed;
    case 103: return DriverFxCommand::VibratoDepth;
    case 104: return DriverFxCommand::TremoloSpeed;
    case 105: return DriverFxCommand::TremoloDepth;
    case 106: return DriverFxCommand::PitchSlide;
    case 107: return DriverFxCommand::Portamento;
    case 108: return DriverFxCommand::Arpeggio;
    case 109: return DriverFxCommand::VolumeSlide;
    case 111: return DriverFxCommand::Retrigger;
    case 112: return DriverFxCommand::NoteCut;
    case 113: return DriverFxCommand::NoteDelay;
    default: return DriverFxCommand::None;
    }
}

// DAC MASTER convention shared by the VST recorder and the future ROM driver.
// Values above 79 stay reserved so the protocol can grow without changing the
// eight musical slots already written into projects.
constexpr int fm_patch_slot_from_cc110(int value) noexcept {
    return value >= 0 && value <= 79 ? value / 10 : -1;
}

// CC111 uses 0 as OFF. Every other MIDI value must remain audible/useful:
// even CC=1 maps to one 60 Hz driver tick instead of being rounded back to 0.
constexpr int retrigger_ticks_from_cc111(int value) noexcept {
    if (value <= 0) return 0;
    if (value >= 127) return 31;
    return 1 + ((value - 1) * 30 + 62) / 126;
}

// A physical DMS-1 voice is monophonic and has no hidden note stack. A late
// note-off for the note that was replaced must not silence or resurrect the
// currently sounding note.
constexpr bool strict_mono_note_off_matches(int active_note,
                                             int released_note) noexcept {
    return active_note >= 0 && active_note == released_note;
}

// The OPM/OPZ note nibble starts at C# and ends at C. This is intentionally
// not the more familiar C-first table used by many MIDI helpers.
constexpr unsigned char opz_keycode_from_midi(int note) noexcept {
    constexpr std::array<int, 12> codes = {14, 0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13};
    note = note < 13 ? 13 : (note > 108 ? 108 : note);
    const int pitchClass = note % 12;
    int block = note / 12 - 1;
    if (pitchClass == 0) --block;
    return static_cast<unsigned char>((block << 4) | codes[static_cast<std::size_t>(pitchClass)]);
}

} // namespace dms1
