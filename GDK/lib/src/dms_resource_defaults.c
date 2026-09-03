#include <stdint.h>
#include "dms_resource_runtime.h"

/* Empty weak sprite table for projects that do not use SPR_create().
   Existing/generated project resource tables override these symbols strongly. */
__attribute__((weak)) const DmsSpriteResourceDesc dms_sprite_resources[1] = {
    { 0, 0, 0u, 0u, 0u, 0u, 0u }
};
__attribute__((weak)) const uint16_t dms_sprite_resource_count = 0u;
