#ifndef DMS_VIDEO_H
#define DMS_VIDEO_H
#include <stdint.h>

/* Presentation profiles are PC/display output filters. They do NOT alter the
 * frozen DMS-1 VDP rules, VRAM layout, palettes, sprite limits or collisions. */
#define DMS_VIDEO_RAW                0
#define DMS_VIDEO_SCANLINES          1
#define DMS_VIDEO_CRT_SOFT           2
#define DMS_VIDEO_CRT_SCANLINES      3
#define DMS_VIDEO_COMPOSITE          4
#define DMS_VIDEO_PROFILE_COUNT      5

void VIDEO_setProfile(uint8_t profile);
uint8_t VIDEO_getProfile(void);

#endif
