#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace dms1 {

constexpr double kAdpcmARate = 8'000'000.0 / 432.0;
constexpr double kAdpcmBServiceRate = 8'000'000.0 / 144.0;
constexpr double kAdpcmBNominalRate = 26'000.0;
constexpr std::size_t kSamplePageBytes = 256;
constexpr std::size_t kSamplePageDecodedSamples = kSamplePageBytes * 2;

std::vector<int16_t> resample_windowed_sinc(const std::vector<int16_t>& source,
                                             double source_rate,
                                             double target_rate,
                                             int radius = 18);

std::vector<uint8_t> encode_adpcm_a(const std::vector<int16_t>& pcm);
std::vector<uint8_t> encode_adpcm_b(const std::vector<int16_t>& pcm);

} // namespace dms1
