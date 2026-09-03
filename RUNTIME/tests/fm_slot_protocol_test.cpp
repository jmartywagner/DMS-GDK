#include "dms1_midi_protocol.hpp"

#include <cstdlib>
#include <iostream>

int main() {
    if (dms1::fm_patch_slot_from_cc110(-1) != -1) return EXIT_FAILURE;
    for (int slot = 0; slot < dms1::kFmPatchSlots; ++slot) {
        for (int value = slot * 10; value <= slot * 10 + 9; ++value) {
            if (dms1::fm_patch_slot_from_cc110(value) != slot) return EXIT_FAILURE;
        }
    }
    for (int value = 80; value <= 127; ++value) {
        if (dms1::fm_patch_slot_from_cc110(value) != -1) return EXIT_FAILURE;
    }
    for (int controller = 0; controller <= 127; ++controller) {
        const bool expected = (controller >= 102 && controller <= 109)
            || (controller >= 111 && controller <= 113);
        const bool mapped = dms1::driver_fx_command_from_controller(controller)
            != dms1::DriverFxCommand::None;
        if (mapped != expected) return EXIT_FAILURE;
    }
    if (dms1::retrigger_ticks_from_cc111(0) != 0) return EXIT_FAILURE;
    if (dms1::retrigger_ticks_from_cc111(1) != 1) return EXIT_FAILURE;
    if (dms1::retrigger_ticks_from_cc111(127) != 31) return EXIT_FAILURE;
    if (dms1::strict_mono_note_off_matches(62, 60)) return EXIT_FAILURE;
    if (!dms1::strict_mono_note_off_matches(62, 62)) return EXIT_FAILURE;
    if (dms1::opz_keycode_from_midi(60) != 0x3e) return EXIT_FAILURE; // C4
    if (dms1::opz_keycode_from_midi(61) != 0x40) return EXIT_FAILURE; // C#4
    if (dms1::opz_keycode_from_midi(69) != 0x4a) return EXIT_FAILURE; // A4
    if (dms1::opz_keycode_from_midi(72) != 0x4e) return EXIT_FAILURE; // C5
    std::cout << "OK: CC110 slots; CC102-109/111-113 FX; retrigger has no dead zone\n";
    return EXIT_SUCCESS;
}
