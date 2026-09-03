/* DMS-1 P1.2 full Motorola 68000 bridge for Musashi.
   The bridge owns a native mirror of the 68000-visible DMS bus so the CPU core
   does not cross Python for every memory access. Python synchronises at each
   scheduler phase and replays write events into the existing VDP model. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "m68k.h"

#if defined(_WIN32)
# define DMS_EXPORT __declspec(dllexport)
#else
# define DMS_EXPORT __attribute__((visibility("default")))
#endif

#define ADDR_MASK       0x00FFFFFFu
#define RAM_BASE        0x00100000u
#define RAM_SIZE        0x00010000u
#define VRAM_BASE       0x00200000u
#define VRAM_SIZE       0x00020000u
#define CRAM_BASE       0x00220000u
#define CRAM_SIZE       0x00000100u
#define VDP_BASE        0x00300000u
#define VDP_SIZE        0x00000100u
#define PAD_BASE        0x00400000u
#define PAD_SIZE        0x00000100u
#define MAIL_BASE       0x00500000u
#define MAIL_SIZE       0x00000100u
#define EVENT_CAPACITY  262144u

/* Event kinds. Address is the original 24-bit bus address. */
#define EV_VRAM 1u
#define EV_CRAM 2u
#define EV_VDP  3u

typedef struct {
    uint32_t address;
    uint8_t value;
    uint8_t kind;
    uint16_t reserved;
} dms_event_t;

static uint8_t *g_rom = NULL;
static uint32_t g_rom_size = 0;
static uint8_t g_ram[RAM_SIZE];
static uint8_t g_vram[VRAM_SIZE];
static uint8_t g_cram[CRAM_SIZE];
static uint8_t g_mail[MAIL_SIZE];
static uint8_t g_pad = 0;
static uint8_t g_vblank = 1;
static uint8_t g_vdp_mode = 0;
static uint8_t g_vdp_backdrop = 0;
static uint8_t g_mode_write_rejected = 0;
static uint8_t g_vdp_regs[VDP_SIZE];
static dms_event_t g_events[EVENT_CAPACITY];
static uint32_t g_event_count = 0;
static uint32_t g_event_overflow = 0;

#define WAIT_RANGE_CAPACITY 8u
static uint32_t g_wait_start[WAIT_RANGE_CAPACITY];
static uint32_t g_wait_end[WAIT_RANGE_CAPACITY];
static uint8_t g_wait_enabled[WAIT_RANGE_CAPACITY];
static uint64_t g_profile_total_cycles = 0;
static uint64_t g_profile_wait_cycles = 0;
static uint32_t g_hook_last_pc = 0;
static int g_hook_last_cycle = 0;
static uint8_t g_hook_valid = 0;

static int pc_is_wait(uint32_t pc) {
    uint32_t i;
    pc &= ADDR_MASK;
    for (i = 0; i < WAIT_RANGE_CAPACITY; ++i) {
        if (g_wait_enabled[i] && pc >= g_wait_start[i] && pc < g_wait_end[i]) return 1;
    }
    return 0;
}

static void profile_account(uint32_t pc, int cycles) {
    if (cycles <= 0) return;
    g_profile_total_cycles += (uint64_t)(unsigned int)cycles;
    if (pc_is_wait(pc)) g_profile_wait_cycles += (uint64_t)(unsigned int)cycles;
}

/* Called by Musashi immediately before each 68000 instruction. The delta from
   the previous hook is therefore the cost of the previous instruction. */
void dms_m68k_instruction_hook(unsigned int pc) {
    int now = m68k_cycles_run();
    if (g_hook_valid) profile_account(g_hook_last_pc, now - g_hook_last_cycle);
    g_hook_last_pc = pc & ADDR_MASK;
    g_hook_last_cycle = now;
    g_hook_valid = 1;
}

static void log_event(uint8_t kind, uint32_t address, uint8_t value) {
    if (g_event_count < EVENT_CAPACITY) {
        dms_event_t *e = &g_events[g_event_count++];
        e->address = address & ADDR_MASK;
        e->value = value;
        e->kind = kind;
        e->reserved = 0;
    } else {
        g_event_overflow = 1;
    }
}

static uint8_t read_vdp_reg(uint32_t off) {
    off &= 0xFFu;
    switch (off) {
        case 0x00: return (uint8_t)((g_vblank ? 1u : 0u) | (g_mode_write_rejected ? 2u : 0u));
        case 0x02: return g_vdp_mode;
        case 0x04: return g_vdp_backdrop;
        default: return g_vdp_regs[off];
    }
}

static void write_vdp_reg(uint32_t off, uint8_t value) {
    off &= 0xFFu;
    if (off == 0x02u) {
        if (g_vblank && value <= 4u) g_vdp_mode = value;
        else g_mode_write_rejected = 1u;
    } else if (off == 0x04u) {
        g_vdp_backdrop = value;
    }
    g_vdp_regs[off] = value;
    log_event(EV_VDP, VDP_BASE + off, value);
}

static uint8_t bus_read8(uint32_t address) {
    address &= ADDR_MASK;
    if (address < g_rom_size) return g_rom[address];
    if (address >= RAM_BASE && address < RAM_BASE + RAM_SIZE) return g_ram[address - RAM_BASE];
    if (address >= VRAM_BASE && address < VRAM_BASE + VRAM_SIZE) return g_vram[address - VRAM_BASE];
    if (address >= CRAM_BASE && address < CRAM_BASE + CRAM_SIZE) return g_cram[address - CRAM_BASE];
    if (address >= VDP_BASE && address < VDP_BASE + VDP_SIZE) return read_vdp_reg(address - VDP_BASE);
    if (address >= PAD_BASE && address < PAD_BASE + PAD_SIZE) return ((address - PAD_BASE) == 0u) ? g_pad : 0u;
    if (address >= MAIL_BASE && address < MAIL_BASE + MAIL_SIZE) return g_mail[address - MAIL_BASE];
    return 0xFFu;
}

static void bus_write8(uint32_t address, uint8_t value) {
    address &= ADDR_MASK;
    if (address >= RAM_BASE && address < RAM_BASE + RAM_SIZE) {
        g_ram[address - RAM_BASE] = value;
        return;
    }
    if (address >= VRAM_BASE && address < VRAM_BASE + VRAM_SIZE) {
        g_vram[address - VRAM_BASE] = value;
        log_event(EV_VRAM, address, value);
        return;
    }
    if (address >= CRAM_BASE && address < CRAM_BASE + CRAM_SIZE) {
        uint32_t off = address - CRAM_BASE;
        g_cram[off] = value;
        if ((off & 1u) == 0u) g_cram[off] &= 0x01u; /* RGB333: high byte only bit 0 */
        log_event(EV_CRAM, address, g_cram[off]);
        return;
    }
    if (address >= VDP_BASE && address < VDP_BASE + VDP_SIZE) {
        write_vdp_reg(address - VDP_BASE, value);
        return;
    }
    if (address >= MAIL_BASE && address < MAIL_BASE + MAIL_SIZE) {
        g_mail[address - MAIL_BASE] = value;
        return;
    }
    /* ROM, PAD and unmapped writes are ignored. */
}

/* Musashi host callbacks. The DMS-1 bus is big-endian like the 68000. */
unsigned int m68k_read_memory_8(unsigned int address) { return bus_read8(address); }
unsigned int m68k_read_memory_16(unsigned int address) {
    return ((unsigned int)bus_read8(address) << 8) | bus_read8(address + 1u);
}
unsigned int m68k_read_memory_32(unsigned int address) {
    return (m68k_read_memory_16(address) << 16) | m68k_read_memory_16(address + 2u);
}
void m68k_write_memory_8(unsigned int address, unsigned int value) { bus_write8(address, (uint8_t)value); }
void m68k_write_memory_16(unsigned int address, unsigned int value) {
    bus_write8(address, (uint8_t)(value >> 8));
    bus_write8(address + 1u, (uint8_t)value);
}
void m68k_write_memory_32(unsigned int address, unsigned int value) {
    m68k_write_memory_16(address, value >> 16);
    m68k_write_memory_16(address + 2u, value);
}

DMS_EXPORT int dms68k_init(void) {
    memset(g_ram, 0, sizeof(g_ram));
    memset(g_vram, 0, sizeof(g_vram));
    memset(g_cram, 0, sizeof(g_cram));
    memset(g_mail, 0, sizeof(g_mail));
    memset(g_vdp_regs, 0, sizeof(g_vdp_regs));
    g_pad = 0; g_vblank = 1; g_vdp_mode = 0; g_vdp_backdrop = 0;
    g_mode_write_rejected = 0; g_event_count = 0; g_event_overflow = 0;
    memset(g_wait_enabled, 0, sizeof(g_wait_enabled));
    g_profile_total_cycles = 0; g_profile_wait_cycles = 0; g_hook_valid = 0;

    /* Musashi 4.60 requires m68k_init() before selecting/executing a CPU.
       It builds the 65536-entry opcode dispatch table and installs default
       callbacks. Without it the DLL loads, but the first m68k_execute() can
       jump through an uninitialised/null handler. */
    m68k_init();
    m68k_set_cpu_type(M68K_CPU_TYPE_68000);
    return 1;
}

DMS_EXPORT int dms68k_load_rom(const uint8_t *data, uint32_t size) {
    uint8_t *new_rom;
    if (!data || size < 8u) return 0;
    new_rom = (uint8_t*)malloc(size);
    if (!new_rom) return 0;
    memcpy(new_rom, data, size);
    free(g_rom);
    g_rom = new_rom;
    g_rom_size = size;
    return 1;
}

DMS_EXPORT void dms68k_reset(void) {
    g_event_count = 0; g_event_overflow = 0; g_mode_write_rejected = 0;
    m68k_pulse_reset();
}
DMS_EXPORT int dms68k_run(int cycles) {
    int actual, now;
    if (cycles <= 0) return 0;
    g_hook_valid = 0;
    g_hook_last_cycle = 0;
    actual = m68k_execute(cycles);
    if (g_hook_valid) {
        now = m68k_cycles_run();
        profile_account(g_hook_last_pc, now - g_hook_last_cycle);
    }
    g_hook_valid = 0;
    return actual;
}
DMS_EXPORT void dms68k_set_pad(uint8_t value) { g_pad = value; }
DMS_EXPORT void dms68k_set_vblank(uint8_t value) { g_vblank = value ? 1u : 0u; }
DMS_EXPORT void dms68k_set_irq(uint8_t level) { m68k_set_irq(level & 7u); }

DMS_EXPORT void dms68k_set_mailbox(const uint8_t *data, uint32_t size) {
    if (!data) return;
    if (size > MAIL_SIZE) size = MAIL_SIZE;
    memcpy(g_mail, data, size);
}
DMS_EXPORT void dms68k_get_mailbox(uint8_t *data, uint32_t size) {
    if (!data) return;
    if (size > MAIL_SIZE) size = MAIL_SIZE;
    memcpy(data, g_mail, size);
}
DMS_EXPORT void dms68k_set_ram(const uint8_t *data, uint32_t size) {
    if (!data) return;
    if (size > RAM_SIZE) size = RAM_SIZE;
    memcpy(g_ram, data, size);
}
DMS_EXPORT void dms68k_get_ram(uint8_t *data, uint32_t size) {
    if (!data) return;
    if (size > RAM_SIZE) size = RAM_SIZE;
    memcpy(data, g_ram, size);
}
DMS_EXPORT void dms68k_set_vram(const uint8_t *data, uint32_t size) {
    if (!data) return;
    if (size > VRAM_SIZE) size = VRAM_SIZE;
    memcpy(g_vram, data, size);
}
DMS_EXPORT void dms68k_set_cram(const uint8_t *data, uint32_t size) {
    if (!data) return;
    if (size > CRAM_SIZE) size = CRAM_SIZE;
    memcpy(g_cram, data, size);
}
DMS_EXPORT void dms68k_set_vdp_reg(uint8_t off, uint8_t value) {
    /* Synchronisation from Python; do not generate an event. */
    g_vdp_regs[off] = value;
    if (off == 0x02u) g_vdp_mode = value;
    else if (off == 0x04u) g_vdp_backdrop = value;
}
DMS_EXPORT void dms68k_set_mode_rejected(uint8_t value) { g_mode_write_rejected = value ? 1u : 0u; }

DMS_EXPORT uint32_t dms68k_event_count(void) { return g_event_count; }
DMS_EXPORT uint32_t dms68k_event_overflow(void) { return g_event_overflow; }
DMS_EXPORT uint32_t dms68k_event_address(uint32_t index) { return index < g_event_count ? g_events[index].address : 0u; }
DMS_EXPORT uint32_t dms68k_event_value(uint32_t index) { return index < g_event_count ? g_events[index].value : 0u; }
DMS_EXPORT uint32_t dms68k_event_kind(uint32_t index) { return index < g_event_count ? g_events[index].kind : 0u; }
DMS_EXPORT void dms68k_events_clear(void) { g_event_count = 0; g_event_overflow = 0; }
DMS_EXPORT uint32_t dms68k_get_pc(void) { return m68k_get_reg(NULL, M68K_REG_PC) & ADDR_MASK; }
DMS_EXPORT uint32_t dms68k_get_sr(void) { return m68k_get_reg(NULL, M68K_REG_SR) & 0xFFFFu; }
DMS_EXPORT uint32_t dms68k_get_d(uint32_t index) {
    if (index > 7u) return 0u;
    return m68k_get_reg(NULL, (m68k_register_t)(M68K_REG_D0 + index));
}
DMS_EXPORT uint32_t dms68k_get_a(uint32_t index) {
    if (index > 7u) return 0u;
    return m68k_get_reg(NULL, (m68k_register_t)(M68K_REG_A0 + index));
}
DMS_EXPORT void dms68k_profile_reset(void) {
    g_profile_total_cycles = 0; g_profile_wait_cycles = 0; g_hook_valid = 0;
}
DMS_EXPORT uint64_t dms68k_profile_total_cycles(void) { return g_profile_total_cycles; }
DMS_EXPORT uint64_t dms68k_profile_wait_cycles(void) { return g_profile_wait_cycles; }
DMS_EXPORT void dms68k_profile_clear_wait_ranges(void) { memset(g_wait_enabled, 0, sizeof(g_wait_enabled)); }
DMS_EXPORT void dms68k_profile_set_wait_range(uint32_t index, uint32_t start, uint32_t end) {
    if (index >= WAIT_RANGE_CAPACITY) return;
    g_wait_start[index] = start & ADDR_MASK;
    g_wait_end[index] = end & ADDR_MASK;
    g_wait_enabled[index] = (end > start) ? 1u : 0u;
}
DMS_EXPORT void dms68k_shutdown(void) { free(g_rom); g_rom = NULL; g_rom_size = 0; }
