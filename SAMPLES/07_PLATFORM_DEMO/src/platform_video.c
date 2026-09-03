#include <stdint.h>
#include "dms1_hw.h"
#include "dms_vdp.h"
#include "platform_data.h"
#include "platform_video.h"

#define BG_A_STD_BASE 0x08000u
#define BG_B_STD_BASE 0x09000u
#define BG_A_HIGH_BASE 0x0B000u
#define LINE_SCROLL_A_BASE 0x0C000u
#define LINE_SCROLL_B_BASE 0x0C200u
#define MAP_W 64u
#define MAP_H 32u
#define TILE_BYTES 32u
#define PAL_BITS 0x1C00u
#define TILE_MASK 0x03FFu
#define FADE_MAX 8u

static volatile uint8_t * const vram8=(volatile uint8_t*)DMS_VRAM_BASE;
static volatile uint16_t * const vram16=(volatile uint16_t*)DMS_VRAM_BASE;
static volatile uint16_t * const cram16=(volatile uint16_t*)DMS_CRAM_BASE;
static volatile uint8_t * const vdp=(volatile uint8_t*)DMS_VDP_BASE;
static uint16_t last_a_tile;
static uint16_t last_b_tile;
static uint16_t stream_count;
static uint16_t rich_palettes[8][16];
static uint16_t sprite_palette_saved[16];
static uint8_t fade_level=FADE_MAX;

static const uint8_t high_banks[7]={0u,1u,2u,4u,5u,6u,7u};
static const int8_t wave32[32]={0,1,2,3,4,5,6,6,6,5,4,3,2,1,0,-1,-2,-3,-4,-5,-6,-6,-6,-5,-4,-3,-2,-1,0,1,2,3};

static uint8_t clamp7(int16_t v){if(v<0)return 0u;if(v>7)return 7u;return(uint8_t)v;}
static uint16_t rgb333(uint8_t r,uint8_t g,uint8_t b){return(uint16_t)(((uint16_t)(r&7u)<<6)|((uint16_t)(g&7u)<<3)|(b&7u));}

static uint16_t variant_colour(uint16_t c,uint8_t variant){
    uint8_t r,g,b;
    if(c==0u)return 0u;
    r=(uint8_t)((c>>6)&7u);g=(uint8_t)((c>>3)&7u);b=(uint8_t)(c&7u);
    switch(variant){
        case 4u:return rgb333(clamp7((int16_t)r+2),clamp7((int16_t)g+1),b);
        case 5u:return rgb333(r,clamp7((int16_t)g+2),clamp7((int16_t)b+2));
        case 6u:return rgb333(clamp7((int16_t)r+2),g,clamp7((int16_t)b+2));
        case 7u:return rgb333(clamp7((int16_t)r+1),clamp7((int16_t)g+2),b);
        default:return c;
    }
}

static void build_rich_palettes(void){
    uint8_t i;
    for(i=0u;i<16u;++i){
        rich_palettes[0][i]=platform_palettes[0][i];
        rich_palettes[1][i]=platform_palettes[1][i];
        rich_palettes[2][i]=platform_palettes[2][i];
        rich_palettes[4][i]=variant_colour(platform_palettes[0][i],4u);
        rich_palettes[5][i]=variant_colour(platform_palettes[1][i],5u);
        rich_palettes[6][i]=variant_colour(platform_palettes[2][i],6u);
        rich_palettes[7][i]=variant_colour(platform_palettes[0][i],7u);
    }
}

static uint16_t fade_colour(uint16_t c,uint8_t level){
    uint8_t r=(uint8_t)((c>>6)&7u),g=(uint8_t)((c>>3)&7u),b=(uint8_t)(c&7u);
    r=(uint8_t)(((uint16_t)r*level)/FADE_MAX);g=(uint8_t)(((uint16_t)g*level)/FADE_MAX);b=(uint8_t)(((uint16_t)b*level)/FADE_MAX);
    return rgb333(r,g,b);
}

static void apply_palettes(void){
    uint8_t bi,i;uint16_t work[16];
    for(bi=0u;bi<7u;++bi){uint8_t bank=high_banks[bi];for(i=0u;i<16u;++i)work[i]=fade_colour(rich_palettes[bank][i],fade_level);VDP_setPalette(bank,work);}
    for(i=0u;i<16u;++i)work[i]=fade_colour(sprite_palette_saved[i],fade_level);
    VDP_setPalette(3u,work);
}

static void set_scroll_a(uint16_t x){x&=0x01FFu;vdp[0x10]=(uint8_t)(x>>8);vdp[0x11]=(uint8_t)x;vdp[0x12]=0u;vdp[0x13]=0u;}
static void set_scroll_b(uint16_t x){x&=0x01FFu;vdp[0x14]=(uint8_t)(x>>8);vdp[0x15]=(uint8_t)x;vdp[0x16]=0u;vdp[0x17]=0u;}

static uint16_t source_word(const uint16_t *map,uint16_t wx,uint16_t y){
    if(y>=PLATFORM_WORLD_CELLS_H)return 0u;
    wx=(uint16_t)(wx%PLATFORM_WORLD_CELLS_W);
    return map[(uint32_t)y*PLATFORM_WORLD_CELLS_W+wx];
}

static uint16_t rich_word(uint16_t word,uint16_t wx,uint16_t y,uint8_t plane){
    uint8_t bank;
    if((word&TILE_MASK)==0u)return 0u;
    bank=high_banks[(uint8_t)(((wx>>2)+(y>>2)+(uint16_t)(plane*2u))%7u)];
    return(uint16_t)((word&~PAL_BITS)|((uint16_t)bank<<10));
}

static uint16_t a_word_for_mode(uint8_t mode,uint16_t wx,uint16_t y){
    uint16_t a=source_word(platform_map_a,wx,y);
    if(mode==DMS_MODE_HIGH_COLOR){if((a&TILE_MASK)==0u)a=source_word(platform_map_b,wx,y);return rich_word(a,wx,y,0u);}
    if(mode==DMS_MODE_LOW_RES)return rich_word(a,wx,y,0u);
    return a;
}

static uint16_t b_word_for_mode(uint8_t mode,uint16_t wx,uint16_t y){
    uint16_t b=source_word(platform_map_b,wx,y);
    if(mode==DMS_MODE_LOW_RES)return rich_word(b,wx,y,1u);
    return b;
}

static void stream_column(uint32_t base,uint16_t world_col,uint8_t mode,uint8_t plane){
    uint16_t y,slot=(uint16_t)(world_col&63u);volatile uint16_t *dst=vram16+(base>>1);
    for(y=0u;y<MAP_H;++y)dst[(uint32_t)y*MAP_W+slot]=(plane==0u)?a_word_for_mode(mode,world_col,y):b_word_for_mode(mode,world_col,y);
    ++stream_count;
}

static void fill_ring(uint32_t base,uint16_t first_world_col,uint8_t mode,uint8_t plane){
    uint16_t i,y;volatile uint16_t *dst=vram16+(base>>1);
    for(y=0u;y<MAP_H;++y)for(i=0u;i<MAP_W;++i)dst[(uint32_t)y*MAP_W+i]=0u;
    for(i=0u;i<MAP_W;++i){uint16_t wc=(uint16_t)(first_world_col+i),slot=(uint16_t)(wc&63u);for(y=0u;y<MAP_H;++y)dst[(uint32_t)y*MAP_W+slot]=(plane==0u)?a_word_for_mode(mode,wc,y):b_word_for_mode(mode,wc,y);}
}

static void clear_line_scroll(void){uint16_t y;volatile uint16_t *a=vram16+(LINE_SCROLL_A_BASE>>1),*b=vram16+(LINE_SCROLL_B_BASE>>1);for(y=0u;y<224u;++y){a[y]=0u;b[y]=0u;}}

void PLATFORM_VIDEO_rebuild(uint16_t camera_x,uint8_t mode){
    uint16_t a=(uint16_t)(camera_x>>3),b=(uint16_t)((camera_x>>2)>>3);
    if(mode==DMS_MODE_HIGH_COLOR||mode==DMS_MODE_LOW_RES)fill_ring(BG_A_HIGH_BASE,a,mode,0u);else fill_ring(BG_A_STD_BASE,a,mode,0u);
    if(mode==DMS_MODE_STANDARD||mode==DMS_MODE_SCROLL||mode==DMS_MODE_LOW_RES)fill_ring(BG_B_STD_BASE,b,mode,1u);
    last_a_tile=a;last_b_tile=b;set_scroll_a(camera_x);set_scroll_b((uint16_t)(camera_x>>2));
    if(mode!=DMS_MODE_SCROLL)clear_line_scroll();
}

void PLATFORM_VIDEO_init(uint16_t camera_x,uint8_t mode){
    uint32_t i,dst=(uint32_t)PLATFORM_TILE_BASE*TILE_BYTES;
    for(i=0u;i<(uint32_t)PLATFORM_TILE_COUNT*TILE_BYTES;++i)vram8[dst+i]=platform_tiles[i];
    for(i=0u;i<16u;++i)sprite_palette_saved[i]=(uint16_t)(cram16[48u+i]&0x01FFu);
    build_rich_palettes();fade_level=FADE_MAX;apply_palettes();stream_count=0u;PLATFORM_VIDEO_rebuild(camera_x,mode);
}

void PLATFORM_VIDEO_setMode(uint8_t mode,uint16_t camera_x){apply_palettes();PLATFORM_VIDEO_rebuild(camera_x,mode);}

void PLATFORM_VIDEO_setCamera(uint16_t camera_x,uint8_t mode){
    uint16_t a=(uint16_t)(camera_x>>3),b=(uint16_t)((camera_x>>2)>>3);uint32_t abase=(mode==DMS_MODE_HIGH_COLOR||mode==DMS_MODE_LOW_RES)?BG_A_HIGH_BASE:BG_A_STD_BASE;
    while(last_a_tile<a){++last_a_tile;stream_column(abase,(uint16_t)(last_a_tile+63u),mode,0u);}while(last_a_tile>a){--last_a_tile;stream_column(abase,last_a_tile,mode,0u);}
    if(mode==DMS_MODE_STANDARD||mode==DMS_MODE_SCROLL||mode==DMS_MODE_LOW_RES){while(last_b_tile<b){++last_b_tile;stream_column(BG_B_STD_BASE,(uint16_t)(last_b_tile+63u),mode,1u);}while(last_b_tile>b){--last_b_tile;stream_column(BG_B_STD_BASE,last_b_tile,mode,1u);}}
    set_scroll_a(camera_x);if(mode!=DMS_MODE_HIGH_COLOR&&mode!=DMS_MODE_SPRITE)set_scroll_b((uint16_t)(camera_x>>2));
}

void PLATFORM_VIDEO_tick(uint8_t mode,uint16_t frame_counter){
    uint16_t y;volatile uint16_t *a,*b;
    if(mode!=DMS_MODE_SCROLL)return;
    a=vram16+(LINE_SCROLL_A_BASE>>1);b=vram16+(LINE_SCROLL_B_BASE>>1);
    for(y=0u;y<224u;++y){int16_t wa=wave32[(uint8_t)(((y>>2)+(frame_counter>>1))&31u)];int16_t wb=(int16_t)(wave32[(uint8_t)(((y>>3)+(frame_counter>>2)+9u)&31u)]/2);a[y]=(uint16_t)wa;b[y]=(uint16_t)wb;}
}

void PLATFORM_VIDEO_setFade(uint8_t level){if(level>FADE_MAX)level=FADE_MAX;if(level==fade_level)return;fade_level=level;apply_palettes();}
uint16_t PLATFORM_VIDEO_streamedColumns(void){return stream_count;}
