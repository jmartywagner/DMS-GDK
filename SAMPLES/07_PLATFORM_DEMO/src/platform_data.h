#ifndef DMS1_PLATFORM_DATA_H
#define DMS1_PLATFORM_DATA_H
#include <stdint.h>
#define PLATFORM_WORLD_CELLS_W 512u
#define PLATFORM_WORLD_CELLS_H 28u
#define PLATFORM_WORLD_W 4096u
#define PLATFORM_WORLD_H 224u
#define PLATFORM_TILE_BASE 512u
#define PLATFORM_TILE_COUNT 64u
#define PLATFORM_OBJECT_COUNT 45u
#define PLATFORM_ZONE_COUNT 28u

enum { POBJ_NONE=0, POBJ_COLLECTIBLE=1, POBJ_ENEMY=2, POBJ_SPRING=3, POBJ_BOOSTER=4, POBJ_MOVING_PLATFORM=5, POBJ_CHECKPOINT=6, POBJ_PLAYER_START=7 };
enum { PCOLL_SOLID=0, PCOLL_ONEWAY=1, PCOLL_DANGER=2, PCOLL_LADDER=3, PCOLL_WATER=4, PCOLL_SLOW=5, PCOLL_TRIGGER=6, PCOLL_EXIT=7, PCOLL_CHECKPOINT=8, PCOLL_CUSTOM=9 };
enum { PSHAPE_RECT=0, PSHAPE_SEGMENT=1, PSHAPE_SLOPE=2, PSHAPE_POLYGON=3, PSHAPE_POINT=4 };
typedef struct { int16_t x,y; uint8_t type; int16_t param1; uint8_t param2; } PlatformObjectDef;
typedef struct { int16_t x0,y0,x1,y1; uint8_t type,shape; int16_t ax,ay,bx,by; uint8_t target_mask; } PlatformZoneDef;
extern const uint8_t platform_tiles[];
extern const uint16_t platform_palettes[3][16];
extern const uint16_t platform_map_a[PLATFORM_WORLD_CELLS_W*PLATFORM_WORLD_CELLS_H];
extern const uint16_t platform_map_b[PLATFORM_WORLD_CELLS_W*PLATFORM_WORLD_CELLS_H];
extern const PlatformObjectDef platform_objects[PLATFORM_OBJECT_COUNT];
extern const PlatformZoneDef platform_zones[PLATFORM_ZONE_COUNT];
#endif
