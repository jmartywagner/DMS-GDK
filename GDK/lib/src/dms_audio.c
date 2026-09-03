#include <stdint.h>
#include "dms1_hw.h"
#include "dms_audio.h"
#include "dms_resource_runtime.h"

#define AUDIO_VOICES 9u
#define AUDIO_QUEUE 8u
#define AUDIO_PROGRAM_MAX 48u
#define AUDIO_RESTORE_BASE 0x1800u
#define AUDIO_RESTORE_STRIDE 96u
#define AUDIO_DUCK_SLOT 9u
#define AUDIO_STATUS_IDLE 0x80u
#define AUDIO_STATUS_MUSIC 0xC2u

typedef struct {uint8_t active,priority,kind,restore_count,duck;uint16_t sfx,frames;} AudioVoice;
static AudioVoice g_voice[AUDIO_VOICES];
static uint16_t g_queue[AUDIO_QUEUE];static uint8_t g_qread,g_qwrite,g_qcount;
static uint8_t g_music_active,g_duck_steps,g_music_volume=255u,g_sfx_volume=255u;
static uint8_t g_music_pending;
static uint16_t g_current_music_id=0xFFFFu;
static uint16_t g_music_start_bank=0u;
static uint8_t g_active_music_priorities[AUDIO_VOICES]={50u,50u,50u,50u,50u,50u,50u,50u,50u};
static volatile uint8_t * const mail=(volatile uint8_t*)DMS_MAILBOX_BASE;

static uint8_t wait_mail(void){uint32_t guard=200000u;while(mail[0]&&guard)--guard;return guard?1u:0u;}
static const DmsMusicResourceDesc *find_music(uint16_t id){uint16_t i;for(i=0u;i<dms_music_resource_count;++i)if(dms_music_resources[i].id==id)return &dms_music_resources[i];return 0;}
static const DmsSfxResourceDesc *find_sfx(uint16_t id){uint16_t i;for(i=0u;i<dms_sfx_resource_count;++i)if(dms_sfx_resources[i].id==id)return &dms_sfx_resources[i];return 0;}
static uint16_t restore_address(uint8_t slot){return (uint16_t)(AUDIO_RESTORE_BASE+(uint16_t)slot*AUDIO_RESTORE_STRIDE);}
static uint16_t relocate_address(uint16_t address,uint8_t kind,uint8_t channel){
    if(kind==DMS_SFX_FM){
        if(address==0x0020u||address==0x0028u||address==0x0030u||address==0x0038u)return (uint16_t)(address+channel);
        if(address>=0x0040u&&address<=0x00FFu&&(address&7u)==0u)return (uint16_t)(address+channel);
    }else if(kind==DMS_SFX_SSG){
        if(address==0x0100u||address==0x0101u)return (uint16_t)(address+(uint16_t)channel*2u);
        if(address==0x0108u)return (uint16_t)(address+channel);
    }
    return address;
}
static uint8_t relocate_value(uint16_t address,uint8_t value,uint8_t kind,uint8_t channel){
    if(kind==DMS_SFX_SSG&&address==0x0107u){uint8_t mixer=0x3Fu;if((value&1u)==0u)mixer=(uint8_t)(mixer&~(1u<<channel));if((value&8u)==0u)mixer=(uint8_t)(mixer&~(1u<<(channel+3u)));return mixer;}
    return value;
}
static uint8_t send_program(const DmsAudioRegWrite *program,uint8_t count,uint8_t kind,uint8_t channel,uint8_t restore_slot){
    uint8_t i;uint16_t ptr;if(!count||count>AUDIO_PROGRAM_MAX||!wait_mail())return 0u;ptr=restore_address(restore_slot);mail[13]=count;mail[14]=(uint8_t)ptr;mail[15]=(uint8_t)(ptr>>8);
    for(i=0u;i<count;++i){uint16_t a=relocate_address(program[i].address,kind,channel);uint8_t v=relocate_value(program[i].address,program[i].value,kind,channel);mail[16u+i*3u]=(uint8_t)(a>>8);mail[17u+i*3u]=(uint8_t)a;mail[18u+i*3u]=v;}
    mail[0]=4u;return 1u;
}
static uint8_t send_restore(uint8_t slot,uint8_t count){uint16_t ptr;if(!count||!wait_mail())return 0u;ptr=restore_address(slot);mail[13]=count;mail[14]=(uint8_t)ptr;mail[15]=(uint8_t)(ptr>>8);mail[0]=5u;return 1u;}
static uint8_t sample_program(const DmsSfxResourceDesc *s,DmsAudioRegWrite *p){
    if(s->kind==DMS_SFX_SAMPLE_A){p[0]=(DmsAudioRegWrite){0x0120u,2u};p[1]=(DmsAudioRegWrite){0x0121u,s->pan};p[2]=(DmsAudioRegWrite){0x0122u,(uint8_t)(s->level&31u)};p[3]=(DmsAudioRegWrite){0x0124u,(uint8_t)s->start_page};p[4]=(DmsAudioRegWrite){0x0125u,(uint8_t)(s->start_page>>8)};p[5]=(DmsAudioRegWrite){0x0126u,(uint8_t)s->end_page};p[6]=(DmsAudioRegWrite){0x0127u,(uint8_t)(s->end_page>>8)};p[7]=(DmsAudioRegWrite){0x0120u,1u};return 8u;}
    {uint16_t delta=(uint16_t)(((uint32_t)s->rate_hz*65536u)/55556u);if(!delta)delta=1u;p[0]=(DmsAudioRegWrite){0x0140u,1u};p[1]=(DmsAudioRegWrite){0x0141u,s->pan};p[2]=(DmsAudioRegWrite){0x0142u,(uint8_t)s->start_page};p[3]=(DmsAudioRegWrite){0x0143u,(uint8_t)(s->start_page>>8)};p[4]=(DmsAudioRegWrite){0x0144u,(uint8_t)s->end_page};p[5]=(DmsAudioRegWrite){0x0145u,(uint8_t)(s->end_page>>8)};p[6]=(DmsAudioRegWrite){0x0149u,(uint8_t)delta};p[7]=(DmsAudioRegWrite){0x014Au,(uint8_t)(delta>>8)};p[8]=(DmsAudioRegWrite){0x014Bu,s->level};p[9]=(DmsAudioRegWrite){0x0140u,0x80u};return 10u;}
}
static uint8_t voice_base_priority(uint8_t slot){return g_voice[slot].active?g_voice[slot].priority:(g_music_active?g_active_music_priorities[slot]:0u);}
static int16_t choose_voice(const DmsSfxResourceDesc *s){
    uint8_t first=0u,last=0u,i,best=0xFFu,bestp=255u;if(s->kind==DMS_SFX_FM){first=0u;last=3u;}else if(s->kind==DMS_SFX_SSG){first=4u;last=6u;}else if(s->kind==DMS_SFX_SAMPLE_A){first=last=7u;}else if(s->kind==DMS_SFX_SAMPLE_B){first=last=8u;}else return -1;
    if(s->target){if(s->kind==DMS_SFX_FM){first=last=(uint8_t)(s->target-1u);}else if(s->kind==DMS_SFX_SSG){first=last=(uint8_t)(4u+s->target-1u);}}
    for(i=first;i<=last;++i){uint8_t p=voice_base_priority(i);if(!g_voice[i].active&&!g_music_active)return i;if(s->conflict==DMS_AUDIO_FORCE)return i;if(s->priority>=p&&p<bestp){best=i;bestp=p;}}
    return best==0xFFu?-1:(int16_t)best;
}
static void stop_voice(uint8_t slot){if(slot>=AUDIO_VOICES||!g_voice[slot].active)return;send_restore(slot,g_voice[slot].restore_count);g_voice[slot].active=0u;g_voice[slot].restore_count=0u;}
static uint8_t queue_sfx(uint16_t id){if(g_qcount>=AUDIO_QUEUE)return 0u;g_queue[g_qwrite]=id;g_qwrite=(uint8_t)((g_qwrite+1u)%AUDIO_QUEUE);++g_qcount;return 1u;}
static uint8_t play_internal(uint16_t id,uint8_t depth){
    const DmsSfxResourceDesc *s=find_sfx(id);int16_t slot;uint8_t count;DmsAudioRegWrite sample[10];if(!s||depth>4u)return 0u;
    if(s->kind==DMS_SFX_COMPOSITE){uint16_t i;uint8_t played=0u;for(i=0u;i<s->composite_count&&s->composite_first+i<dms_sfx_composite_member_count;++i)played|=play_internal(dms_sfx_composite_members[s->composite_first+i],(uint8_t)(depth+1u));return played;}
    slot=choose_voice(s);if(slot<0){if(s->conflict==DMS_AUDIO_QUEUE)return queue_sfx(id);return 0u;}if(g_voice[slot].active){if(s->conflict==DMS_AUDIO_IGNORE)return 0u;stop_voice((uint8_t)slot);}
    if(s->kind==DMS_SFX_SAMPLE_A||s->kind==DMS_SFX_SAMPLE_B){count=sample_program(s,sample);if(!send_program(sample,count,s->kind,0u,(uint8_t)slot))return 0u;}else{if(s->program_first+s->program_count>dms_sfx_program_count||s->program_count>AUDIO_PROGRAM_MAX)return 0u;count=(uint8_t)s->program_count;if(!send_program(&dms_sfx_program[s->program_first],count,s->kind,(uint8_t)(s->kind==DMS_SFX_FM?slot:slot-4),(uint8_t)slot))return 0u;}
    g_voice[slot].active=1u;g_voice[slot].priority=s->priority;g_voice[slot].kind=s->kind;g_voice[slot].restore_count=count;g_voice[slot].duck=s->duck_steps;g_voice[slot].sfx=id;g_voice[slot].frames=s->duration_frames;return 1u;
}
static void update_duck(void){
    uint8_t i,wanted=0u;DmsAudioRegWrite p[2];for(i=0u;i<AUDIO_VOICES;++i)if(g_voice[i].active&&g_voice[i].duck>wanted)wanted=g_voice[i].duck;if(wanted==g_duck_steps)return;
    if(!wanted){send_restore(AUDIO_DUCK_SLOT,2u);g_duck_steps=0u;return;}
    p[0]=(DmsAudioRegWrite){0x0188u,(uint8_t)(12u+wanted>63u?63u:12u+wanted)};p[1]=(DmsAudioRegWrite){0x0189u,(uint8_t)(20u+wanted>63u?63u:20u+wanted)};if(send_program(p,2u,0u,0u,AUDIO_DUCK_SLOT))g_duck_steps=wanted;
}
/* Le 68000 peut demander la musique pendant sa toute premiere tranche CPU,
   avant que le firmware Z80 ait publie AUDIO_STATUS_IDLE. L'ancien code
   envoyait PLAY immediatement ; l'initialisation Z80 effacait alors la
   commande et le jeu restait silencieux. La requete est maintenant conservee
   puis servie des que le Z80 est pret. Le meme chemin rend aussi les changements
   de morceau fiables : on arrete d'abord le flux actif, puis on lance le nouveau. */
static void service_music_command(void){
    if(!g_music_pending)return;
    if(mail[2]==AUDIO_STATUS_MUSIC){
        if(!mail[0])mail[0]=2u;
        g_music_active=0u;
        return;
    }
    if(mail[2]!=AUDIO_STATUS_IDLE||mail[0])return;
    mail[1]=(uint8_t)g_music_start_bank;
    mail[3]=(uint8_t)(g_music_start_bank>>8);
    mail[0]=1u;
    g_music_active=1u;
    g_music_pending=0u;
}
void dms_audio_init(void){uint8_t i;mail[0]=0u;for(i=0u;i<AUDIO_VOICES;++i){g_voice[i].active=0u;g_active_music_priorities[i]=dms_music_channel_priorities[i];}g_qread=g_qwrite=g_qcount=0u;g_music_active=0u;g_music_pending=0u;g_current_music_id=0xFFFFu;g_music_start_bank=0u;g_duck_steps=0u;}
void dms_audio_frame(void){uint8_t i;service_music_command();for(i=0u;i<AUDIO_VOICES;++i)if(g_voice[i].active){if(g_voice[i].frames)--g_voice[i].frames;if(!g_voice[i].frames)stop_voice(i);}update_duck();if(g_qcount){uint16_t id=g_queue[g_qread];const DmsSfxResourceDesc *s=find_sfx(id);if(s&&choose_voice(s)>=0){g_qread=(uint8_t)((g_qread+1u)%AUDIO_QUEUE);--g_qcount;(void)play_internal(id,0u);}}}
void MUS_play(uint16_t id){
    const DmsMusicResourceDesc *m=find_music(id);uint8_t i;if(!m)return;
    for(i=0u;i<AUDIO_VOICES;++i){stop_voice(i);}
    update_duck();
    for(i=0u;i<AUDIO_VOICES;++i)g_active_music_priorities[i]=m->priorities[i];
    g_current_music_id=id;g_music_start_bank=m->start_bank;g_music_pending=1u;
    service_music_command();
}
void MUS_stop(void){uint8_t i;g_music_pending=0u;for(i=0u;i<AUDIO_VOICES;++i)stop_voice(i);update_duck();if(wait_mail())mail[0]=2u;g_music_active=0u;}
void MUS_pause(void){MUS_stop();}
void MUS_resume(void){if(g_current_music_id!=0xFFFFu){g_music_pending=1u;service_music_command();}}
void SFX_play(uint16_t id){(void)play_internal(id,0u);update_duck();}
void SFX_stop(uint16_t id){uint8_t i;for(i=0u;i<AUDIO_VOICES;++i)if(g_voice[i].active&&g_voice[i].sfx==id)stop_voice(i);update_duck();}
uint8_t SFX_isPlaying(uint16_t id){uint8_t i;for(i=0u;i<AUDIO_VOICES;++i)if(g_voice[i].active&&g_voice[i].sfx==id)return 1u;return 0u;}
void VOICE_play(uint16_t id){SFX_play(id);}
void dms_audio_set_music_volume(uint8_t level){g_music_volume=level;(void)g_music_volume;}
void dms_audio_set_sfx_volume(uint8_t level){g_sfx_volume=level;(void)g_sfx_volume;}
