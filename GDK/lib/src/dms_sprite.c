#include <stdint.h>
#include "dms1_hw.h"
#include "dms_sprite.h"
#include "dms_resource_runtime.h"
#include "dms_vdp.h"

#define SPR_TABLE_OFFSET 0x0A000u
#define HW_SPR_COUNT 128u
#define LOGICAL_SPR_COUNT 128u
#define SPR_ENTRY_WORDS 4u
#define SPR_PRIORITY 0x0008u
#define SPR_HFLIP 0x0010u
#define SPR_VFLIP 0x0020u
#define SPR_SIZE16 0x0040u
#define TILE_BYTES 32u
#define TILE_CACHE_COUNT 32u

typedef struct {
    uint8_t used;
    uint8_t hw_first;
    uint8_t hw_count;
    uint8_t legacy_mode;
    uint8_t raw16_mode;
    uint8_t visible;
    uint8_t screen_space;
    uint8_t priority_override;
    uint8_t palette_override;
    uint8_t flip_x;
    uint16_t tile_base;
    uint16_t frame;
    int16_t x, y;
    const DmsDresResourceDesc *dres;
    const DmsSpriteResourceDesc *legacy;
} LogicalSprite;

typedef struct { uint8_t used; uint16_t resource_id; uint16_t base; } TileCache;
static LogicalSprite g_sprites[LOGICAL_SPR_COUNT];
static uint8_t g_hw_used[HW_SPR_COUNT];
static TileCache g_cache[TILE_CACHE_COUNT];
static uint16_t g_next_tile;
static uint16_t g_reserved_top=1u;

static volatile uint8_t * const vram8 = (volatile uint8_t*)DMS_VRAM_BASE;
static volatile uint16_t * const vram16 = (volatile uint16_t*)DMS_VRAM_BASE;
static volatile uint16_t * const cram16 = (volatile uint16_t*)DMS_CRAM_BASE;

static volatile uint16_t *entry(uint16_t s) {
    return vram16 + ((SPR_TABLE_OFFSET >> 1) + ((uint32_t)s * SPR_ENTRY_WORDS));
}
static void hide_hw(uint16_t s){
    volatile uint16_t *e=entry(s); e[0]=0x01FFu; e[1]=0x01FFu; e[2]=0u; e[3]=0u;
}
static const DmsDresResourceDesc *find_dres(uint16_t id){
    uint16_t i;
    for(i=0u;i<dms_dres_resource_count;++i) if(dms_dres_resources[i].resource_id==id) return &dms_dres_resources[i];
    return 0;
}
static const DmsSpriteResourceDesc *find_legacy(uint16_t id){
    if(id>=dms_sprite_resource_count) return 0;
    return &dms_sprite_resources[id];
}
static uint8_t dres_uses_palette(const DmsDresResourceDesc *r,uint16_t local_palette){
    uint16_t i;
    if(!r || local_palette>=r->palette_count)return 0u;
    /* Some authoring files retain a complete four-palette source bank even
       though every emitted cell references palette 0. Loading the unused
       entries would clobber palettes owned by the map or HUD. Descriptors
       without cell metadata keep the conservative legacy behaviour. */
    if(!r->cells || !r->cell_count)return 1u;
    for(i=0u;i<r->cell_count;++i)if(r->cells[i].palette==local_palette)return 1u;
    return 0u;
}
static uint16_t hw_mode_limit(void){
    switch(VDP_getMode()){
        case DMS_MODE_SCROLL: return 48u;
        case DMS_MODE_SPRITE: return 128u;
        case DMS_MODE_STANDARD:
        case DMS_MODE_HIGH_COLOR:
        case DMS_MODE_LOW_RES:
        default: return 80u;
    }
}
static int16_t alloc_hw(uint16_t count){
    uint16_t first, i, limit=hw_mode_limit();
    if(!count || count>limit) return -1;
    for(first=0u;first+count<=limit;++first){
        for(i=0u;i<count;++i) if(g_hw_used[first+i]) break;
        if(i==count){ for(i=0u;i<count;++i) g_hw_used[first+i]=1u; return (int16_t)first; }
        first=(uint16_t)(first+i);
    }
    return -1;
}
static void free_hw(uint16_t first,uint16_t count){
    uint16_t i; for(i=0u;i<count;++i){g_hw_used[first+i]=0u;hide_hw((uint16_t)(first+i));}
}
static uint16_t load_dres_tiles_ex(const DmsDresResourceDesc *r,uint8_t load_palette){
    uint16_t i,c,limit=dms_bg_pattern_limit(),floor=dms_bg_pattern_floor();
    if(g_next_tile<floor)g_next_tile=floor;
    for(c=0u;c<TILE_CACHE_COUNT;++c) if(g_cache[c].used && g_cache[c].resource_id==r->resource_id) return g_cache[c].base;
    if(!r->tiles || !r->tile_count || (uint32_t)g_next_tile+r->tile_count>limit) return 0xFFFFu;
    for(i=0u;i<(uint16_t)(r->tile_count*TILE_BYTES);++i) vram8[(uint32_t)g_next_tile*TILE_BYTES+i]=r->tiles[i];
    if(load_palette)for(i=0u;i<r->palette_count;++i){
        uint16_t p=(uint16_t)r->palette_base+i,k;
        if(!dres_uses_palette(r,i))continue;
        if(p>=8u) break;
        for(k=0u;k<16u;++k) cram16[p*16u+k]=(uint16_t)(r->palettes[i*16u+k]&0x01FFu);
    }
    for(c=0u;c<TILE_CACHE_COUNT;++c) if(!g_cache[c].used){g_cache[c].used=1u;g_cache[c].resource_id=r->resource_id;g_cache[c].base=g_next_tile;break;}
    i=g_next_tile;g_next_tile=(uint16_t)(g_next_tile+r->tile_count);return i;
}
static uint16_t load_legacy_tiles_ex(const DmsSpriteResourceDesc *r,uint8_t load_palette){
    uint16_t i,limit=dms_bg_pattern_limit(),floor=dms_bg_pattern_floor(),base;
    if(g_next_tile<floor)g_next_tile=floor;
    base=g_next_tile;
    if(!r->tiles || !r->tile_count || (uint32_t)base+r->tile_count>limit) return 0xFFFFu;
    for(i=0u;i<(uint16_t)(r->tile_count*TILE_BYTES);++i) vram8[(uint32_t)base*TILE_BYTES+i]=r->tiles[i];
    if(load_palette&&r->palette){
        uint16_t p=(uint16_t)((r->palette_id&7u)*16u);
        for(i=0u;i<16u;++i) cram16[p+i]=(uint16_t)(r->palette[i]&0x01FFu);
    }
    g_next_tile=(uint16_t)(g_next_tile+r->tile_count);return base;
}
static void render_dres(LogicalSprite *s){
    const DmsSpriteFrameDesc *f;
    uint16_t i;
    int16_t camx=s->screen_space?0:dms_bg_scroll_x(),camy=s->screen_space?0:dms_bg_scroll_y();
    if(!s->used || !s->dres || s->frame>=s->dres->frame_count) return;
    f=&s->dres->frames[s->frame];
    for(i=0u;i<s->hw_count;++i) hide_hw((uint16_t)s->hw_first+i);
    if(!s->visible)return;
    for(i=0u;i<f->cell_count && i<s->hw_count;++i){
        const DmsSpriteCellDesc *c=&s->dres->cells[f->first_cell+i];
        volatile uint16_t *e=entry((uint16_t)s->hw_first+i);
        int16_t sx=(int16_t)(s->x-f->pivot_x+c->x-camx);
        int16_t sy=(int16_t)(s->y-f->pivot_y+c->y-camy);
        uint16_t attr=(uint16_t)(s->palette_override==0xFFu?((s->dres->palette_base+c->palette)&7u):(s->palette_override&7u));
        if((s->priority_override==0xFFu && s->dres->priority) || s->priority_override==1u) attr|=SPR_PRIORITY;
        if(((c->flags&DMS_SPR_CELL_HFLIP)?1u:0u)^s->flip_x) attr|=SPR_HFLIP;
        if(c->flags&DMS_SPR_CELL_VFLIP) attr|=SPR_VFLIP;
        e[0]=(uint16_t)sy&0x01FFu;e[1]=(uint16_t)sx&0x01FFu;
        e[2]=(uint16_t)(s->tile_base+c->tile)&0x03FFu;e[3]=attr;
    }
}
static void render_legacy(LogicalSprite *s){
    volatile uint16_t *e;
    uint16_t attr;
    if(!s->used || !s->legacy) return;
    if(!s->visible){hide_hw(s->hw_first);return;}
    e=entry(s->hw_first);attr=(uint16_t)(s->palette_override==0xFFu?(s->legacy->palette_id&7u):(s->palette_override&7u));
    if((s->priority_override==0xFFu && s->legacy->priority) || s->priority_override==1u)attr|=SPR_PRIORITY;
    if(s->flip_x)attr|=SPR_HFLIP;
    if(s->legacy->width==16u && s->legacy->height==16u)attr|=SPR_SIZE16;
    e[0]=(uint16_t)s->y&0x01FFu;e[1]=(uint16_t)s->x&0x01FFu;e[2]=s->tile_base&0x03FFu;e[3]=attr;
}
static void render_raw16(LogicalSprite *s){
    volatile uint16_t *e;uint16_t attr;
    if(!s->used)return;
    if(!s->visible){hide_hw(s->hw_first);return;}
    e=entry(s->hw_first);attr=(uint16_t)(s->palette_override&7u);
    if(s->priority_override==1u)attr|=SPR_PRIORITY;
    e[0]=(uint16_t)s->y&0x01FFu;e[1]=(uint16_t)s->x&0x01FFu;e[2]=s->tile_base&0x03FFu;e[3]=(uint16_t)(attr|SPR_SIZE16);
}
static void position_dres(LogicalSprite *s){
    const DmsSpriteFrameDesc *f;uint16_t i;int16_t camx,camy;
    if(!s->used||!s->visible||!s->dres||s->frame>=s->dres->frame_count)return;
    f=&s->dres->frames[s->frame];camx=s->screen_space?0:dms_bg_scroll_x();camy=s->screen_space?0:dms_bg_scroll_y();
    for(i=0u;i<f->cell_count&&i<s->hw_count;++i){const DmsSpriteCellDesc*c=&s->dres->cells[f->first_cell+i];volatile uint16_t*e=entry((uint16_t)s->hw_first+i);e[0]=(uint16_t)(s->y-f->pivot_y+c->y-camy)&0x01FFu;e[1]=(uint16_t)(s->x-f->pivot_x+c->x-camx)&0x01FFu;}
}
static void position_only(LogicalSprite *s){
    if(s->raw16_mode){if(s->visible){volatile uint16_t*e=entry(s->hw_first);e[0]=(uint16_t)s->y&0x01FFu;e[1]=(uint16_t)s->x&0x01FFu;}return;}
    if(s->legacy_mode){if(s->visible){volatile uint16_t*e=entry(s->hw_first);e[0]=(uint16_t)s->y&0x01FFu;e[1]=(uint16_t)s->x&0x01FFu;}return;}
    position_dres(s);
}
static void render(LogicalSprite *s){if(s->raw16_mode)render_raw16(s);else if(s->legacy_mode)render_legacy(s);else render_dres(s);}

void dms_sprite_runtime_init(void){
    uint16_t i;g_next_tile=1u;g_reserved_top=1u;
    /* Pattern 0 is permanently transparent so empty map cells stay empty. */
    for(i=0u;i<TILE_BYTES;++i)vram8[i]=0u;
    for(i=0u;i<HW_SPR_COUNT;++i){g_hw_used[i]=0u;hide_hw(i);}
    for(i=0u;i<LOGICAL_SPR_COUNT;++i)g_sprites[i].used=0u;
    for(i=0u;i<TILE_CACHE_COUNT;++i)g_cache[i].used=0u;
}
uint16_t dms_sprite_reserve_patterns(uint16_t pattern_count){
    uint16_t base,limit=dms_bg_pattern_limit(),floor=dms_bg_pattern_floor();
    if(g_next_tile<floor)g_next_tile=floor;
    base=g_next_tile;
    if(!pattern_count || (uint32_t)base+pattern_count>limit)return 0xFFFFu;
    g_next_tile=(uint16_t)(base+pattern_count);if(g_next_tile>g_reserved_top)g_reserved_top=g_next_tile;return base;
}

static DmsSprite create_sprite(uint16_t resource_id,int16_t x,int16_t y,uint8_t load_palette){
    uint16_t slot,base,count;int16_t hw;
    const DmsDresResourceDesc *dr=find_dres(resource_id);
    const DmsSpriteResourceDesc *lr=0;
    if(!dr)lr=find_legacy(resource_id);
    if(!dr && !lr)return 0xFFFFu;
    for(slot=0u;slot<LOGICAL_SPR_COUNT;++slot)if(!g_sprites[slot].used)break;
    if(slot==LOGICAL_SPR_COUNT)return 0xFFFFu;
    count=dr?dr->max_frame_cells:1u;
    if(!count)return 0xFFFFu;
    hw=alloc_hw(count);if(hw<0)return 0xFFFFu;
    base=dr?load_dres_tiles_ex(dr,load_palette):load_legacy_tiles_ex(lr,load_palette);
    if(base==0xFFFFu){free_hw((uint16_t)hw,count);return 0xFFFFu;}
    g_sprites[slot].used=1u;g_sprites[slot].hw_first=(uint8_t)hw;g_sprites[slot].hw_count=(uint8_t)count;
    g_sprites[slot].legacy_mode=dr?0u:1u;g_sprites[slot].raw16_mode=0u;g_sprites[slot].tile_base=base;g_sprites[slot].frame=0u;
    g_sprites[slot].visible=1u;g_sprites[slot].screen_space=0u;g_sprites[slot].priority_override=0xFFu;g_sprites[slot].palette_override=0xFFu;g_sprites[slot].flip_x=0u;
    g_sprites[slot].x=x;g_sprites[slot].y=y;g_sprites[slot].dres=dr;g_sprites[slot].legacy=lr;
    render(&g_sprites[slot]);return slot;
}
DmsSprite SPR_create(uint16_t resource_id,int16_t x,int16_t y){return create_sprite(resource_id,x,y,1u);}
DmsSprite SPR_createNoPalette(uint16_t resource_id,int16_t x,int16_t y){return create_sprite(resource_id,x,y,0u);}
void SPR_destroy(DmsSprite sprite){
    if(sprite>=LOGICAL_SPR_COUNT || !g_sprites[sprite].used)return;
    free_hw(g_sprites[sprite].hw_first,g_sprites[sprite].hw_count);
    g_sprites[sprite].used=0u;
}
void SPR_setPosition(DmsSprite sprite,int16_t x,int16_t y){
    if(sprite>=LOGICAL_SPR_COUNT || !g_sprites[sprite].used)return;
    if(g_sprites[sprite].screen_space && g_sprites[sprite].x==x && g_sprites[sprite].y==y)return;
    g_sprites[sprite].x=x;g_sprites[sprite].y=y;position_only(&g_sprites[sprite]);
}
void SPR_setFrame(DmsSprite sprite,uint16_t frame_id){dms_sprite_set_frame(sprite,frame_id);}
void SPR_setVisible(DmsSprite sprite,uint8_t visible){
    if(sprite>=LOGICAL_SPR_COUNT || !g_sprites[sprite].used)return;
    visible=visible?1u:0u;if(g_sprites[sprite].visible==visible)return;
    g_sprites[sprite].visible=visible;render(&g_sprites[sprite]);
}
void SPR_setPriority(DmsSprite sprite,uint8_t in_front){
    if(sprite>=LOGICAL_SPR_COUNT || !g_sprites[sprite].used)return;
    in_front=in_front?1u:0u;if(g_sprites[sprite].priority_override==in_front)return;
    g_sprites[sprite].priority_override=in_front;render(&g_sprites[sprite]);
}
void SPR_setScreenSpace(DmsSprite sprite,uint8_t screen_space){
    if(sprite>=LOGICAL_SPR_COUNT || !g_sprites[sprite].used)return;
    screen_space=screen_space?1u:0u;if(g_sprites[sprite].screen_space==screen_space)return;
    g_sprites[sprite].screen_space=screen_space;render(&g_sprites[sprite]);
}
void SPR_setFlipX(DmsSprite sprite,uint8_t flipped){
    if(sprite>=LOGICAL_SPR_COUNT || !g_sprites[sprite].used)return;
    flipped=flipped?1u:0u;if(g_sprites[sprite].flip_x==flipped)return;
    g_sprites[sprite].flip_x=flipped;render(&g_sprites[sprite]);
}
void SPR_setPalette(DmsSprite sprite,uint8_t palette_id){
    if(sprite>=LOGICAL_SPR_COUNT || !g_sprites[sprite].used)return;
    palette_id=(uint8_t)(palette_id&7u);if(g_sprites[sprite].palette_override==palette_id)return;
    g_sprites[sprite].palette_override=palette_id;render(&g_sprites[sprite]);
}
void dms_sprite_set_frame(uint16_t sprite,uint16_t frame_id){
    if(sprite>=LOGICAL_SPR_COUNT || !g_sprites[sprite].used || !g_sprites[sprite].dres || frame_id>=g_sprites[sprite].dres->frame_count)return;
    if(g_sprites[sprite].frame==frame_id)return;
    g_sprites[sprite].frame=frame_id;render(&g_sprites[sprite]);
}
uint16_t dms_sprite_get_frame(uint16_t sprite){
    return (sprite<LOGICAL_SPR_COUNT && g_sprites[sprite].used && g_sprites[sprite].dres)?g_sprites[sprite].frame:0xFFFFu;
}
const DmsDresResourceDesc *dms_sprite_desc_for_handle(uint16_t sprite){
    return (sprite<LOGICAL_SPR_COUNT && g_sprites[sprite].used)?g_sprites[sprite].dres:0;
}
void SPR_setAnimation(DmsSprite sprite,uint16_t animation_id){
    const DmsDresResourceDesc *r=dms_sprite_desc_for_handle(sprite);
    if(!r || animation_id>=r->animation_count)return;
    if(r->animations[animation_id].frame_count)dms_sprite_set_frame(sprite,r->animation_frame_ids[r->animations[animation_id].first_frame_index]);
}

uint16_t SPR_reservePatterns(uint16_t pattern_count){return dms_sprite_reserve_patterns(pattern_count);}
void SPR_uploadPatterns(uint16_t base_tile,const uint8_t* tile_data,uint16_t pattern_count){uint32_t i,total;if(!tile_data)return;total=(uint32_t)pattern_count*TILE_BYTES;for(i=0u;i<total;++i)vram8[(uint32_t)base_tile*TILE_BYTES+i]=tile_data[i];}
void SPR_uploadPalette(uint8_t palette_id,const uint16_t* rgb333_words){uint16_t i,p;if(!rgb333_words)return;p=(uint16_t)(palette_id&7u)*16u;for(i=0u;i<16u;++i)cram16[p+i]=(uint16_t)(rgb333_words[i]&0x01FFu);}
DmsSprite SPR_createRaw16(uint16_t tile_base,int16_t x,int16_t y,uint8_t palette_id,uint8_t priority){uint16_t slot;int16_t hw;for(slot=0u;slot<LOGICAL_SPR_COUNT;++slot)if(!g_sprites[slot].used)break;if(slot==LOGICAL_SPR_COUNT)return 0xFFFFu;hw=alloc_hw(1u);if(hw<0)return 0xFFFFu;g_sprites[slot].used=1u;g_sprites[slot].hw_first=(uint8_t)hw;g_sprites[slot].hw_count=1u;g_sprites[slot].legacy_mode=0u;g_sprites[slot].raw16_mode=1u;g_sprites[slot].tile_base=tile_base;g_sprites[slot].frame=0u;g_sprites[slot].visible=1u;g_sprites[slot].screen_space=1u;g_sprites[slot].priority_override=priority?1u:0u;g_sprites[slot].palette_override=(uint8_t)(palette_id&7u);g_sprites[slot].flip_x=0u;g_sprites[slot].x=x;g_sprites[slot].y=y;g_sprites[slot].dres=0;g_sprites[slot].legacy=0;render_raw16(&g_sprites[slot]);return slot;}
static void reload_dres_palette(const DmsDresResourceDesc *r){
    uint16_t i,k;
    if(!r)return;
    for(i=0u;i<r->palette_count;++i){uint16_t p=(uint16_t)r->palette_base+i;if(!dres_uses_palette(r,i))continue;if(p>=8u)break;for(k=0u;k<16u;++k)cram16[p*16u+k]=(uint16_t)(r->palettes[i*16u+k]&0x01FFu);}
}
static void reload_all_resources(uint8_t load_palette){
    uint16_t c,i,floor=dms_bg_pattern_floor(),limit=dms_bg_pattern_limit();uint8_t repack=0u;
    for(c=0u;c<TILE_CACHE_COUNT;++c)if(g_cache[c].used){const DmsDresResourceDesc*r=find_dres(g_cache[c].resource_id);if(r&&(g_cache[c].base<floor||(uint32_t)g_cache[c].base+r->tile_count>limit)){repack=1u;break;}}
    if(repack){
        for(c=0u;c<TILE_CACHE_COUNT;++c)g_cache[c].used=0u;
        g_next_tile=floor;if(g_next_tile<g_reserved_top)g_next_tile=g_reserved_top;
        for(i=0u;i<LOGICAL_SPR_COUNT;++i)if(g_sprites[i].used&&g_sprites[i].dres){uint16_t b=load_dres_tiles_ex(g_sprites[i].dres,load_palette);if(b!=0xFFFFu)g_sprites[i].tile_base=b;}
    }else{
        for(c=0u;c<TILE_CACHE_COUNT;++c)if(g_cache[c].used){const DmsDresResourceDesc*r=find_dres(g_cache[c].resource_id);if(r&&r->tiles){for(i=0u;i<(uint16_t)(r->tile_count*TILE_BYTES);++i)vram8[(uint32_t)g_cache[c].base*TILE_BYTES+i]=r->tiles[i];if(load_palette)reload_dres_palette(r);}}
    }
    for(i=0u;i<LOGICAL_SPR_COUNT;++i)if(g_sprites[i].used)render(&g_sprites[i]);
}
void SPR_reloadAllResources(void){reload_all_resources(1u);}
void SPR_reloadAllResourcesNoPalette(void){reload_all_resources(0u);}
void SPR_reloadAllPalettes(void){
    uint16_t c,i;
    for(c=0u;c<TILE_CACHE_COUNT;++c)if(g_cache[c].used)reload_dres_palette(find_dres(g_cache[c].resource_id));
    for(i=0u;i<LOGICAL_SPR_COUNT;++i)if(g_sprites[i].used&&g_sprites[i].legacy&&g_sprites[i].legacy->palette){uint16_t k,p=(uint16_t)((g_sprites[i].legacy->palette_id&7u)*16u);for(k=0u;k<16u;++k)cram16[p+k]=(uint16_t)(g_sprites[i].legacy->palette[k]&0x01FFu);}
}
uint16_t SPR_hwUsedCount(void){uint16_t i,n=0u;for(i=0u;i<HW_SPR_COUNT;++i)if(g_hw_used[i])++n;return n;}
uint16_t SPR_hwModeLimit(void){return hw_mode_limit();}
uint16_t SPR_hwAvailableCount(void){uint16_t used=SPR_hwUsedCount(),lim=hw_mode_limit();return used<lim?(uint16_t)(lim-used):0u;}
