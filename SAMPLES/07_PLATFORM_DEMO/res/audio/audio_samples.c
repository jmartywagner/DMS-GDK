#include "audio_samples.h"

const DmsAudioSampleDef dms_audio_samples[DMS_SAMPLE_COUNT] = {
    {0u, 1536u, 0, 5, 18519, DMS_AUDIO_CODEC_ADPCM_A, 0}, /* JUMP */
    {1536u, 2048u, 6, 13, 18519, DMS_AUDIO_CODEC_ADPCM_A, 0}, /* PICKUP */
    {3584u, 2816u, 14, 24, 18519, DMS_AUDIO_CODEC_ADPCM_A, 0}, /* SPRING */
    {6400u, 2048u, 25, 32, 18519, DMS_AUDIO_CODEC_ADPCM_A, 0}, /* HIT */
    {8448u, 2560u, 33, 42, 18519, DMS_AUDIO_CODEC_ADPCM_A, 0}, /* BOOST */
    {11008u, 4352u, 43, 59, 18519, DMS_AUDIO_CODEC_ADPCM_A, 0}, /* CHECKPOINT */
    {15360u, 5120u, 60, 79, 18519, DMS_AUDIO_CODEC_ADPCM_A, 0}, /* DEATH */
};
const uint16_t dms_audio_sample_count = 7;
