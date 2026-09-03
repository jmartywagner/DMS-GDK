#include <stdint.h>
#include "dms1_hw.h"
#include "dms_vdp.h"
#include "dms_fx.h"

#define H 224u
#define LS_A 0x0C000u
#define LS_B 0x0C200u
#define MAX_COLORS 128u

typedef struct {
    uint8_t active;
    DmsFxId id;
    DmsFxParams p;
    uint16_t age, left, total;
    int8_t kx, ky;
} FxSlot;

static volatile uint8_t * const vdp = (volatile uint8_t*)DMS_VDP_BASE;
static volatile uint8_t * const vram8 = (volatile uint8_t*)DMS_VRAM_BASE;
static volatile uint16_t * const cram = (volatile uint16_t*)DMS_CRAM_BASE;

static uint8_t g_mode;
static FxSlot g_slot[DMS_FX_STACK_MAX];
static uint8_t g_count;
static int16_t g_last_ax,g_last_ay,g_last_bx,g_last_by;
static uint16_t g_pal_base[MAX_COLORS];
static uint8_t g_pal_saved;
static uint8_t g_pal_touched;
static uint8_t g_black_hold;
static uint16_t g_lfsr=0xB4D3u;
static DmsFxRasterZone g_zone[DMS_FX_RASTER_ZONES_MAX];
static uint8_t g_zone_used[DMS_FX_RASTER_ZONES_MAX];
static uint8_t g_raster_on;

static const int8_t sin64[64]={
0,12,25,37,49,60,71,81,90,98,106,112,118,122,125,127,
127,127,125,122,118,112,106,98,90,81,71,60,49,37,25,12,
0,-12,-25,-37,-49,-60,-71,-81,-90,-98,-106,-112,-118,-122,-125,-127,
-127,-127,-125,-122,-118,-112,-106,-98,-90,-81,-71,-60,-49,-37,-25,-12};

static uint16_t rnd(void){uint16_t b=(uint16_t)(((g_lfsr>>0)^(g_lfsr>>2)^(g_lfsr>>3)^(g_lfsr>>5))&1u);g_lfsr=(uint16_t)((g_lfsr>>1)|(b<<15));return g_lfsr;}
static int16_t clamp_line(int16_t v){if(v>63)return 63;if(v<-63)return -63;return v;}
static int16_t wave(uint16_t phase,uint8_t amp){return (int16_t)(((int16_t)sin64[phase&63u]*(int16_t)amp)/127);}
static void wr16(uint32_t off,int16_t x){uint16_t u=(uint16_t)x;vram8[off]=(uint8_t)(u>>8);vram8[off+1u]=(uint8_t)u;}
static void line_clear(void){uint16_t y;for(y=0;y<H;++y){wr16(LS_A+(uint32_t)y*2u,0);wr16(LS_B+(uint32_t)y*2u,0);}}
static void scroll_write(int16_t ax,int16_t ay,int16_t bx,int16_t by){uint16_t x;x=((uint16_t)ax)&0x01FFu;vdp[0x10]=(uint8_t)(x>>8);vdp[0x11]=(uint8_t)x;x=((uint16_t)ay)&0x00FFu;vdp[0x12]=(uint8_t)(x>>8);vdp[0x13]=(uint8_t)x;x=((uint16_t)bx)&0x01FFu;vdp[0x14]=(uint8_t)(x>>8);vdp[0x15]=(uint8_t)x;x=((uint16_t)by)&0x00FFu;vdp[0x16]=(uint8_t)(x>>8);vdp[0x17]=(uint8_t)x;}
static uint16_t pack(uint8_t r,uint8_t g,uint8_t b){return (uint16_t)(((r&7u)<<6)|((g&7u)<<3)|(b&7u));}
static uint16_t mix(uint16_t a,uint16_t b,uint8_t t){uint8_t ar=(uint8_t)((a>>6)&7u),ag=(uint8_t)((a>>3)&7u),ab=(uint8_t)(a&7u);uint8_t br=(uint8_t)((b>>6)&7u),bg=(uint8_t)((b>>3)&7u),bb=(uint8_t)(b&7u);uint8_t r=(uint8_t)(((uint16_t)ar*(15u-t)+(uint16_t)br*t+7u)/15u);uint8_t g=(uint8_t)(((uint16_t)ag*(15u-t)+(uint16_t)bg*t+7u)/15u);uint8_t bl=(uint8_t)(((uint16_t)ab*(15u-t)+(uint16_t)bb*t+7u)/15u);return pack(r,g,bl);}
static uint16_t invert(uint16_t c){return pack((uint8_t)(7u-((c>>6)&7u)),(uint8_t)(7u-((c>>3)&7u)),(uint8_t)(7u-(c&7u)));}
static uint16_t gray(uint16_t c){uint8_t v=(uint8_t)((((c>>6)&7u)+((c>>3)&7u)+(c&7u)+1u)/3u);return pack(v,v,v);}
static uint8_t default_mask(void){return (uint8_t)((g_mode==DMS_MODE_HIGH_COLOR||g_mode==DMS_MODE_LOW_RES)?0xFFu:0x0Fu);}
static uint8_t is_palette(DmsFxId id){return (uint8_t)((id>=DMS_FX_FLASH&&id<=DMS_FX_COLOR_CYCLE)||(id>=DMS_FX_PALETTE_INVERT&&id<=DMS_FX_HIT_FREEZE_VISUAL));}
static uint8_t is_line(DmsFxId id){return (uint8_t)((id>=DMS_FX_WATER_WAVE&&id<=DMS_FX_SPEED_BANDS)||(id>=DMS_FX_EARTHQUAKE_RASTER&&id<=DMS_FX_UNDERWATER_DRIFT));}
static uint8_t is_bg2(DmsFxId id){return (uint8_t)(id==DMS_FX_BG_PARALLAX_OSC||id==DMS_FX_PARALLAX_KICK||id==DMS_FX_BG_DEPTH_SWAY);}
static uint16_t defdur(DmsFxId id){switch(id){case DMS_FX_SHAKE:return 36;case DMS_FX_KICK:return 14;case DMS_FX_FLASH:return 10;case DMS_FX_FADE_OUT:case DMS_FX_FADE_IN:return 45;case DMS_FX_HIT_FREEZE_VISUAL:return 8;case DMS_FX_PARALLAX_KICK:return 18;case DMS_FX_PALETTE_STROBE:return 120;case DMS_FX_PALETTE_INVERT:case DMS_FX_PALETTE_TINT:case DMS_FX_PALETTE_DESATURATE:return 90;default:return 240;}}
static uint8_t slot_env(const FxSlot* s){uint16_t a=s->p.attack,h=s->p.hold,r=s->p.release;if((a|h|r)==0u)return 15u;if(s->age<a)return (uint8_t)(a?(s->age*15u/a):15u);if(s->age<(uint16_t)(a+h))return 15u;if(s->age<(uint16_t)(a+h+r)){uint16_t d=(uint16_t)(s->age-a-h);return (uint8_t)(r?((r-d)*15u/r):0u);}return 0u;}
static uint8_t amp_env(const FxSlot* s){return (uint8_t)((uint16_t)s->p.intensity*slot_env(s)/15u);}
static void pal_capture(void){uint16_t i;if(g_pal_saved)return;for(i=0;i<MAX_COLORS;++i)g_pal_base[i]=(uint16_t)(cram[i]&0x01FFu);g_pal_saved=1u;}
static void pal_restore(void){uint16_t i;if(!g_pal_saved){g_black_hold=0u;return;}for(i=0;i<MAX_COLORS;++i)if(g_pal_touched&(1u<<(i>>4)))cram[i]=g_pal_base[i];g_pal_saved=0u;g_pal_touched=0u;g_black_hold=0u;}
static void remove_slot(uint8_t i){uint8_t j;if(!g_slot[i].active)return;for(j=i;j+1u<DMS_FX_STACK_MAX;++j)g_slot[j]=g_slot[j+1u];g_slot[DMS_FX_STACK_MAX-1u].active=0u;if(g_count)--g_count;}

void FX_init(uint8_t mode){uint8_t i;g_mode=mode;g_count=0u;g_pal_saved=0u;g_pal_touched=0u;g_black_hold=0u;g_raster_on=0u;g_last_ax=g_last_ay=g_last_bx=g_last_by=0;for(i=0;i<DMS_FX_STACK_MAX;++i)g_slot[i].active=0u;for(i=0;i<DMS_FX_RASTER_ZONES_MAX;++i)g_zone_used[i]=0u;if(mode==DMS_MODE_SCROLL)line_clear();}
void FX_setMode(uint8_t mode){FX_stop();g_mode=mode;}
void FX_setModeKeepBlack(uint8_t mode){uint8_t i;if(!g_pal_saved){FX_holdBlack(default_mask());}else{for(i=0u;i<DMS_FX_STACK_MAX;++i)g_slot[i].active=0u;g_count=0u;g_black_hold=1u;}g_mode=mode;if(mode==DMS_MODE_SCROLL)line_clear();}
void FX_holdBlack(uint8_t mask){uint16_t i;uint8_t s;if(mask==0u)mask=default_mask();for(s=0u;s<DMS_FX_STACK_MAX;++s)g_slot[s].active=0u;g_count=0u;g_pal_saved=1u;g_pal_touched=mask;g_black_hold=1u;for(i=0u;i<MAX_COLORS;++i){g_pal_base[i]=(uint16_t)(cram[i]&0x01FFu);if(mask&(1u<<(i>>4)))cram[i]=0u;}if(g_mode==DMS_MODE_SCROLL)line_clear();}
uint8_t FX_getMode(void){return g_mode;}
uint8_t FX_isCompatible(DmsFxId id,uint8_t mode){if(id==DMS_FX_NONE||id==DMS_FX_SHAKE||id==DMS_FX_KICK||is_palette(id))return 1u;if(is_line(id))return (uint8_t)(mode==DMS_MODE_SCROLL);if(is_bg2(id))return (uint8_t)(mode==DMS_MODE_STANDARD||mode==DMS_MODE_SCROLL||mode==DMS_MODE_LOW_RES);return 0u;}
const char* FX_name(DmsFxId id){static const char* const n[DMS_FX_COUNT]={"NONE","SHAKE","CAMERA KICK","FLASH","FADE OUT","FADE IN","LUMA PULSE","COLOR CYCLE","WATER WAVE","SINE RIPPLE","HEAT HAZE","SHEAR WOBBLE","RASTER SPLIT","SCAN SWEEP","SPEED BANDS","BG A/B OSC","PALETTE INVERT","PALETTE TINT","DESATURATE","PALETTE STROBE","HIT FREEZE VISUAL","EARTHQUAKE RASTER","PERSPECTIVE WARP","UNDERWATER DRIFT","PARALLAX KICK","BG DEPTH SWAY"};return (id<DMS_FX_COUNT)?n[id]:"?";}

static uint8_t add_slot(DmsFxId id,const DmsFxParams* p){DmsFxParams q={8u,5u,0u,0u,0x01FFu,0u,1u,15u,0u,0u,0u};FxSlot* s;if(g_count>=DMS_FX_STACK_MAX||id<=DMS_FX_NONE||id>=DMS_FX_COUNT||!FX_isCompatible(id,g_mode))return 0u;if(p)q=*p;if(q.intensity>15u)q.intensity=15u;if(q.secondary>15u)q.secondary=15u;if(q.palette_mask==0u)q.palette_mask=default_mask();s=&g_slot[g_count++];s->active=1u;s->id=id;s->p=q;s->age=0u;s->total=q.duration?q.duration:defdur(id);if((q.attack|q.hold|q.release)!=0u){uint16_t e=(uint16_t)q.attack+q.hold+q.release;if(e>s->total)s->total=e;}s->left=s->total;s->kx=(int8_t)((q.secondary&1u)?-1:1);s->ky=(int8_t)((q.secondary&2u)?-1:1);if(is_palette(id)){pal_capture();g_pal_touched|=q.palette_mask;}return 1u;}
uint8_t FX_start(DmsFxId id,const DmsFxParams* p){if(id==DMS_FX_FADE_IN&&g_black_hold){uint8_t i;for(i=0u;i<DMS_FX_STACK_MAX;++i)g_slot[i].active=0u;g_count=0u;return add_slot(id,p);}FX_stop();return add_slot(id,p);}
uint8_t FX_stackAdd(DmsFxId id,const DmsFxParams* p){return add_slot(id,p);}
void FX_stackClear(void){FX_stop();}
uint8_t FX_stackCount(void){return g_count;}
uint8_t FX_stackContains(DmsFxId id){uint8_t i;for(i=0;i<g_count;++i)if(g_slot[i].active&&g_slot[i].id==id)return 1u;return 0u;}
void FX_stop(void){uint8_t i;pal_restore();for(i=0;i<DMS_FX_STACK_MAX;++i)g_slot[i].active=0u;g_count=0u;if(g_mode==DMS_MODE_SCROLL)line_clear();scroll_write(g_last_ax,g_last_ay,g_last_bx,g_last_by);}
void FX_reset(void){FX_stop();FX_rasterComposerClear();if(g_mode==DMS_MODE_SCROLL)line_clear();}
uint8_t FX_active(void){return (uint8_t)(g_count!=0u||g_raster_on);}
DmsFxId FX_current(void){return g_count?g_slot[0].id:DMS_FX_NONE;}
uint16_t FX_framesLeft(void){return g_count?g_slot[0].left:0u;}

uint8_t FX_envelopeValue(const DmsFxEnvelope* e,uint16_t age){uint16_t a,h,r;if(!e)return 0u;a=e->attack;h=e->hold;r=e->release;if(age<a)return (uint8_t)(a?((uint32_t)age*e->peak/a):e->peak);if(age<(uint16_t)(a+h))return e->peak;if(age<(uint16_t)(a+h+r)){uint16_t d=(uint16_t)(age-a-h);return (uint8_t)(r?((uint32_t)(r-d)*e->peak/r):0u);}return 0u;}
void FX_rasterComposerClear(void){uint8_t i;for(i=0;i<DMS_FX_RASTER_ZONES_MAX;++i)g_zone_used[i]=0u;g_raster_on=0u;if(g_mode==DMS_MODE_SCROLL&&g_count==0u)line_clear();}
uint8_t FX_rasterComposerSet(uint8_t i,const DmsFxRasterZone* z){if(i>=DMS_FX_RASTER_ZONES_MAX||!z||z->y0>=H||z->y1>=H||z->y1<z->y0)return 0u;g_zone[i]=*z;g_zone_used[i]=1u;return 1u;}
void FX_rasterComposerEnable(uint8_t e){g_raster_on=(uint8_t)(e&&g_mode==DMS_MODE_SCROLL);if(!g_raster_on&&g_mode==DMS_MODE_SCROLL&&g_count==0u)line_clear();}
uint8_t FX_rasterComposerEnabled(void){return g_raster_on;}
uint8_t FX_hitFreezeRequested(void){uint8_t i;for(i=0;i<g_count;++i)if(g_slot[i].id==DMS_FX_HIT_FREEZE_VISUAL&&g_slot[i].age<(uint16_t)(1u+g_slot[i].p.secondary/3u))return 1u;return 0u;}

static uint16_t palette_apply_one(const FxSlot* s,uint16_t base,uint16_t current,uint16_t idx){uint8_t t=amp_env(s),step;uint16_t half;if(!(s->p.palette_mask&(1u<<(idx>>4))))return current;switch(s->id){case DMS_FX_FLASH:half=(s->total+1u)/2u;if(s->age<half)t=(uint8_t)((s->age*15u)/half);else t=(uint8_t)(((s->total-s->age)*15u)/half);return mix(current,s->p.color,t);case DMS_FX_FADE_OUT:t=(uint8_t)(s->total?((uint32_t)s->age*15u/s->total):15u);if(t>15u)t=15u;return mix(current,0u,t);case DMS_FX_FADE_IN:t=(uint8_t)(s->total?((uint32_t)s->age*15u/s->total):15u);if(t>15u)t=15u;return mix(0u,base,t);case DMS_FX_PULSE:t=(uint8_t)(((wave((uint16_t)(s->age*(1u+s->p.secondary/4u)),7)+7)*s->p.intensity)/14u);return mix(current,0x01FFu,t);case DMS_FX_PALETTE_INVERT:return mix(current,invert(base),t);case DMS_FX_PALETTE_TINT:return mix(current,s->p.color,t);case DMS_FX_PALETTE_DESATURATE:return mix(current,gray(base),t);case DMS_FX_PALETTE_STROBE:step=(uint8_t)(2u+(15u-s->p.secondary));return ((s->age/step)&1u)?mix(current,s->p.color,t):current;case DMS_FX_HIT_FREEZE_VISUAL:if(s->age<3u)return mix(current,0x01FFu,(uint8_t)(15u-s->age*5u));return current;default:return current;}}
static void palette_render(void){uint16_t i;uint8_t s;uint16_t c;if(!g_pal_saved)return;for(i=0;i<MAX_COLORS;++i){if(!(g_pal_touched&(1u<<(i>>4))))continue;c=g_pal_base[i];for(s=0;s<g_count;++s){if(!is_palette(g_slot[s].id))continue;if(g_slot[s].id==DMS_FX_COLOR_CYCLE){uint8_t pal=(uint8_t)(g_slot[s].p.palette&7u),first=(uint8_t)(g_slot[s].p.first_color&15u),count=g_slot[s].p.color_count,j,step;if(count<2u)count=2u;if(first+count>16u)count=(uint8_t)(16u-first);if((i>>4)==pal&&i>=((uint16_t)pal*16u+first)&&i<((uint16_t)pal*16u+first+count)){j=(uint8_t)(i-((uint16_t)pal*16u+first));step=(uint8_t)(g_slot[s].age/(1u+(15u-g_slot[s].p.intensity)));c=g_pal_base[(uint16_t)pal*16u+first+((j+count-(step%count))%count)];}}else c=palette_apply_one(&g_slot[s],g_pal_base[i],c,i);}cram[i]=c;}}

static void line_contrib(const FxSlot* s,uint16_t y,int16_t* a,int16_t* b){uint8_t amp=(uint8_t)(1u+amp_env(s)*2u),freq=(uint8_t)(1u+s->p.secondary);int16_t x=0,z=0;if(y<48u)return;switch(s->id){case DMS_FX_WATER_WAVE:{uint16_t surface=(uint16_t)(70u-s->p.secondary*2u);if(y>surface){uint16_t d=y-surface;x=wave((uint16_t)(y*freq/3u+s->age*3u),(uint8_t)((amp*d)/(H-surface)));z=wave((uint16_t)(y*freq/4u+s->age*2u),(uint8_t)((amp*d)/(H-surface)/2u));}}break;case DMS_FX_RIPPLE:x=wave((uint16_t)(y*freq/2u+s->age*3u),amp);z=(int16_t)-wave((uint16_t)(y*freq/3u+s->age*2u),(uint8_t)(amp/2u));break;case DMS_FX_HEAT_HAZE:if(y>80u){x=(int16_t)(wave((uint16_t)(y*(2u+freq)+s->age*5u),amp)/2+(int16_t)((rnd()&3u)-1));z=(int16_t)-x/3;}break;case DMS_FX_SHEAR_WOBBLE:x=(int16_t)(((int32_t)((int16_t)y-112)*(int32_t)s->p.intensity)/56+wave((uint16_t)(y+s->age*2u),(uint8_t)(s->p.secondary+1u)));z=(int16_t)-x/2;break;case DMS_FX_RASTER_SPLIT:{uint8_t band=(uint8_t)(y/(8u+(15u-s->p.secondary)));x=(int16_t)((band&1u)?amp:-amp);x=(int16_t)(x+wave((uint16_t)(s->age*2u+band*5u),(uint8_t)(amp/3u)));z=(int16_t)-x/2;}break;case DMS_FX_SCAN_SWEEP:{uint16_t pos=(uint16_t)((s->age*3u)%(H+48u));int16_t dy=(int16_t)y-(int16_t)pos;if(dy<0)dy=(int16_t)-dy;if(dy<24)x=(int16_t)((24-dy)*amp/24);z=(int16_t)-x/2;}break;case DMS_FX_SPEED_BANDS:{uint8_t band=(uint8_t)(y>>3);int16_t dir=(band&1u)?1:-1;int16_t mag=(int16_t)((band%6u)*(1u+s->p.intensity/3u));x=(int16_t)(dir*(mag+(int16_t)(s->age*(1u+s->p.secondary/4u))));z=(int16_t)-x/3;}break;case DMS_FX_EARTHQUAKE_RASTER:{uint8_t depth=(uint8_t)((y-48u)*15u/(H-48u));uint8_t qa=(uint8_t)(1u+(amp*depth/15u));uint16_t h=(uint16_t)(s->age*37u+(y>>3)*73u);x=(int16_t)((int16_t)((h^(h>>5))%(2u*qa+1u))-qa);z=(int16_t)-x/2;}break;case DMS_FX_PERSPECTIVE_WARP:{uint8_t depth=(uint8_t)((y-48u)*15u/(H-48u));uint8_t pa=(uint8_t)(1u+amp*depth/15u);x=wave((uint16_t)(s->age*(1u+s->p.secondary/3u)+y/3u),pa);z=(int16_t)-wave((uint16_t)(s->age+y/5u),(uint8_t)(pa/3u));}break;case DMS_FX_UNDERWATER_DRIFT:{int16_t w1=wave((uint16_t)(y/2u+s->age*(1u+s->p.secondary/4u)),amp);int16_t w2=wave((uint16_t)(y*3u/5u+s->age/2u+17u),(uint8_t)(amp/2u));x=(int16_t)(w1+w2);z=(int16_t)(-w1/3+wave((uint16_t)(y/4u+s->age), (uint8_t)(amp/3u)));}break;default:break;}*a=(int16_t)(*a+x);*b=(int16_t)(*b+z);}
static void raster_contrib(uint16_t y,uint16_t age,int16_t* a,int16_t* b){uint8_t i;for(i=0;i<DMS_FX_RASTER_ZONES_MAX;++i)if(g_zone_used[i]&&y>=g_zone[i].y0&&y<=g_zone[i].y1){int16_t wa=0;if(g_zone[i].wave_amp)wa=wave((uint16_t)(age*g_zone[i].wave_speed+y),g_zone[i].wave_amp);*a=(int16_t)(*a+g_zone[i].offset_a+wa);*b=(int16_t)(*b+g_zone[i].offset_b-wa/2);}}

void FX_update(int16_t ax,int16_t ay,int16_t bx,int16_t by){uint8_t i;int16_t ox=0,oy=0,obx=0,oby=0;uint16_t line_age=0u;g_last_ax=ax;g_last_ay=ay;g_last_bx=bx;g_last_by=by;if(g_count==0u&&!g_raster_on){scroll_write(ax,ay,bx,by);return;}for(i=0;i<g_count;++i){FxSlot* s=&g_slot[i];uint8_t amp=amp_env(s);if(s->age>line_age)line_age=s->age;switch(s->id){case DMS_FX_SHAKE:{uint16_t scale=s->total?((uint32_t)s->left*255u/s->total):0u;uint8_t a=(uint8_t)((amp*scale)/255u);ox=(int16_t)(ox+(int16_t)(rnd()%(2u*a+1u))-a);oy=(int16_t)(oy+(int16_t)(rnd()%(2u*a+1u))-a);}break;case DMS_FX_KICK:{uint16_t sc=s->total?((uint32_t)s->left*15u/s->total):0u;ox=(int16_t)(ox+s->kx*(int16_t)s->p.intensity*(int16_t)sc/4);oy=(int16_t)(oy+s->ky*(int16_t)s->p.intensity*(int16_t)sc/6);}break;case DMS_FX_BG_PARALLAX_OSC:ox=(int16_t)(ox+wave((uint16_t)(s->age*(2u+s->p.secondary/3u)),amp));obx=(int16_t)(obx-wave((uint16_t)(s->age*(1u+s->p.secondary/4u)),(uint8_t)(amp*2u)));oy=(int16_t)(oy+wave(s->age,(uint8_t)(amp/3u)));oby=(int16_t)(oby-wave(s->age,(uint8_t)(amp/3u)));break;case DMS_FX_PARALLAX_KICK:{uint16_t sc=s->total?((uint32_t)s->left*15u/s->total):0u;int16_t k=(int16_t)(s->kx*(int16_t)s->p.intensity*(int16_t)sc/3);ox=(int16_t)(ox+k);obx=(int16_t)(obx-k*2);oy=(int16_t)(oy+s->ky*k/3);oby=(int16_t)(oby-s->ky*k/6);}break;case DMS_FX_BG_DEPTH_SWAY:ox=(int16_t)(ox+wave((uint16_t)(s->age*(1u+s->p.secondary/5u)),amp));obx=(int16_t)(obx+wave((uint16_t)(s->age/2u+21u),(uint8_t)(amp*2u)));oy=(int16_t)(oy+wave((uint16_t)(s->age/3u),(uint8_t)(amp/4u)));oby=(int16_t)(oby-wave((uint16_t)(s->age/4u),(uint8_t)(amp/2u)));break;case DMS_FX_HIT_FREEZE_VISUAL:if(s->age<4u){int16_t k=(int16_t)((4u-s->age)*s->p.intensity/2u);ox=(int16_t)(ox+((s->age&1u)?-k:k));}break;default:break;}}
palette_render();if(g_mode==DMS_MODE_SCROLL){uint8_t need_line=g_raster_on;uint16_t y;if(!need_line){for(i=0u;i<g_count;++i)if(is_line(g_slot[i].id)){need_line=1u;break;}}if(need_line){for(y=0;y<H;++y){int16_t la=0,lb=0;for(i=0;i<g_count;++i)if(is_line(g_slot[i].id))line_contrib(&g_slot[i],y,&la,&lb);if(g_raster_on)raster_contrib(y,line_age,&la,&lb);wr16(LS_A+(uint32_t)y*2u,clamp_line(la));wr16(LS_B+(uint32_t)y*2u,clamp_line(lb));}}}
scroll_write((int16_t)(ax+ox),(int16_t)(ay+oy),(int16_t)(bx+obx),(int16_t)(by+oby));
/* Advance/remove finite effects. Fade-out holds its final black state in classic API. */
i=0u;while(i<g_count){FxSlot* s=&g_slot[i];++s->age;if(s->left)--s->left;if(s->left==0u){if(s->id==DMS_FX_FADE_OUT&&g_count==1u){s->age=s->total;++i;continue;}remove_slot(i);continue;}++i;}if(g_pal_saved){uint8_t anyp=0u;for(i=0;i<g_count;++i)if(is_palette(g_slot[i].id)){anyp=1u;break;}if(!anyp)pal_restore();}if(g_count==0u&&!g_raster_on&&g_mode==DMS_MODE_SCROLL)line_clear();}

uint8_t FX_shake(uint8_t a,uint16_t d,uint8_t att){DmsFxParams p={a,att,d,0,0,0,0,0,0,0,0};return FX_start(DMS_FX_SHAKE,&p);}
uint8_t FX_kick(int8_t dx,int8_t dy,uint8_t a,uint16_t d){DmsFxParams p={a,(uint8_t)((dx<0?1u:0u)|(dy<0?2u:0u)),d,0,0,0,0,0,0,0,0};return FX_start(DMS_FX_KICK,&p);}
uint8_t FX_flash(uint16_t c,uint8_t m,uint16_t d){DmsFxParams p={15,0,d,m,c,0,0,0,0,0,0};return FX_start(DMS_FX_FLASH,&p);}
uint8_t FX_fadeOut(uint8_t m,uint16_t d){DmsFxParams p={15,0,d,m,0,0,0,0,0,0,0};return FX_start(DMS_FX_FADE_OUT,&p);}
uint8_t FX_fadeIn(uint8_t m,uint16_t d){DmsFxParams p={15,0,d,m,0,0,0,0,0,0,0};return FX_start(DMS_FX_FADE_IN,&p);}
