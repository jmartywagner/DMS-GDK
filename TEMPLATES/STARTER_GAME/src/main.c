#include <dms1.h>
#include "resources.h"

int main(void) {
    SYS_init();
    VDP_setMode(DMS_MODE_STANDARD);
    BG_loadMap(RES_STAGE01);
    ACTOR_spawn(RES_HERO, 152, 96);
    COLL_bind(RES_STAGE01_COLL);
    MUS_play(RES_LEVEL1);
    for (;;) {
        ACTOR_update();
        COLL_update();
        if (PAD_pressed(DMS_BUTTON_C)) SFX_play(SFX_PLAYER_SHOT);
        SYS_waitVBlank();
    }
}
