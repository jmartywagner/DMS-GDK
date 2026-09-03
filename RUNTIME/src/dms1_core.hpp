#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "dms1_output_stage.hpp"

namespace dms1 {

// Yamaha stereo routing bits used by both ADPCM engines and stored per sample
// resource in DMR. Samples remain mono; the decoder is routed after playback.
enum class HardwarePan : uint8_t {
    Right = 0x40,
    Left = 0x80,
    Center = 0xc0,
};

struct RenderConfig {
    uint32_t tail_ms = 500;
    uint32_t max_seconds = 60;
    bool trace = false;
    OutputStage output_stage = OutputStage::RawDigital;
    // Optional P0.6 ZTR1 trace emitted by the native Z80 driver.  When set,
    // the renderer consumes these real Z80-originated MMIO writes instead of
    // executing the legacy DSEQ driver.
    std::string native_trace_path;
};

struct RenderReport {
    size_t rom_bytes = 0;
    uint64_t frames = 0;
    uint32_t output_rate = 44'100;
    uint32_t peak = 0;
    uint64_t clipped_samples = 0;
    uint64_t dac_overload_samples = 0;
    uint64_t mixer_overload_samples = 0;
    OutputStage output_stage = OutputStage::RawDigital;
};

struct PcmRender {
    RenderReport report;
    std::vector<int16_t> interleaved_stereo;
};

// Live access to the exact same virtual machine used by the DMR renderer.
// Register writes are applied at the boundary preceding the next rendered
// host sample. The 24 MHz scheduler remains continuous across audio blocks.
class RealtimeCore final {
public:
    using RegisterWriteObserver = void (*)(void* context, uint64_t cycle,
                                           uint16_t address, uint8_t data) noexcept;

    explicit RealtimeCore(double output_rate = 48'000.0,
                          OutputStage output_stage = OutputStage::RawDigital);
    ~RealtimeCore();

    RealtimeCore(RealtimeCore&&) noexcept;
    RealtimeCore& operator=(RealtimeCore&&) noexcept;
    RealtimeCore(const RealtimeCore&) = delete;
    RealtimeCore& operator=(const RealtimeCore&) = delete;

    void set_output_rate(double output_rate);
    double output_rate() const noexcept;
    void set_output_stage(OutputStage output_stage);
    OutputStage output_stage() const noexcept;
    void reset();

    void write_register(uint16_t address, uint8_t data);
    uint8_t read_register(uint16_t address) const;

    // Optional zero-allocation tap used by the DMS recorder. It observes
    // successful external MMIO writes at the exact current 24 MHz cycle.
    void set_register_write_observer(RegisterWriteObserver observer,
                                     void* context) noexcept;

    // Replaces the cartridge sample bus without resetting FM/SSG state.
    // Both ADPCM engines are stopped before the memory becomes visible.
    void set_sample_memory(std::vector<uint8_t> memory);
    size_t sample_memory_size() const noexcept;

    void render(float* left, float* right, size_t frames);

    uint64_t current_cycle() const noexcept;
    uint64_t clipped_samples() const noexcept;
    uint64_t dac_overload_samples() const noexcept;
    uint64_t mixer_overload_samples() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

PcmRender render_rom_to_pcm(const std::string& rom_path,
                            const RenderConfig& config = {});

void write_pcm_to_wav(const std::string& wav_path,
                      const PcmRender& render);

RenderReport render_rom_to_wav(const std::string& rom_path,
                               const std::string& wav_path,
                               const RenderConfig& config = {});

} // namespace dms1
