#include <stdint.h>
#include "dms1_hw.h"
#include "dms_video.h"

static volatile uint8_t * const vdp = (volatile uint8_t*)DMS_VDP_BASE;

void VIDEO_setProfile(uint8_t profile) {
    if (profile < DMS_VIDEO_PROFILE_COUNT) vdp[5] = profile;
}

uint8_t VIDEO_getProfile(void) {
    uint8_t profile = vdp[5];
    return (profile < DMS_VIDEO_PROFILE_COUNT) ? profile : DMS_VIDEO_RAW;
}
