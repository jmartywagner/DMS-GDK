#include "dms1_sample_codec.hpp"

#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <vector>

int main() {
    const std::vector<int16_t> pcm = {
        0, 1000, -1000, 5000, -5000, 12000, -12000, 32767, -32768, 1234, -4321};
    const auto encoded_a = dms1::encode_adpcm_a(pcm);
    const auto encoded_b = dms1::encode_adpcm_b(pcm);
    assert(encoded_a.size() == 256);
    assert(encoded_b.size() == 256);

    constexpr std::array<uint8_t, 24> expected_a = {
        7, 247, 247, 199, 5, 128, 128, 128, 8, 8, 128, 128,
        8, 128, 8, 128, 8, 128, 8, 128, 8, 128, 8, 128};
    constexpr std::array<uint8_t, 24> expected_b = {
        7, 247, 247, 247, 242, 128, 128, 128, 8, 128, 8, 128,
        8, 8, 128, 8, 128, 128, 8, 128, 8, 8, 128, 128};
    for (std::size_t index = 0; index < expected_a.size(); ++index) {
        assert(encoded_a[index] == expected_a[index]);
        assert(encoded_b[index] == expected_b[index]);
    }

    const auto resampled = dms1::resample_windowed_sinc(pcm, 44'100.0, dms1::kAdpcmARate);
    const std::vector<int16_t> expected_resampled = {1183, -313, 4706, 180, -8206};
    assert(resampled == expected_resampled);

    std::cout << "OK: WAV preparation and ADPCM-A/B encoders match the ROM toolchain\n";
    return 0;
}
