// DAC MASTER DMS-1 P1.0.9 - final locked native Windows host.
//
// This process owns the real-time window, keyboard, GDI backbuffer and Win32
// message pump. Python owns the emulated DMS-1 machine. After one full startup
// snapshot, only dirty VRAM/CRAM ranges and current registers cross the pipe.
// No Tk/Tcl object participates in video and no periodic telemetry runs on the GUI thread.
//
// Binary stdin protocol (little endian):
//   Header { 'DMSH', uint32 type, uint32 payload_size }
//   type 1 FRAME_FULL : FrameMeta + VRAM bytes + CRAM bytes
//   type 2 HUD        : UTF-8 text (up to three lines)
//   type 3 QUIT       : empty
//   type 4 FRAME_DELTA: FrameMeta(counts) + dirty range records
//   type 5 DISPLAY_PROFILE: uint32 profile (host presentation only; frame ABI unchanged)
// stdout is line-oriented telemetry: READY, PAD n, STAT ..., FREEZE n, CLOSE.

#include <cstdint>
#include <cstdio>
#include <cstring>

#if !defined(_WIN32)
int main() {
    std::puts("DMS-1 native host is Windows-only");
    return 0;
}
#else

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>
#include <fcntl.h>
#include <io.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

extern "C" int dms1_vdp_render(
    const uint8_t* vram, std::size_t vram_size,
    const uint8_t* cram, std::size_t cram_size,
    int mode, int backdrop,
    int scroll_a_x, int scroll_a_y, int scroll_b_x, int scroll_b_y,
    uint8_t* out, std::size_t out_size, int* out_width);

namespace {
constexpr uint32_t MAGIC = 0x48534D44u; // bytes: D M S H
constexpr uint32_t PKT_FRAME_FULL  = 1;
constexpr uint32_t PKT_HUD         = 2;
constexpr uint32_t PKT_QUIT        = 3;
constexpr uint32_t PKT_FRAME_DELTA = 4;
constexpr uint32_t PKT_DISPLAY_PROFILE = 5;
constexpr UINT WM_DMS_FRAME  = WM_APP + 0x31;
constexpr UINT WM_DMS_HUD    = WM_APP + 0x32;
constexpr int SOURCE_H = 224;
constexpr int MAX_SOURCE_W = 320;
/* The old host was hard-wired to 960x672 + 210px HUD (882px client).
   On common 1366x768 displays this physically placed the sprite/scanline lines
   of F3 below the desktop.  The host now chooses a scale from the Windows work
   area while preserving the DMS framebuffer aspect ratio and a full 10-line HUD. */
constexpr int HUD_MIN_H = 168;
constexpr int HOST_MAX_SCALE_PCT = 300;
constexpr int HOST_MIN_SCALE_PCT = 180;
int g_video_w = 960;
int g_video_h = 672;
int g_hud_h = 210;
int g_client_w = 960;
int g_client_h = 882;
constexpr std::size_t VRAM_EXPECTED = 0x20000;
constexpr std::size_t CRAM_EXPECTED = 0x100;

constexpr uint8_t PAD_UP    = 0x01;
constexpr uint8_t PAD_DOWN  = 0x02;
constexpr uint8_t PAD_LEFT  = 0x04;
constexpr uint8_t PAD_RIGHT = 0x08;
constexpr uint8_t PAD_A     = 0x10;
constexpr uint8_t PAD_B     = 0x20;
constexpr uint8_t PAD_C     = 0x40;
constexpr uint8_t PAD_START = 0x80;

#pragma pack(push, 1)
struct PacketHeader {
    char magic[4];
    uint32_t type;
    uint32_t size;
};
struct FrameMeta {
    uint64_t frame;
    int32_t mode;
    int32_t backdrop;
    int32_t scroll_a_x;
    int32_t scroll_a_y;
    int32_t scroll_b_x;
    int32_t scroll_b_y;
    uint32_t arg0; // FULL=vram_size, DELTA=vram_range_count
    uint32_t arg1; // FULL=cram_size, DELTA=cram_range_count
};
struct RangeMeta { uint32_t offset; uint32_t size; };
#pragma pack(pop)

struct FrameState {
    FrameMeta meta{};
    std::vector<uint8_t> vram;
    std::vector<uint8_t> cram;
};

struct BackBuffer {
    HDC dc = nullptr;
    HBITMAP bitmap = nullptr;
    HGDIOBJ old = nullptr;
    int width = 0;
    int height = 0;

    void destroy() {
        if (dc && old) { SelectObject(dc, old); old = nullptr; }
        if (bitmap) { DeleteObject(bitmap); bitmap = nullptr; }
        if (dc) { DeleteDC(dc); dc = nullptr; }
        width = height = 0;
    }
    ~BackBuffer() { destroy(); }

    bool create(HWND hwnd, int w, int h) {
        destroy();
        HDC window_dc = GetDC(hwnd);
        if (!window_dc) return false;
        dc = CreateCompatibleDC(window_dc);
        bitmap = CreateCompatibleBitmap(window_dc, w, h);
        ReleaseDC(hwnd, window_dc);
        if (!dc || !bitmap) { destroy(); return false; }
        old = SelectObject(dc, bitmap);
        width = w; height = h;
        return true;
    }
};

HWND g_hwnd = nullptr;
BackBuffer g_back;
std::mutex g_frame_mutex;
FrameState g_latest;
std::atomic<bool> g_frame_pending{false};
std::atomic<bool> g_alive{true};
std::atomic<uint8_t> g_pad{0};
std::atomic<bool> g_freeze{false};
std::mutex g_hud_mutex;
std::string g_hud = "DMS-1 DEBUGGER V0.1\nF1=DEBUG F3=VDP F4=AUDIO F5=RAM F6=CPU F8=PAUSE F9=CAPTURE F10=STEP F11=DISPLAY F12=FREEZE";
std::mutex g_stdout_mutex;

// P1.0.9 optional compositor pacing. Loaded dynamically so MinGW does not
// require a hard dwmapi link dependency. DwmFlush waits until DWM has consumed
// the frame, removing the last host-side burst/jitter visible mostly in M0.
using DwmFlushFn = HRESULT (WINAPI*)();
DwmFlushFn g_dwm_flush = nullptr;
std::atomic<uint64_t> g_dwm_flushes{0};

std::atomic<uint64_t> g_received{0};
std::atomic<uint64_t> g_rendered{0};
std::atomic<uint64_t> g_paints{0};
std::atomic<uint64_t> g_video_presented{0};
std::atomic<bool> g_video_dirty{false};
std::atomic<uint64_t> g_overwritten{0};
std::atomic<uint64_t> g_last_frame{0};
std::atomic<double> g_last_render_ms{0.0};
uint64_t g_stat_last_rendered = 0;
uint64_t g_stat_last_received = 0;
uint64_t g_stat_last_overwritten = 0;
uint64_t g_stat_last_presented = 0;
auto g_stat_time = std::chrono::steady_clock::now();
std::atomic<double> g_last_present_gap_ms{0.0};
std::atomic<double> g_max_present_gap_ms{0.0};
auto g_last_present_time = std::chrono::steady_clock::now();

void emit_line(const std::string& s) {
    std::lock_guard<std::mutex> lock(g_stdout_mutex);
    std::fwrite(s.data(), 1, s.size(), stdout);
    std::fwrite("\n", 1, 1, stdout);
    std::fflush(stdout);
}

bool read_exact(void* dst, std::size_t n) {
    auto* p = static_cast<uint8_t*>(dst);
    while (n) {
        const std::size_t got = std::fread(p, 1, n, stdin);
        if (got == 0) return false;
        p += got;
        n -= got;
    }
    return true;
}

uint32_t fnv1a(const uint8_t* p, std::size_t n) {
    uint32_t h = 2166136261u;
    for (std::size_t i = 0; i < n; ++i) { h ^= p[i]; h *= 16777619u; }
    return h;
}


enum DisplayProfile : int {
    DISPLAY_RAW = 0,
    DISPLAY_SCANLINES = 1,
    DISPLAY_CRT_SOFT = 2,
    DISPLAY_CRT_SCANLINES = 3,
    DISPLAY_COMPOSITE = 4,
    DISPLAY_PROFILE_COUNT = 5,
};

std::atomic<int> g_display_override{-1}; // -1 = cartridge/GDK requested profile
std::atomic<int> g_last_game_profile{DISPLAY_RAW};

const char* display_profile_name(int profile) {
    switch (profile) {
        case DISPLAY_RAW: return "RAW / PIXEL PERFECT";
        case DISPLAY_SCANLINES: return "SCANLINES";
        case DISPLAY_CRT_SOFT: return "CRT SOFT";
        case DISPLAY_CRT_SCANLINES: return "CRT + SCANLINES";
        case DISPLAY_COMPOSITE: return "COMPOSITE";
        default: return "RAW / PIXEL PERFECT";
    }
}

int sanitize_display_profile(int profile) {
    return (profile >= 0 && profile < DISPLAY_PROFILE_COUNT) ? profile : DISPLAY_RAW;
}

inline uint8_t darken_u8(uint8_t v, unsigned numerator, unsigned denominator) {
    return static_cast<uint8_t>((static_cast<unsigned>(v) * numerator + denominator / 2u) / denominator);
}

void apply_scanlines(std::vector<uint8_t>& bgr, int width, int height, unsigned numerator = 11u, unsigned denominator = 16u) {
    for (int y = 1; y < height; y += 2) {
        uint8_t* row = bgr.data() + static_cast<std::size_t>(y) * width * 3u;
        for (int x = 0; x < width * 3; ++x) row[x] = darken_u8(row[x], numerator, denominator);
    }
}

void apply_crt_soft(const std::vector<uint8_t>& src, std::vector<uint8_t>& dst, int width, int height) {
    const std::size_t bytes = static_cast<std::size_t>(width) * height * 3u;
    if (dst.size() < bytes) dst.resize(bytes);
    for (int y = 0; y < height; ++y) {
        const uint8_t* row = src.data() + static_cast<std::size_t>(y) * width * 3u;
        uint8_t* out = dst.data() + static_cast<std::size_t>(y) * width * 3u;
        for (int x = 0; x < width; ++x) {
            const int xl = (x > 0) ? x - 1 : x;
            const int xr = (x + 1 < width) ? x + 1 : x;
            for (int c = 0; c < 3; ++c) {
                const unsigned l = row[xl * 3 + c];
                const unsigned m = row[x  * 3 + c];
                const unsigned r = row[xr * 3 + c];
                // 75% centre + 12.5% each neighbour: enough analogue blending
                // to calm hard square pixels without destroying pixel art.
                out[x * 3 + c] = static_cast<uint8_t>((l + 6u * m + r + 4u) >> 3);
            }
        }
    }
}

void apply_composite(const std::vector<uint8_t>& src, std::vector<uint8_t>& dst, int width, int height) {
    const std::size_t bytes = static_cast<std::size_t>(width) * height * 3u;
    if (dst.size() < bytes) dst.resize(bytes);
    for (int y = 0; y < height; ++y) {
        const uint8_t* row = src.data() + static_cast<std::size_t>(y) * width * 3u;
        uint8_t* out = dst.data() + static_cast<std::size_t>(y) * width * 3u;
        for (int x = 0; x < width; ++x) {
            const int xl = (x > 0) ? x - 1 : x;
            const int xr = (x + 1 < width) ? x + 1 : x;
            // BGR: mild luma blend plus opposite chroma bleed. This intentionally
            // merges dithering/colour edges while staying subtle enough for text.
            const unsigned lb = row[xl * 3 + 0], cb = row[x * 3 + 0];
            const unsigned cg = row[x * 3 + 1];
            const unsigned cr = row[x * 3 + 2], rr = row[xr * 3 + 2];
            const unsigned lg = row[xl * 3 + 1], rg = row[xr * 3 + 1];
            out[x * 3 + 0] = static_cast<uint8_t>((3u * cb + lb + 2u) >> 2);
            out[x * 3 + 1] = static_cast<uint8_t>((lg + 6u * cg + rg + 4u) >> 3);
            out[x * 3 + 2] = static_cast<uint8_t>((3u * cr + rr + 2u) >> 2);
        }
    }
}

const std::vector<uint8_t>* apply_display_profile(
    int profile, std::vector<uint8_t>& base, std::vector<uint8_t>& scratch,
    int width, int height) {
    profile = sanitize_display_profile(profile);
    if (profile == DISPLAY_RAW) return &base;
    if (profile == DISPLAY_SCANLINES) {
        scratch = base;
        apply_scanlines(scratch, width, height);
        return &scratch;
    }
    if (profile == DISPLAY_CRT_SOFT) {
        apply_crt_soft(base, scratch, width, height);
        return &scratch;
    }
    if (profile == DISPLAY_CRT_SCANLINES) {
        apply_crt_soft(base, scratch, width, height);
        apply_scanlines(scratch, width, height, 12u, 16u);
        return &scratch;
    }
    apply_composite(base, scratch, width, height);
    return &scratch;
}

void draw_text_lines(HDC dc, const std::string& text) {
    RECT r{0, g_video_h, g_client_w, g_client_h};
    HBRUSH bg = CreateSolidBrush(RGB(11, 16, 22));
    FillRect(dc, &r, bg);
    DeleteObject(bg);

    SetBkMode(dc, TRANSPARENT);
    HFONT font = static_cast<HFONT>(GetStockObject(ANSI_FIXED_FONT));
    HGDIOBJ old_font = SelectObject(dc, font);

    int y = g_video_h + 7;
    int line = 0;
    std::size_t start = 0;
    while (start <= text.size() && line < 10) {
        std::size_t end = text.find('\n', start);
        if (end == std::string::npos) end = text.size();
        std::string part = text.substr(start, end - start);
        if (line == 0) SetTextColor(dc, RGB(255, 209, 102));
        else if (line == 1) SetTextColor(dc, RGB(85, 230, 255));
        else SetTextColor(dc, RGB(183, 195, 207));
        TextOutA(dc, 10, y, part.c_str(), static_cast<int>(part.size()));
        y += 16;
        ++line;
        if (end == text.size()) break;
        start = end + 1;
    }
    SelectObject(dc, old_font);
}

void redraw_hud_only() {
    if (!g_back.dc) return;
    std::string hud;
    {
        std::lock_guard<std::mutex> lock(g_hud_mutex);
        hud = g_hud;
    }
    if (g_freeze.load()) hud += "\nVIDEO PRESENTATION FROZEN (F12)";
    {
        const int over = g_display_override.load();
        const int effective = over >= 0 ? over : sanitize_display_profile(g_last_game_profile.load());
        hud += "\nDISPLAY: ";
        hud += display_profile_name(effective);
        hud += over >= 0 ? "  [F11 override]" : "  [GDK]";
    }
    draw_text_lines(g_back.dc, hud);
    if (g_hwnd) {
        RECT hud_rect{0, g_video_h, g_client_w, g_client_h};
        InvalidateRect(g_hwnd, &hud_rect, FALSE);
    }
}

bool compose_frame(const FrameState& f) {
    if (!g_back.dc || f.vram.size() < VRAM_EXPECTED || f.cram.size() < CRAM_EXPECTED) return false;
    const auto t0 = std::chrono::steady_clock::now();

    static std::vector<uint8_t> rgb(static_cast<std::size_t>(MAX_SOURCE_W) * SOURCE_H * 3);
    static std::vector<uint8_t> bgr(static_cast<std::size_t>(MAX_SOURCE_W) * SOURCE_H * 3);
    static std::vector<uint8_t> filtered(static_cast<std::size_t>(MAX_SOURCE_W) * SOURCE_H * 3);
    int source_w = 0;
    const int rc = dms1_vdp_render(
        f.vram.data(), f.vram.size(), f.cram.data(), f.cram.size(),
        f.meta.mode, f.meta.backdrop,
        f.meta.scroll_a_x, f.meta.scroll_a_y, f.meta.scroll_b_x, f.meta.scroll_b_y,
        rgb.data(), rgb.size(), &source_w);
    if (rc != 0 || (source_w != 320 && source_w != 256)) return false;

    const std::size_t px = static_cast<std::size_t>(source_w) * SOURCE_H;
    for (std::size_t i = 0; i < px; ++i) {
        bgr[i*3 + 0] = rgb[i*3 + 2];
        bgr[i*3 + 1] = rgb[i*3 + 1];
        bgr[i*3 + 2] = rgb[i*3 + 0];
    }

    // Compose only the VIDEO region off-screen. The HUD is retained and is no
    // longer cleared/redrawn 60 times/s. 320-wide modes overwrite every video
    // pixel, so only LOW RES needs an explicit black letterbox clear.
    int dst_w = g_video_w, dst_h = g_video_h;
    if (source_w == 256) {
        PatBlt(g_back.dc, 0, 0, g_video_w, g_video_h, BLACKNESS);
        dst_h = g_video_h;
        dst_w = (g_video_h * 256) / SOURCE_H;
    }
    const int dst_x = (g_video_w - dst_w) / 2;
    const int dst_y = (g_video_h - dst_h) / 2;

    const int over = g_display_override.load();
    const int profile = (over >= 0) ? over : sanitize_display_profile(g_last_game_profile.load());
    const std::vector<uint8_t>* presented = apply_display_profile(profile, bgr, filtered, source_w, SOURCE_H);

    BITMAPINFO bmi{};
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = source_w;
    bmi.bmiHeader.biHeight = -SOURCE_H;
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 24;
    bmi.bmiHeader.biCompression = BI_RGB;
    SetStretchBltMode(g_back.dc, COLORONCOLOR);
    const int lines = StretchDIBits(
        g_back.dc, dst_x, dst_y, dst_w, dst_h,
        0, 0, source_w, SOURCE_H,
        presented->data(), &bmi, DIB_RGB_COLORS, SRCCOPY);
    if (lines == GDI_ERROR) return false;

    const auto t1 = std::chrono::steady_clock::now();
    const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    g_last_render_ms.store(ms);
    g_last_frame.store(f.meta.frame);
    ++g_rendered;
    g_video_dirty.store(true);
    RECT video_rect{0, 0, g_video_w, g_video_h};
    InvalidateRect(g_hwnd, &video_rect, FALSE);
    return true;
}

void emit_pad() {
    emit_line("PAD " + std::to_string(static_cast<unsigned>(g_pad.load())));
}

void set_pad_bit(uint8_t bit, bool down) {
    uint8_t old = g_pad.load();
    uint8_t next;
    do {
        next = down ? static_cast<uint8_t>(old | bit) : static_cast<uint8_t>(old & ~bit);
        if (next == old) return;
    } while (!g_pad.compare_exchange_weak(old, next));
    emit_pad();
}

uint8_t key_bit(WPARAM vk) {
    switch (vk) {
        case VK_UP: return PAD_UP;
        case VK_DOWN: return PAD_DOWN;
        case VK_LEFT: return PAD_LEFT;
        case VK_RIGHT: return PAD_RIGHT;
        case 'Z': return PAD_A;
        case 'X': return PAD_B;
        case 'C': return PAD_C;
        case VK_RETURN: return PAD_START;
        default: return 0;
    }
}

LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_CREATE:
            return 0;
        case WM_ERASEBKGND:
            // No visible clear between frames. The persistent backbuffer owns all pixels.
            return 1;
        case WM_KEYDOWN:
        case WM_SYSKEYDOWN: {
            if (wp == VK_ESCAPE) {
                emit_line("CLOSE");
                DestroyWindow(hwnd);
                return 0;
            }
            if (!(lp & (1LL << 30))) {
                const char* debug_key = nullptr;
                switch (wp) {
                    case VK_F1: debug_key = "F1"; break;
                    case VK_F2: debug_key = "F2"; break;
                    case VK_F3: debug_key = "F3"; break;
                    case VK_F4: debug_key = "F4"; break;
                    case VK_F5: debug_key = "F5"; break;
                    case VK_F6: debug_key = "F6"; break;
                    case VK_F7: debug_key = "F7"; break;
                    case VK_F8: debug_key = "F8"; break;
                    case VK_F9: debug_key = "F9"; break;
                    case VK_F10: debug_key = "F10"; break;
                    default: break;
                }
                if (debug_key) {
                    emit_line(std::string("KEY ") + debug_key);
                    return 0;
                }
                if (wp == VK_F11) {
                    const int current_override = g_display_override.load();
                    int next = -1;
                    if (current_override < 0) {
                        const int game = sanitize_display_profile(g_last_game_profile.load());
                        next = (game + 1) % DISPLAY_PROFILE_COUNT;
                    } else if (current_override + 1 < DISPLAY_PROFILE_COUNT) {
                        next = current_override + 1;
                    }
                    g_display_override.store(next);
                    emit_line(std::string("DISPLAY ") + (next < 0 ? "GDK" : display_profile_name(next)));
                    g_frame_pending.store(true);
                    PostMessage(hwnd, WM_DMS_FRAME, 0, 0);
                    redraw_hud_only();
                    return 0;
                }
                if (wp == VK_F12) {
                    const bool now = !g_freeze.load();
                    g_freeze.store(now);
                    emit_line(std::string("FREEZE ") + (now ? "1" : "0"));
                    if (!now) {
                        g_frame_pending.store(true);
                        PostMessage(hwnd, WM_DMS_FRAME, 0, 0);
                    }
                    redraw_hud_only();
                    return 0;
                }
            }
            const uint8_t bit = key_bit(wp);
            if (bit) set_pad_bit(bit, true);
            return 0;
        }
        case WM_KEYUP:
        case WM_SYSKEYUP: {
            const uint8_t bit = key_bit(wp);
            if (bit) set_pad_bit(bit, false);
            return 0;
        }
        case WM_KILLFOCUS:
            if (g_pad.exchange(0) != 0) emit_pad();
            return 0;
        case WM_DMS_FRAME: {
            g_frame_pending.store(false);
            if (g_freeze.load()) return 0;
            // P1.0.9: persistent snapshot. P1.0.8 constructed/copy-assigned a
            // 128 KiB vector pair on every frame, creating allocator churn.
            // Resize once, then memcpy into stable storage.
            static FrameState local;
            if (local.vram.size() != VRAM_EXPECTED) local.vram.resize(VRAM_EXPECTED);
            if (local.cram.size() != CRAM_EXPECTED) local.cram.resize(CRAM_EXPECTED);
            bool valid = false;
            {
                std::lock_guard<std::mutex> lock(g_frame_mutex);
                if (g_latest.vram.size() == VRAM_EXPECTED && g_latest.cram.size() == CRAM_EXPECTED) {
                    local.meta = g_latest.meta;
                    std::memcpy(local.vram.data(), g_latest.vram.data(), VRAM_EXPECTED);
                    std::memcpy(local.cram.data(), g_latest.cram.data(), CRAM_EXPECTED);
                    valid = true;
                }
            }
            if (valid) compose_frame(local);
            return 0;
        }
        case WM_DMS_HUD:
            // HUD is outside the DMS-1 framebuffer and updates independently.
            redraw_hud_only();
            return 0;
        case WM_PAINT: {
            PAINTSTRUCT ps{};
            HDC dc = BeginPaint(hwnd, &ps);
            if (g_back.dc) {
                const int w = std::max(0L, ps.rcPaint.right - ps.rcPaint.left);
                const int h = std::max(0L, ps.rcPaint.bottom - ps.rcPaint.top);
                if (w > 0 && h > 0) {
                    BitBlt(dc, ps.rcPaint.left, ps.rcPaint.top, w, h,
                           g_back.dc, ps.rcPaint.left, ps.rcPaint.top, SRCCOPY);
                }
            }
            EndPaint(hwnd, &ps);
            ++g_paints;
            if (g_video_dirty.exchange(false)) {
                // Synchronise the completed BitBlt with the Desktop Window
                // Manager when available. This is host presentation pacing only;
                // it does not change the DMS-1 60 Hz machine clock.
                if (g_dwm_flush) {
                    g_dwm_flush();
                    ++g_dwm_flushes;
                }
                const auto now = std::chrono::steady_clock::now();
                const double gap = std::chrono::duration<double, std::milli>(now - g_last_present_time).count();
                g_last_present_time = now;
                g_last_present_gap_ms.store(gap);
                double previous = g_max_present_gap_ms.load();
                while (gap > previous && !g_max_present_gap_ms.compare_exchange_weak(previous, gap)) {}
                ++g_video_presented;
            }
            return 0;
        }
        case WM_CLOSE:
            emit_line("CLOSE");
            DestroyWindow(hwnd);
            return 0;
        case WM_DESTROY:
            g_alive.store(false);
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProc(hwnd, msg, wp, lp);
}

void signal_new_frame_locked() {
    ++g_received;
    if (g_frame_pending.load()) ++g_overwritten;
    if (!g_frame_pending.exchange(true)) PostMessage(g_hwnd, WM_DMS_FRAME, 0, 0);
}

bool apply_delta_payload(const std::vector<uint8_t>& payload) {
    if (payload.size() < sizeof(FrameMeta)) return false;
    FrameMeta meta{};
    std::memcpy(&meta, payload.data(), sizeof(meta));
    const uint8_t* p = payload.data() + sizeof(meta);
    const uint8_t* end = payload.data() + payload.size();
    std::lock_guard<std::mutex> lock(g_frame_mutex);
    if (g_latest.vram.size() != VRAM_EXPECTED || g_latest.cram.size() != CRAM_EXPECTED) return false;
    g_latest.meta = meta;
    auto apply_ranges = [&](std::vector<uint8_t>& dst, uint32_t count) -> bool {
        for (uint32_t i = 0; i < count; ++i) {
            if (static_cast<std::size_t>(end - p) < sizeof(RangeMeta)) return false;
            RangeMeta r{}; std::memcpy(&r, p, sizeof(r)); p += sizeof(r);
            if (r.offset > dst.size() || r.size > dst.size() - r.offset) return false;
            if (static_cast<std::size_t>(end - p) < r.size) return false;
            std::memcpy(dst.data() + r.offset, p, r.size); p += r.size;
        }
        return true;
    };
    if (!apply_ranges(g_latest.vram, meta.arg0)) return false;
    if (!apply_ranges(g_latest.cram, meta.arg1)) return false;
    if (p != end) return false;
    signal_new_frame_locked();
    return true;
}

void reader_thread() {
    while (g_alive.load()) {
        PacketHeader h{};
        if (!read_exact(&h, sizeof(h))) break;
        if (std::memcmp(h.magic, "DMSH", 4) != 0 || h.size > 4 * 1024 * 1024) {
            emit_line("ERROR protocol-header");
            break;
        }
        std::vector<uint8_t> payload(h.size);
        if (h.size && !read_exact(payload.data(), payload.size())) break;

        if (h.type == PKT_FRAME_FULL) {
            if (payload.size() < sizeof(FrameMeta)) continue;
            FrameMeta meta{};
            std::memcpy(&meta, payload.data(), sizeof(meta));
            const std::size_t need = sizeof(meta) + static_cast<std::size_t>(meta.arg0) + meta.arg1;
            if (meta.arg0 != VRAM_EXPECTED || meta.arg1 != CRAM_EXPECTED || payload.size() != need) {
                emit_line("ERROR frame-full-size");
                continue;
            }
            FrameState next;
            next.meta = meta;
            const uint8_t* q = payload.data() + sizeof(meta);
            next.vram.assign(q, q + meta.arg0); q += meta.arg0;
            next.cram.assign(q, q + meta.arg1);
            {
                std::lock_guard<std::mutex> lock(g_frame_mutex);
                g_latest = std::move(next);
                signal_new_frame_locked();
            }
        } else if (h.type == PKT_FRAME_DELTA) {
            if (!apply_delta_payload(payload)) emit_line("ERROR frame-delta");
        } else if (h.type == PKT_DISPLAY_PROFILE) {
            if (payload.size() == sizeof(uint32_t)) {
                uint32_t requested = 0;
                std::memcpy(&requested, payload.data(), sizeof(requested));
                g_last_game_profile.store(sanitize_display_profile(static_cast<int>(requested)));
                g_frame_pending.store(true);
                if (g_hwnd) PostMessage(g_hwnd, WM_DMS_FRAME, 0, 0);
                redraw_hud_only();
            }
        } else if (h.type == PKT_HUD) {
            std::string text(reinterpret_cast<const char*>(payload.data()), payload.size());
            {
                std::lock_guard<std::mutex> lock(g_hud_mutex);
                g_hud = std::move(text);
            }
            PostMessage(g_hwnd, WM_DMS_HUD, 0, 0);
        } else if (h.type == PKT_QUIT) {
            PostMessage(g_hwnd, WM_CLOSE, 0, 0);
            return;
        }
    }
    if (g_alive.load()) PostMessage(g_hwnd, WM_CLOSE, 0, 0);
}

void telemetry_thread() {
    // Crucial P1.0.8 rule: never perform pipe I/O from the Win32 GUI/message
    // thread. A slow diagnostic reader therefore cannot delay WM_PAINT.
    while (g_alive.load()) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        if (!g_alive.load()) break;
        const auto now = std::chrono::steady_clock::now();
        const double sec = std::max(1e-6, std::chrono::duration<double>(now - g_stat_time).count());
        const uint64_t rendered = g_rendered.load();
        const uint64_t presented = g_video_presented.load();
        const uint64_t received = g_received.load();
        const uint64_t overwritten = g_overwritten.load();
        const double fps = (presented - g_stat_last_presented) / sec;
        const double render_fps = (rendered - g_stat_last_rendered) / sec;
        const double rx_fps = (received - g_stat_last_received) / sec;
        const uint64_t drop_delta = overwritten - g_stat_last_overwritten;
        g_stat_last_rendered = rendered; g_stat_last_presented = presented;
        g_stat_last_received = received; g_stat_last_overwritten = overwritten; g_stat_time = now;
        const double max_gap = g_max_present_gap_ms.exchange(0.0);
        char buf[384];
        std::snprintf(buf, sizeof(buf),
            "STAT fps=%.3f render_fps=%.3f rx_fps=%.3f received=%llu rendered=%llu presented=%llu overwritten=%llu drop_delta=%llu paints=%llu render_ms=%.3f frame=%llu freeze=%d gap_ms=%.3f gap_max_ms=%.3f dwm_flushes=%llu",
            fps, render_fps, rx_fps,
            static_cast<unsigned long long>(received), static_cast<unsigned long long>(rendered),
            static_cast<unsigned long long>(presented), static_cast<unsigned long long>(overwritten),
            static_cast<unsigned long long>(drop_delta), static_cast<unsigned long long>(g_paints.load()),
            g_last_render_ms.load(), static_cast<unsigned long long>(g_last_frame.load()),
            g_freeze.load() ? 1 : 0, g_last_present_gap_ms.load(), max_gap,
            static_cast<unsigned long long>(g_dwm_flushes.load()));
        emit_line(buf);
    }
}

void configure_host_geometry() {
    RECT work{};
    int work_w = 960, work_h = 768;
    if (SystemParametersInfoA(SPI_GETWORKAREA, 0, &work, 0)) {
        work_w = (work.right > work.left) ? (work.right - work.left) : work_w;
        work_h = (work.bottom > work.top) ? (work.bottom - work.top) : work_h;
    }

    /* Reserve a conservative amount for caption/borders before choosing the
       framebuffer scale.  The resulting client always keeps the full F3 page. */
    const int frame_allowance = 46;
    const int hud = HUD_MIN_H;
    const int usable_w = (work_w > 40) ? (work_w - 24) : work_w;
    const int usable_h = (work_h > frame_allowance + hud) ? (work_h - frame_allowance - hud) : SOURCE_H;
    int scale_w = (usable_w * 100) / 320;
    int scale_h = (usable_h * 100) / SOURCE_H;
    int scale = (scale_w < scale_h) ? scale_w : scale_h;
    if (scale > HOST_MAX_SCALE_PCT) scale = HOST_MAX_SCALE_PCT;
    if (scale < HOST_MIN_SCALE_PCT) scale = HOST_MIN_SCALE_PCT;

    g_video_w = (320 * scale) / 100;
    g_video_h = (SOURCE_H * scale) / 100;
    g_hud_h = hud;
    g_client_w = g_video_w;
    g_client_h = g_video_h + g_hud_h;
}

} // namespace

int main() {
    _setmode(_fileno(stdin), _O_BINARY);
    SetProcessDPIAware();
    configure_host_geometry();
    timeBeginPeriod(1);
    if (HMODULE dwm = LoadLibraryA("dwmapi.dll")) {
        g_dwm_flush = reinterpret_cast<DwmFlushFn>(GetProcAddress(dwm, "DwmFlush"));
    }

    HINSTANCE inst = GetModuleHandle(nullptr);
    WNDCLASSEXA wc{};
    wc.cbSize = sizeof(wc);
    wc.style = CS_OWNDC;
    wc.lpfnWndProc = wnd_proc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hbrBackground = nullptr; // critical: Windows never erases behind our retained frame
    wc.lpszClassName = "DMS1NativeHostP109";
    if (!RegisterClassExA(&wc)) {
        emit_line("ERROR RegisterClassEx");
        timeEndPeriod(1);
        return 2;
    }

    RECT wr{0, 0, g_client_w, g_client_h};
    const DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    AdjustWindowRect(&wr, style, FALSE);
    HWND hwnd = CreateWindowExA(
        0, wc.lpszClassName,
        "DAC MASTER - DMS-1 P1.0.9 FINAL RUNTIME LOCK",
        style,
        CW_USEDEFAULT, CW_USEDEFAULT, wr.right - wr.left, wr.bottom - wr.top,
        nullptr, nullptr, inst, nullptr);
    if (!hwnd) {
        emit_line("ERROR CreateWindow");
        timeEndPeriod(1);
        return 3;
    }
    g_hwnd = hwnd;
    if (!g_back.create(hwnd, g_client_w, g_client_h)) {
        emit_line("ERROR backbuffer");
        DestroyWindow(hwnd);
        timeEndPeriod(1);
        return 4;
    }
    PatBlt(g_back.dc, 0, 0, g_client_w, g_client_h, BLACKNESS);
    draw_text_lines(g_back.dc, g_hud);

    // Give the presentation process a little scheduling preference without
    // starving audio or the rest of Windows.
    SetPriorityClass(GetCurrentProcess(), ABOVE_NORMAL_PRIORITY_CLASS);
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL);
    ShowWindow(hwnd, SW_SHOW);
    UpdateWindow(hwnd);
    SetForegroundWindow(hwnd);
    SetFocus(hwnd);
    emit_line("READY");
    emit_pad();

    std::thread(reader_thread).detach();
    std::thread(telemetry_thread).detach();

    MSG msg{};
    while (GetMessage(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }

    g_alive.store(false);
    timeEndPeriod(1);
    return 0;
}
#endif
