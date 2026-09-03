#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace dms1 {

// Temporary P0.3.4 comparison switch. RAW DIGITAL is the exact P0.3.3
// summing path; DMS-1 HARDWARE inserts the historically modelled output card.
enum class OutputStage : uint8_t {
    RawDigital,
    Hardware,
};

const char* output_stage_name(OutputStage stage) noexcept;
const char* output_stage_cli_name(OutputStage stage) noexcept;

struct HardwareOutputInput {
    std::array<int32_t, 2> fm{};
    std::array<int32_t, 3> ssg{};
    std::array<int32_t, 2> adpcm_a{};
    std::array<int32_t, 2> adpcm_b{};
    uint8_t gain_fm = 12;
    uint8_t gain_ssg = 20;
    uint8_t gain_a = 12;
    uint8_t gain_b = 12;
    uint8_t gain_master = 4;
    std::array<uint8_t, 3> ssg_routes {0xc0, 0xc0, 0xc0};
};

struct HardwareOutputStats {
    uint64_t dac_overload_samples = 0;
    uint64_t mixer_overload_samples = 0;
    uint64_t capture_clipped_samples = 0;
};

// Analogue-output card evaluated at four times the host rate. The individual
// chip outputs remain zero-order held by Machine at their own native clocks.
// A fixed anti-alias capture filter then returns one host-rate frame.
class HardwareOutputStage final {
public:
    static constexpr uint32_t kOversampleFactor = 4;
    static constexpr size_t kCaptureTaps = 129;

    explicit HardwareOutputStage(double host_rate = 44'100.0);

    void reset(double host_rate);
    void process_subsample(const HardwareOutputInput& input,
                           HardwareOutputStats& stats);
    std::array<int16_t, 2> capture_frame(HardwareOutputStats& stats) const;

    double host_rate() const noexcept { return host_rate_; }
    uint32_t latency_frames() const noexcept {
        return static_cast<uint32_t>((kCaptureTaps - 1) /
                                     (2 * kOversampleFactor));
    }

    // YM3012/YM3016-family 16-bit dynamic range: signed 10-bit mantissa and
    // seven exponent ranges. Exposed for deterministic hardware-model tests.
    static int16_t quantize_floating_dac(int16_t sample) noexcept;

private:
    struct DcBlocker {
        void configure(double sample_rate, double cutoff_hz);
        void reset() noexcept;
        double process(double input) noexcept;

        double alpha = 0.0;
        double previous_input = 0.0;
        double previous_output = 0.0;
    };

    struct LowPass {
        void configure(double sample_rate, double cutoff_hz, double q);
        void reset() noexcept;
        double process(double input) noexcept;

        double b0 = 0.0;
        double b1 = 0.0;
        double b2 = 0.0;
        double a1 = 0.0;
        double a2 = 0.0;
        double z1 = 0.0;
        double z2 = 0.0;
    };

    static int64_t apply_gain(int64_t value, uint8_t reg);
    static int16_t saturate_dac(int64_t value, HardwareOutputStats& stats) noexcept;
    static double limit_mixer(double value, HardwareOutputStats& stats) noexcept;
    static double bessel_i0(double value) noexcept;
    void configure_capture_filter();
    void push_capture(size_t channel, double sample) noexcept;
    double capture(size_t channel) const noexcept;

    double host_rate_ = 44'100.0;
    double internal_rate_ = 176'400.0;
    std::array<DcBlocker, 2> fm_coupling_{};
    std::array<DcBlocker, 2> pcm_coupling_{};
    std::array<DcBlocker, 2> ssg_coupling_{};
    std::array<LowPass, 2> reconstruction_{};
    std::array<double, kCaptureTaps> capture_coefficients_{};
    std::array<std::array<double, kCaptureTaps>, 2> capture_history_{};
    size_t capture_cursor_ = 0;
};

} // namespace dms1
