#include <stdint.h>
#include "dms_resource_runtime.h"
#include "dms_flow.h"

/* Compatibility fallbacks for projects that predate resources.dmsres.
   Generated tables and legacy src/resources.c definitions are strong symbols
   and automatically override these weak zero tables. */
#if defined(__GNUC__)
#define DMS_WEAK __attribute__((weak))
#else
#define DMS_WEAK
#endif

DMS_WEAK const DmsSpriteResourceDesc dms_sprite_resources[1] = {{0}};
DMS_WEAK const uint16_t dms_sprite_resource_count = 0u;
DMS_WEAK const DmsDresResourceDesc dms_dres_resources[1] = {{0}};
DMS_WEAK const uint16_t dms_dres_resource_count = 0u;
DMS_WEAK const DmsImageResourceDesc dms_image_resources[1] = {{0}};
DMS_WEAK const uint16_t dms_image_resource_count = 0u;
DMS_WEAK const DmsMapResourceDesc dms_map_resources[1] = {{0}};
DMS_WEAK const uint16_t dms_map_resource_count = 0u;
DMS_WEAK const DmsCollisionResourceDesc dms_collision_resources[1] = {{0}};
DMS_WEAK const uint16_t dms_collision_resource_count = 0u;
DMS_WEAK const DmsActorResourceDesc dms_actor_resources[1] = {{0}};
DMS_WEAK const uint16_t dms_actor_resource_count = 0u;
/* Overrideable tables MUST stay in this separate translation unit.
   Do not move them back into their consumer modules: -Os may constant-fold
   zero-sized weak defaults before strong generated definitions are linked. */
DMS_WEAK const DmsSceneResourceDesc dms_scene_resources[1] = {{0}};
DMS_WEAK const uint16_t dms_scene_resource_count = 0u;
DMS_WEAK const DmsMusicResourceDesc dms_music_resources[1] = {{0}};
DMS_WEAK const uint16_t dms_music_resource_count = 0u;
DMS_WEAK const DmsSfxResourceDesc dms_sfx_resources[1] = {{0}};
DMS_WEAK const uint16_t dms_sfx_resource_count = 0u;
DMS_WEAK const DmsAudioRegWrite dms_sfx_program[1] = {{0}};
DMS_WEAK const uint16_t dms_sfx_program_count = 0u;
DMS_WEAK const uint16_t dms_sfx_composite_members[1] = {0u};
DMS_WEAK const uint16_t dms_sfx_composite_member_count = 0u;
DMS_WEAK const uint8_t dms_music_channel_priorities[9] = {50u,50u,50u,50u,50u,50u,50u,50u,50u};
DMS_WEAK const DmsFlowDefinition dms_flow_definition = {0,0u,0,0u,0u};
