#ifndef DMS1_PLATFORM_GAME_AUDIO_H
#define DMS1_PLATFORM_GAME_AUDIO_H
#include <stdint.h>
enum { GAME_SFX_JUMP=0,GAME_SFX_PICKUP,GAME_SFX_SPRING,GAME_SFX_HIT,GAME_SFX_BOOST,GAME_SFX_CHECKPOINT,GAME_SFX_DEATH,GAME_SFX_COUNT };
void GAME_AUDIO_init(void);
void GAME_AUDIO_tick(void);
void GAME_AUDIO_play(uint8_t id,uint8_t priority,int16_t screen_x);
#endif
