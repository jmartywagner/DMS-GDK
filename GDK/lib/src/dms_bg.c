#include <stdint.h>
#include "dms1_hw.h"
#include "dms_bg.h"
#include "dms_resource_runtime.h"
#include "dms_vdp.h"

#define MAP_W 64u
#define MAP_H 32u
#define TILE_BYTES 32u
#define TILE_MASK 0x03FFu
#define EMPTY_WORD 0xFFFFu
#define EMPTY_CANON_MASK 0x1FFFu
#define EMPTY_CANON_VALUE 0x1FFFu
#define BG_A_STD_BASE 0x08000u
#define BG_B_STD_BASE 0x09000u
#define BG_A_HIGH_BASE 0x0B000u

static volatile uint8_t * const vram8=(volatile uint8_t*)DMS_VRAM_BASE;
static volatile uint16_t * const vram16=(volatile uint16_t*)DMS_VRAM_BASE;
static volatile uint16_t * const cram16=(volatile uint16_t*)DMS_CRAM_BASE;
static volatile uint8_t * const vdp=(volatile uint8_t*)DMS_VDP_BASE;

static const DmsMapResourceDesc *g_map;
static const DmsImageResourceDesc *g_tileset;
static const DmsImageResourceDesc *g_bg_b_image;
static uint16_t g_tile_base;
static uint8_t g_video_mode;
static uint16_t g_bg_b_tile_base;
static uint16_t g_pattern_floor=1u;
static uint16_t g_a_x,g_a_y,g_b_x,g_b_y;
static int16_t g_a_sx,g_a_sy,g_b_sx,g_b_sy;

static const DmsMapResourceDesc *find_map(uint16_t id){
    uint16_t i;
    for(i=0;i<dms_map_resource_count;++i) if(dms_map_resources[i].resource_id==id) return &dms_map_resources[i];
    return 0;
}
static const DmsImageResourceDesc *find_image(uint16_t id){
    uint16_t i;
    for(i=0;i<dms_image_resource_count;++i) if(dms_image_resources[i].resource_id==id) return &dms_image_resources[i];
    return 0;
}
static uint8_t mode_has_bg_b(uint8_t mode){return (uint8_t)(mode==0u||mode==2u||mode==4u);}
static uint32_t plane_base(uint8_t plane){
    if(!g_map) return BG_A_STD_BASE;
    if(plane) return BG_B_STD_BASE;
    return (g_video_mode==1u || g_video_mode==4u) ? BG_A_HIGH_BASE : BG_A_STD_BASE;
}
static uint8_t is_empty_word(uint16_t w){
    /* DMSRES normalise parfois le sentinel auteur 0xFFFF en 0xDFFF après
       traitement des attributs. Dans les deux cas palette=7 + tile=1023
       désigne une cellule vide, jamais un motif de map réel Paper War. */
    return (uint8_t)(w==EMPTY_WORD || (w&EMPTY_CANON_MASK)==EMPTY_CANON_VALUE);
}
static uint16_t world_word(uint8_t plane,uint16_t wx,uint16_t wy){
    uint16_t w;
    if(!g_map) return 0u;

    if(plane && g_bg_b_image && g_bg_b_image->tilemap &&
       wx<g_bg_b_image->width_cells && wy<g_bg_b_image->height_cells){
        w=g_bg_b_image->tilemap[(uint32_t)wy*g_bg_b_image->width_cells+wx];
        if(is_empty_word(w)) return 0u;
        return (uint16_t)((w&~TILE_MASK)|(((w&TILE_MASK)+g_bg_b_tile_base)&TILE_MASK));
    }

    if(wx>=g_map->width_cells || wy>=g_map->height_cells) return 0u;
    {
        const uint16_t *src=plane?g_map->bg_b:g_map->bg_a;
        if(!src) return 0u;
        w=src[(uint32_t)wy*g_map->width_cells+wx];
    }
    if(is_empty_word(w)) return 0u;
    return (uint16_t)((w&~TILE_MASK)|(((w&TILE_MASK)+g_tile_base)&TILE_MASK));
}
static void stream_column(uint8_t plane,uint16_t world_x,uint16_t first_y){
    uint16_t y,slot=(uint16_t)(world_x&63u);
    volatile uint16_t *dst=vram16+(plane_base(plane)>>1);
    for(y=0u;y<MAP_H;++y){
        uint16_t wy=(uint16_t)(first_y+y);
        dst[(uint32_t)(wy&31u)*MAP_W+slot]=world_word(plane,world_x,wy);
    }
}
static void stream_row(uint8_t plane,uint16_t world_y,uint16_t first_x){
    uint16_t x,slot=(uint16_t)(world_y&31u);
    volatile uint16_t *dst=vram16+(plane_base(plane)>>1);
    for(x=0u;x<MAP_W;++x){
        uint16_t wx=(uint16_t)(first_x+x);
        dst[(uint32_t)slot*MAP_W+(wx&63u)]=world_word(plane,wx,world_y);
    }
}
static void fill_ring(uint8_t plane,uint16_t first_x,uint16_t first_y){
    uint16_t x,y;
    volatile uint16_t *dst=vram16+(plane_base(plane)>>1);
    /* BUILD 12: un seul passage suffit. world_word() renvoie déjà 0 hors map.
       L'ancien clear + fill doublait le coût des transitions de mode/map. */
    for(y=0u;y<MAP_H;++y){
        uint16_t wy=(uint16_t)(first_y+y);
        for(x=0u;x<MAP_W;++x){
            uint16_t wx=(uint16_t)(first_x+x);
            dst[(uint32_t)(wy&31u)*MAP_W+(wx&63u)]=world_word(plane,wx,wy);
        }
    }
}
static uint16_t plane_world_w(uint8_t plane){
    if(plane && g_bg_b_image && g_bg_b_image->width_cells) return (uint16_t)(g_bg_b_image->width_cells*8u);
    return g_map?(uint16_t)(g_map->width_cells*8u):0u;
}
static uint16_t plane_world_h(uint8_t plane){
    if(plane && g_bg_b_image && g_bg_b_image->height_cells) return (uint16_t)(g_bg_b_image->height_cells*8u);
    return g_map?(uint16_t)(g_map->height_cells*8u):0u;
}
static int16_t clamp_scroll_x(uint8_t plane,int16_t x){
    int32_t maxx;
    uint16_t screenw=(g_map && g_video_mode==4u)?256u:320u;
    if(!g_map || x<0) return 0;
    maxx=(int32_t)plane_world_w(plane)-(int32_t)screenw;
    if(maxx<0) maxx=0;
    if((int32_t)x>maxx) return (int16_t)maxx;
    return x;
}
static int16_t clamp_scroll_y(uint8_t plane,int16_t y){
    int32_t maxy;
    if(!g_map || y<0) return 0;
    maxy=(int32_t)plane_world_h(plane)-224;
    if(maxy<0) maxy=0;
    if((int32_t)y>maxy) return (int16_t)maxy;
    return y;
}
static void set_scroll_regs(uint8_t plane,uint16_t x,uint16_t y){
    uint8_t o=plane?0x14u:0x10u;
    x&=0x01FFu; y&=0x00FFu;
    vdp[o+0u]=(uint8_t)(x>>8); vdp[o+1u]=(uint8_t)x;
    vdp[o+2u]=(uint8_t)(y>>8); vdp[o+3u]=(uint8_t)y;
}
static void update_plane(uint8_t plane,int16_t sx,int16_t sy){
    uint16_t nx,ny,*px,*py;
    sx=clamp_scroll_x(plane,sx); sy=clamp_scroll_y(plane,sy);
    nx=(uint16_t)sx>>3; ny=(uint16_t)sy>>3;
    px=plane?&g_b_x:&g_a_x; py=plane?&g_b_y:&g_a_y;
    while(*px<nx){++(*px);stream_column(plane,(uint16_t)(*px+63u),*py);}
    while(*px>nx){--(*px);stream_column(plane,*px,*py);}
    while(*py<ny){++(*py);stream_row(plane,(uint16_t)(*py+31u),*px);}
    while(*py>ny){--(*py);stream_row(plane,*py,*px);}
    set_scroll_regs(plane,(uint16_t)sx,(uint16_t)sy);
    if(plane){g_b_sx=sx;g_b_sy=sy;}else{g_a_sx=sx;g_a_sy=sy;}
}
static void jump_plane(uint8_t plane,int16_t sx,int16_t sy){
    uint16_t nx,ny;
    sx=clamp_scroll_x(plane,sx); sy=clamp_scroll_y(plane,sy);
    nx=(uint16_t)sx>>3; ny=(uint16_t)sy>>3;
    if(plane){g_b_x=nx;g_b_y=ny;g_b_sx=sx;g_b_sy=sy;}
    else{g_a_x=nx;g_a_y=ny;g_a_sx=sx;g_a_sy=sy;}
    fill_ring(plane,nx,ny);
    set_scroll_regs(plane,(uint16_t)sx,(uint16_t)sy);
}
static void load_image_palettes(const DmsImageResourceDesc *im){
    uint16_t p,i;
    if(!im) return;
    for(p=0u;p<im->palette_count;++p){
        uint16_t pid=im->palette_ids[p]&7u;
        for(i=0u;i<16u;++i) cram16[pid*16u+i]=(uint16_t)(im->palettes[p*16u+i]&0x01FFu);
    }
}

static void load_map(uint16_t map_resource_id,uint8_t load_palettes){
    uint16_t i;
    g_map=find_map(map_resource_id); g_tileset=0; g_bg_b_image=0;
    g_tile_base=0u;g_bg_b_tile_base=1u;g_pattern_floor=1u;
    g_video_mode=g_map?g_map->mode:0u;
    if(!g_map) return;
    g_tileset=find_image(g_map->tileset_resource_id);
    if(!g_tileset || !g_tileset->tiles || g_tileset->tile_count==0u || g_tileset->tile_count>1024u){g_map=0;return;}

    for(i=0u;i<TILE_BYTES;++i)vram8[i]=0u;
    g_tile_base=(uint16_t)(1024u-g_tileset->tile_count);

    if(g_map->bg_b_image_resource_id!=0xFFFFu){
        g_bg_b_image=find_image(g_map->bg_b_image_resource_id);
        if(g_bg_b_image && g_bg_b_image->tiles && g_bg_b_image->tilemap &&
           g_bg_b_image->width_cells && g_bg_b_image->height_cells &&
           (uint32_t)1u+g_bg_b_image->tile_count<=g_tile_base){
            g_bg_b_tile_base=1u;
            for(i=0u;i<(uint16_t)(g_bg_b_image->tile_count*TILE_BYTES);++i)
                vram8[(uint32_t)g_bg_b_tile_base*TILE_BYTES+i]=g_bg_b_image->tiles[i];
            g_pattern_floor=(uint16_t)(g_bg_b_tile_base+g_bg_b_image->tile_count);
            if(load_palettes)load_image_palettes(g_bg_b_image);
        } else g_bg_b_image=0;
    }

    for(i=0u;i<(uint16_t)(g_tileset->tile_count*TILE_BYTES);++i)
        vram8[(uint32_t)g_tile_base*TILE_BYTES+i]=g_tileset->tiles[i];
    if(load_palettes)load_image_palettes(g_tileset);

    VDP_setMode(g_video_mode);
    g_a_x=g_a_y=g_b_x=g_b_y=0u; g_a_sx=g_a_sy=g_b_sx=g_b_sy=0;
    fill_ring(0u,0u,0u);
    if(mode_has_bg_b(g_video_mode)) fill_ring(1u,0u,0u);
    set_scroll_regs(0u,0u,0u); set_scroll_regs(1u,0u,0u);
}
void BG_loadMap(uint16_t map_resource_id){load_map(map_resource_id,1u);}
void BG_loadMapKeepBlack(uint16_t map_resource_id){load_map(map_resource_id,0u);}
void BG_reloadMapPalettes(void){load_image_palettes(g_bg_b_image);load_image_palettes(g_tileset);}
void BG_setScroll(int16_t x,int16_t y){BG_setScrollA(x,y);}
void BG_setScrollA(int16_t x,int16_t y){if(g_map)update_plane(0u,x,y);}
void BG_setScrollB(int16_t x,int16_t y){if(g_map && mode_has_bg_b(g_video_mode))update_plane(1u,x,y);}
void BG_jumpScrollA(int16_t x,int16_t y){if(g_map)jump_plane(0u,x,y);}
void BG_jumpScrollB(int16_t x,int16_t y){if(g_map && mode_has_bg_b(g_video_mode))jump_plane(1u,x,y);}
void BG_replaceTilePattern(uint16_t tile_id,const uint8_t *pattern32){
    uint16_t i,phys;
    if(!g_map || !g_tileset || !pattern32 || tile_id>=g_tileset->tile_count) return;
    phys=(uint16_t)(g_tile_base+tile_id);
    if(phys>=1024u) return;
    for(i=0u;i<TILE_BYTES;++i) vram8[(uint32_t)phys*TILE_BYTES+i]=pattern32[i];
}
void BG_setVideoMode(uint8_t mode){
    int16_t ax,ay,bx,by;
    if(mode>4u)return;
    if(mode==g_video_mode){VDP_setMode(mode);return;}
    ax=g_a_sx;ay=g_a_sy;bx=g_b_sx;by=g_b_sy;
    g_video_mode=mode;VDP_setMode(mode);
    if(!g_map)return;
    /* Le changement de mode conserve la fenêtre monde au lieu de revenir à 0.
       Paper War l'appelle uniquement sous noir, mais ceci évite aussi le flash
       de la première colonne de map observé sur le host. */
    jump_plane(0u,ax,ay);
    if(mode_has_bg_b(mode))jump_plane(1u,bx,by);
    else set_scroll_regs(1u,0u,0u);
}
uint8_t BG_videoMode(void){return g_video_mode;}

int16_t dms_bg_scroll_x(void){ return g_a_sx; }
int16_t dms_bg_scroll_y(void){ return g_a_sy; }
uint16_t dms_bg_pattern_limit(void){ return g_map ? g_tile_base : 1024u; }
uint16_t dms_bg_pattern_floor(void){ return g_pattern_floor; }
