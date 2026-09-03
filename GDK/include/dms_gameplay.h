#ifndef DMS_GAMEPLAY_H
#define DMS_GAMEPLAY_H
#include <stdint.h>

/* Petit état de partie générique partagé par les outils/runtime. */
void dms_gameplay_runtime_init(void);
void GAMEPLAY_reset(void);
void GAMEPLAY_setStartingLives(uint16_t lives);
uint16_t GAMEPLAY_lives(void);
uint16_t GAMEPLAY_startingLives(void);
void GAMEPLAY_addLife(uint16_t amount);
uint8_t GAMEPLAY_loseLife(void); /* 1 s'il reste au moins une vie */
void GAMEPLAY_setCheckpoint(int16_t x, int16_t y, int16_t camera_x, int16_t camera_y);
void GAMEPLAY_clearCheckpoint(void);
uint8_t GAMEPLAY_hasCheckpoint(void);
int16_t GAMEPLAY_checkpointX(void);
int16_t GAMEPLAY_checkpointY(void);
int16_t GAMEPLAY_checkpointCameraX(void);
int16_t GAMEPLAY_checkpointCameraY(void);
#endif
