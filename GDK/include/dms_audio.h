#ifndef DMS_AUDIO_H
#define DMS_AUDIO_H
#include <stdint.h>
/* Runtime effectif : DMR + ADPCM-A/B + FM + SSG + composites. Les voix sont
   arbitrées par priorité; le shadow Z80 restaure l'état musique après un vol. */
void dms_audio_init(void);
void dms_audio_frame(void);
void MUS_play(uint16_t music_resource_id);
void MUS_stop(void);
void MUS_pause(void);
void MUS_resume(void);
void SFX_play(uint16_t sfx_id);
void SFX_stop(uint16_t sfx_id);
uint8_t SFX_isPlaying(uint16_t sfx_id);
void VOICE_play(uint16_t voice_id);
void dms_audio_set_music_volume(uint8_t level);
void dms_audio_set_sfx_volume(uint8_t level);
#endif
