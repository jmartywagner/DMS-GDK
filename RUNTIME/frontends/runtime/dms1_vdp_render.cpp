#include <cstdint>
#include <cstddef>
#include <cstring>
#include <vector>
#include <array>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

#if defined(_WIN32)
#define DMS_EXPORT extern "C" __declspec(dllexport)
#else
#define DMS_EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {
constexpr int HEIGHT = 224;
constexpr int WIDTH = 320;
constexpr int LOW_WIDTH = 256;
constexpr std::size_t VRAM_SIZE = 0x20000;
constexpr std::size_t CRAM_SIZE = 256;
constexpr int MAP_W = 64;
constexpr int MAP_H = 32;
constexpr int WORLD_W = MAP_W * 8;
constexpr int WORLD_H = MAP_H * 8;
constexpr int MAX_SPRITES = 128;
constexpr int TILE_BYTES = 32;
constexpr int MAX_TILES = 1024;

constexpr int BG_A_STANDARD_BASE = 0x08000;
constexpr int BG_B_STANDARD_BASE = 0x09000;
constexpr int SPRITE_TABLE_BASE = 0x0A000;
constexpr int BG_A_HIGH_BASE = 0x0B000;
constexpr int LINE_SCROLL_A_BASE = 0x0C000;
constexpr int LINE_SCROLL_B_BASE = 0x0C200;

constexpr uint16_t BG_TILE_MASK = 0x03FF;
constexpr uint16_t BG_PRIORITY = 0x2000;
constexpr uint16_t BG_HFLIP = 0x4000;
constexpr uint16_t BG_VFLIP = 0x8000;
constexpr uint16_t SPR_PRIORITY = 0x0008;
constexpr uint16_t SPR_HFLIP = 0x0010;
constexpr uint16_t SPR_VFLIP = 0x0020;
constexpr uint16_t SPR_SIZE16 = 0x0040;

struct Profile {
    int width;
    int palettes;
    int bg_a;
    int bg_b; // -1 = disabled
    bool line_scroll;
    int sprite_total;
    int sprite_per_scanline;
};

constexpr Profile PROFILES[5] = {
    {320, 4, BG_A_STANDARD_BASE, BG_B_STANDARD_BASE, false, 80, 20},
    {320, 8, BG_A_HIGH_BASE,     -1,                 false, 80, 20},
    {320, 4, BG_A_STANDARD_BASE, BG_B_STANDARD_BASE, true,  48, 12},
    {320, 4, BG_A_STANDARD_BASE, -1,                 false, 128, 32},
    {256, 8, BG_A_HIGH_BASE,     BG_B_STANDARD_BASE, false, 96, 24},
};

inline uint16_t be16(const uint8_t* p) {
    return static_cast<uint16_t>((static_cast<uint16_t>(p[0]) << 8) | p[1]);
}

inline int16_t s16(uint16_t v) {
    return static_cast<int16_t>(v);
}

inline uint8_t tile_pixel(const uint8_t* vram, int tile, int x, int y) {
    tile &= BG_TILE_MASK;
    const std::size_t off = static_cast<std::size_t>(tile) * TILE_BYTES + static_cast<std::size_t>(y) * 4 + static_cast<std::size_t>(x >> 1);
    const uint8_t packed = vram[off];
    return (x & 1) ? (packed & 0x0F) : ((packed >> 4) & 0x0F);
}

inline void rgb333(const uint8_t* cram, int index, uint8_t* rgb) {
    index &= 0x7F;
    const int off = index * 2;
    const uint16_t v = static_cast<uint16_t>(((cram[off] & 1u) << 8) | cram[off + 1]);
    const int r = (v >> 6) & 7;
    const int g = (v >> 3) & 7;
    const int b = v & 7;
    rgb[0] = static_cast<uint8_t>((r * 255 + 3) / 7);
    rgb[1] = static_cast<uint8_t>((g * 255 + 3) / 7);
    rgb[2] = static_cast<uint8_t>((b * 255 + 3) / 7);
}

inline int line_scroll(const uint8_t* vram, int base, int y) {
    const int off = base + (y % HEIGHT) * 2;
    return static_cast<int>(s16(be16(vram + off)));
}

void render_plane(const uint8_t* vram, const std::array<std::array<uint8_t,3>,128>& colors,
                  uint8_t* out, uint8_t* owner_pri, uint8_t* owner_layer,
                  const Profile& p, int base, int sx, int sy, int layer, int line_table) {
    const int width = p.width;
    const int palette_mask = p.palettes - 1;
    for (int y = 0; y < HEIGHT; ++y) {
        const int wy = (y + sy) & (WORLD_H - 1);
        const int ly = wy & 7;
        const int cy = wy >> 3;
        const int line_x = sx + (line_table >= 0 ? line_scroll(vram, line_table, y) : 0);
        const int row = y * width;
        for (int x = 0; x < width; ++x) {
            const int wx = (x + line_x) & (WORLD_W - 1);
            const int lx0 = wx & 7;
            const int cx = wx >> 3;
            const int moff = base + ((cy * MAP_W + cx) * 2);
            const uint16_t word = be16(vram + moff);
            const bool hflip = (word & BG_HFLIP) != 0;
            const bool vflip = (word & BG_VFLIP) != 0;
            const int px = hflip ? 7 - lx0 : lx0;
            const int py = vflip ? 7 - ly : ly;
            const uint8_t colour = tile_pixel(vram, word & BG_TILE_MASK, px, py);
            if (!colour) continue;
            const int pos = row + x;
            const uint8_t pri = (word & BG_PRIORITY) ? 1 : 0;
            if (pri < owner_pri[pos] || (pri == owner_pri[pos] && layer < owner_layer[pos])) continue;
            owner_pri[pos] = pri;
            owner_layer[pos] = static_cast<uint8_t>(layer);
            const int palette = ((word >> 10) & 7) & palette_mask;
            const auto& c = colors[palette * 16 + colour];
            const int poff = pos * 3;
            out[poff] = c[0]; out[poff + 1] = c[1]; out[poff + 2] = c[2];
        }
    }
}
}

DMS_EXPORT int dms1_vdp_render(
    const uint8_t* vram, std::size_t vram_size,
    const uint8_t* cram, std::size_t cram_size,
    int mode, int backdrop,
    int scroll_a_x, int scroll_a_y, int scroll_b_x, int scroll_b_y,
    uint8_t* out, std::size_t out_size, int* out_width) {
    if (!vram || !cram || !out || !out_width) return -1;
    if (vram_size < VRAM_SIZE || cram_size < CRAM_SIZE) return -2;
    if (mode < 0 || mode > 4) return -3;
    const Profile& p = PROFILES[mode];
    const std::size_t needed = static_cast<std::size_t>(p.width) * HEIGHT * 3;
    if (out_size < needed) return -4;
    *out_width = p.width;

    std::array<std::array<uint8_t,3>,128> colors{};
    for (int i = 0; i < 128; ++i) rgb333(cram, i, colors[i].data());
    const auto& back = colors[backdrop & 0x7F];
    for (std::size_t i = 0; i < static_cast<std::size_t>(p.width) * HEIGHT; ++i) {
        out[i*3] = back[0]; out[i*3+1] = back[1]; out[i*3+2] = back[2];
    }
    // P1.0.9: no per-frame heap allocation in the rasterizer. The old
    // std::vector temporaries allocated/freed ~140 KiB every frame and could
    // create allocator stalls, most visible in the two-plane STANDARD mode.
    thread_local std::array<uint8_t, WIDTH * HEIGHT> owner_pri{};
    thread_local std::array<uint8_t, WIDTH * HEIGHT> owner_layer{};
    const std::size_t owner_bytes = static_cast<std::size_t>(p.width) * HEIGHT;
    std::memset(owner_pri.data(), 0, owner_bytes);
    std::memset(owner_layer.data(), 0, owner_bytes);

    if (p.bg_b >= 0) {
        render_plane(vram, colors, out, owner_pri.data(), owner_layer.data(), p,
                     p.bg_b, scroll_b_x, scroll_b_y, 0,
                     p.line_scroll ? LINE_SCROLL_B_BASE : -1);
    }
    render_plane(vram, colors, out, owner_pri.data(), owner_layer.data(), p,
                 p.bg_a, scroll_a_x, scroll_a_y, 1,
                 p.line_scroll ? LINE_SCROLL_A_BASE : -1);

    std::array<uint8_t, HEIGHT> scanline_counts{};
    const int total = p.sprite_total < MAX_SPRITES ? p.sprite_total : MAX_SPRITES;
    const int palette_mask = p.palettes - 1;
    for (int index = 0; index < total; ++index) {
        const int off = SPRITE_TABLE_BASE + index * 8;
        const int sy = be16(vram + off) & 0x01FF;
        const int sx = be16(vram + off + 2) & 0x01FF;
        const int tile = be16(vram + off + 4) & BG_TILE_MASK;
        const uint16_t attr = be16(vram + off + 6);
        if (sx == 0x1FF && sy == 0x1FF) continue;
        const int size = (attr & SPR_SIZE16) ? 16 : 8;
        const int palette = (attr & 7) & palette_mask;
        const uint8_t pri = (attr & SPR_PRIORITY) ? 1 : 0;
        const bool hflip = (attr & SPR_HFLIP) != 0;
        const bool vflip = (attr & SPR_VFLIP) != 0;
        for (int oy = 0; oy < size; ++oy) {
            const int y = sy + oy;
            if (y >= HEIGHT) continue;
            if (scanline_counts[y] >= p.sprite_per_scanline) continue;
            ++scanline_counts[y];
            const int ly = vflip ? size - 1 - oy : oy;
            const int row = y * p.width;
            for (int ox = 0; ox < size; ++ox) {
                const int x = sx + ox;
                if (x >= p.width) continue;
                const int lx = hflip ? size - 1 - ox : ox;
                int subtile = tile, px = lx, py = ly;
                if (size == 16) {
                    const int sub_x = lx >> 3;
                    const int sub_y = ly >> 3;
                    px = lx & 7; py = ly & 7;
                    subtile = tile + sub_y * 2 + sub_x;
                }
                const uint8_t colour = tile_pixel(vram, subtile, px, py);
                if (!colour) continue;
                const int pos = row + x;
                if (pri < owner_pri[pos] || (pri == owner_pri[pos] && 2 < owner_layer[pos])) continue;
                owner_pri[pos] = pri;
                owner_layer[pos] = 2;
                const auto& c = colors[palette * 16 + colour];
                const int poff = pos * 3;
                out[poff] = c[0]; out[poff+1] = c[1]; out[poff+2] = c[2];
            }
        }
    }
    return 0;
}


// P1.0.3 Windows presentation path. Tk still owns the window, keyboard and HUD,
// but no longer creates/zooms/deletes PhotoImage objects for every DMS-1 frame.
// The exact same VDP raster is rendered above, then presented directly into the
// Tk viewport HWND with nearest-neighbour StretchDIBits. This is HOST I/O only;
// it does not alter any DMS-1 video rule.
DMS_EXPORT int dms1_vdp_present_win32(
    void* hwnd_value,
    const uint8_t* vram, std::size_t vram_size,
    const uint8_t* cram, std::size_t cram_size,
    int mode, int backdrop,
    int scroll_a_x, int scroll_a_y, int scroll_b_x, int scroll_b_y,
    int target_width, int target_height) {
#if defined(_WIN32)
    if (!hwnd_value || target_width <= 0 || target_height <= 0) return -20;
    thread_local std::vector<uint8_t> rgb(static_cast<std::size_t>(WIDTH) * HEIGHT * 3);
    thread_local std::vector<uint8_t> bgr(static_cast<std::size_t>(WIDTH) * HEIGHT * 3);
    int source_width = 0;
    const int rc = dms1_vdp_render(
        vram, vram_size, cram, cram_size,
        mode, backdrop, scroll_a_x, scroll_a_y, scroll_b_x, scroll_b_y,
        rgb.data(), rgb.size(), &source_width);
    if (rc != 0) return rc;

    const std::size_t pixels = static_cast<std::size_t>(source_width) * HEIGHT;
    for (std::size_t i = 0; i < pixels; ++i) {
        bgr[i * 3 + 0] = rgb[i * 3 + 2];
        bgr[i * 3 + 1] = rgb[i * 3 + 1];
        bgr[i * 3 + 2] = rgb[i * 3 + 0];
    }

    HWND hwnd = reinterpret_cast<HWND>(hwnd_value);
    HDC dc = GetDC(hwnd);
    if (!dc) return -21;

    // P1.0.5: true host-side double buffering. P1.0.3/4 visibly cleared the
    // live Tk viewport to black before StretchDIBits, so Windows could show the
    // intermediate black surface as flicker. We now compose the COMPLETE monitor
    // image in a memory DC and expose it with ONE final BitBlt.
    struct BackBuffer {
        HDC memdc = nullptr;
        HBITMAP bitmap = nullptr;
        HGDIOBJ old_bitmap = nullptr;
        int width = 0;
        int height = 0;
        ~BackBuffer() {
            if (memdc && old_bitmap) SelectObject(memdc, old_bitmap);
            if (bitmap) DeleteObject(bitmap);
            if (memdc) DeleteDC(memdc);
        }
        bool ensure(HDC ref, int w, int h) {
            if (memdc && bitmap && width == w && height == h) return true;
            if (memdc && old_bitmap) { SelectObject(memdc, old_bitmap); old_bitmap = nullptr; }
            if (bitmap) { DeleteObject(bitmap); bitmap = nullptr; }
            if (memdc) { DeleteDC(memdc); memdc = nullptr; }
            memdc = CreateCompatibleDC(ref);
            if (!memdc) return false;
            bitmap = CreateCompatibleBitmap(ref, w, h);
            if (!bitmap) { DeleteDC(memdc); memdc = nullptr; return false; }
            old_bitmap = SelectObject(memdc, bitmap);
            width = w; height = h;
            return true;
        }
    };
    thread_local BackBuffer back;
    if (!back.ensure(dc, target_width, target_height)) {
        ReleaseDC(hwnd, dc);
        return -23;
    }

    // Black letterbox/background is drawn OFF-SCREEN.
    PatBlt(back.memdc, 0, 0, target_width, target_height, BLACKNESS);

    int dst_w = target_width;
    int dst_h = target_height;
    if (source_width == LOW_WIDTH) {
        // Preserve the DMS-1 LOW RES presentation chosen in P0.8.1:
        // 256x224 -> 640x560 inside the permanent 960x672 monitor.
        dst_w = source_width * 5 / 2;
        dst_h = HEIGHT * 5 / 2;
    }
    const int dst_x = (target_width - dst_w) / 2;
    const int dst_y = (target_height - dst_h) / 2;

    BITMAPINFO bmi{};
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = source_width;
    bmi.bmiHeader.biHeight = -HEIGHT; // top-down DIB
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 24;
    bmi.bmiHeader.biCompression = BI_RGB;
    SetStretchBltMode(back.memdc, COLORONCOLOR); // nearest-neighbour / 1990-style pixels
    const int lines = StretchDIBits(
        back.memdc,
        dst_x, dst_y, dst_w, dst_h,
        0, 0, source_width, HEIGHT,
        bgr.data(), &bmi, DIB_RGB_COLORS, SRCCOPY);
    if (lines == GDI_ERROR) {
        ReleaseDC(hwnd, dc);
        return -22;
    }

    // The only write to the visible monitor is this complete-frame blit.
    const BOOL ok = BitBlt(dc, 0, 0, target_width, target_height, back.memdc, 0, 0, SRCCOPY);
    ReleaseDC(hwnd, dc);
    return ok ? 0 : -24;

#else
    (void)hwnd_value; (void)vram; (void)vram_size; (void)cram; (void)cram_size;
    (void)mode; (void)backdrop; (void)scroll_a_x; (void)scroll_a_y;
    (void)scroll_b_x; (void)scroll_b_y; (void)target_width; (void)target_height;
    return -100;
#endif
}
