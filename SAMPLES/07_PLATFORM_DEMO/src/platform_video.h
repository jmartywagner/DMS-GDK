#ifndef DMS1_PLATFORM_VIDEO_H
#define DMS1_PLATFORM_VIDEO_H
#include <stdint.h>
void PLATFORM_VIDEO_init(uint16_t camera_x, uint8_t mode);
void PLATFORM_VIDEO_setMode(uint8_t mode, uint16_t camera_x);
void PLATFORM_VIDEO_setCamera(uint16_t camera_x, uint8_t mode);
void PLATFORM_VIDEO_rebuild(uint16_t camera_x, uint8_t mode);
void PLATFORM_VIDEO_tick(uint8_t mode, uint16_t frame_counter);
void PLATFORM_VIDEO_setFade(uint8_t level);
uint16_t PLATFORM_VIDEO_streamedColumns(void);
#endif
