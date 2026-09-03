#include <stdint.h>
#include "dms1_hw.h"
#include "dms_system.h"
#include "dms_audio.h"

#define VDP_STATUS (*(volatile uint8_t*)(DMS_VDP_BASE + 0x00))
extern void dms_sprite_runtime_init(void);
extern void dms_pad_runtime_init(void);
extern void dms_collision_runtime_init(void);
extern void dms_actor_runtime_init(void);
extern void dms_gameplay_runtime_init(void);

void SYS_init(void) {
    volatile uint8_t *mail = (volatile uint8_t*)DMS_MAILBOX_BASE;
    mail[0] = 0;
    dms_pad_runtime_init();
    dms_sprite_runtime_init();
    dms_collision_runtime_init();
    dms_actor_runtime_init();
    dms_gameplay_runtime_init();
    dms_audio_init();
}

void SYS_waitVBlank(void) {
    extern void dms_pad_frame_boundary(void);
    dms_audio_frame();
    dms_pad_frame_boundary();
    /* P1.4.1 frame-boundary contract.

       A game normally calls SYS_waitVBlank() at the END of its update.  The
       former implementation returned on the rising VBlank edge.  The next game
       update therefore started inside VBlank and could be interrupted by the
       scheduler while libdms was rewriting the multi-cell sprite table.  The
       PC host then presented that half-written table (for the tutorial PLAYER,
       5 cells instead of 10).

       Drain a VBlank already in progress, wait for the next full VBlank, then
       return on its falling edge.  Gameplay begins at the active-frame boundary
       and the live sprite table stays complete throughout VBlank/presentation.
       Frequency remains one update per 60 Hz frame. */
    while (VDP_STATUS & 1u) { }
    while ((VDP_STATUS & 1u) == 0u) { }
    while (VDP_STATUS & 1u) { }
}
