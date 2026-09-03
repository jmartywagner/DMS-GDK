#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

#include "dms1_core.hpp"
#include "ymfm_adpcm.h"
#include "ymfm_misc.h"
#include "ymfm_opz.h"

namespace dms1 {

constexpr uint64_t kSystemClock = 24'000'000;
// Official cartridge/player capture profile. Chip clocks remain independent;
// 44.1 kHz is only the final 16-bit host sampling domain.
constexpr uint32_t kOfflineOutputRate = 44'100;
constexpr uint32_t kOpzClock = 3'579'545;
constexpr uint64_t kOpzTickNumerator = 64ULL * kSystemClock;
// ymfm::ssg_engine::clock() represents CLK/8. With the decided 2 MHz SSG
// clock it must therefore run at 250 kHz, once every 96 master cycles. The
// previous 768-cycle period divided all tones by eight (three octaves).
constexpr uint64_t kSsgOutputPeriod = 96;
constexpr uint64_t kAdpcmATickPeriod = 1296;
constexpr uint64_t kAdpcmBTickPeriod = 432;
constexpr uint32_t kAdpcmBServiceRateNumerator = 8'000'000;
constexpr uint32_t kAdpcmBServiceRateDenominator = 144;
constexpr uint16_t kDelta26k = 0x77cf;
// Fixed analogue-output calibration: +9 dB, represented as Q20. It is
// applied after the programmable master attenuation and before saturation.
constexpr int64_t kOutputGainQ20 = 2'955'289;

class Error : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

uint16_t read_be16(const std::vector<uint8_t>& data, size_t offset) {
    if (offset > data.size() || data.size() - offset < 2) {
        throw Error("lecture 16 bits hors ROM");
    }
    return static_cast<uint16_t>((uint16_t(data[offset]) << 8) | data[offset + 1]);
}

uint32_t read_be32(const std::vector<uint8_t>& data, size_t offset) {
    if (offset > data.size() || data.size() - offset < 4) {
        throw Error("lecture 32 bits hors ROM");
    }
    return (uint32_t(data[offset]) << 24) |
           (uint32_t(data[offset + 1]) << 16) |
           (uint32_t(data[offset + 2]) << 8) |
           uint32_t(data[offset + 3]);
}

uint64_t read_be64(const std::vector<uint8_t>& data, size_t offset) {
    if (offset > data.size() || data.size() - offset < 8) {
        throw Error("lecture 64 bits hors fichier");
    }
    uint64_t value = 0;
    for (unsigned index = 0; index < 8; ++index) {
        value = (value << 8) | data[offset + index];
    }
    return value;
}

std::vector<uint8_t> read_file(const std::string& path) {
    std::ifstream stream(path.c_str(), std::ios::binary | std::ios::ate);
    if (!stream) {
        throw Error("impossible d'ouvrir " + path);
    }
    const auto end = stream.tellg();
    if (end < 0) {
        throw Error("taille de fichier invalide");
    }
    std::vector<uint8_t> bytes(static_cast<size_t>(end));
    stream.seekg(0, std::ios::beg);
    if (!bytes.empty() && !stream.read(reinterpret_cast<char*>(bytes.data()), end)) {
        throw Error("lecture incomplète de " + path);
    }
    return bytes;
}

struct Chunk {
    std::string type;
    uint32_t offset = 0;
    uint32_t size = 0;
    uint32_t flags = 0;
};

struct SampleEntry {
    uint16_t id = 0;
    uint8_t codec = 0;
    uint8_t flags = 0;
    uint16_t start_page = 0;
    uint16_t end_page = 0;
    uint32_t source_rate = 0;
    uint8_t level = 0;
    uint8_t pan = 0;
    uint8_t root_note = 0;
    int8_t fine_cents = 0;
};

class RomImage {
public:
    static RomImage load(const std::string& path) {
        RomImage image;
        image.bytes_ = read_file(path);
        image.parse();
        return image;
    }

    const std::vector<uint8_t>& bytes() const { return bytes_; }
    const Chunk& code() const { return *code_; }
    uint32_t entrypoint() const { return entrypoint_; }
    uint32_t clock_profile() const { return clock_profile_; }
    uint32_t timebase() const { return timebase_; }
    const std::vector<SampleEntry>& samples() const { return samples_; }

    const SampleEntry& sample(uint16_t id) const {
        const auto found = std::find_if(samples_.begin(), samples_.end(),
                                        [id](const SampleEntry& sample) { return sample.id == id; });
        if (found == samples_.end()) {
            std::ostringstream message;
            message << "sample ID " << id << " absent du SDIR";
            throw Error(message.str());
        }
        return *found;
    }

private:
    void require_range(uint32_t offset, uint32_t size, const std::string& what) const {
        if (offset > bytes_.size() || size > bytes_.size() - offset) {
            throw Error(what + " hors limites de la ROM");
        }
    }

    void parse() {
        if (bytes_.size() < 64) {
            throw Error("ROM plus petite que le header DMR");
        }
        if (std::memcmp(bytes_.data(), "DMR0", 4) != 0) {
            throw Error("magic DMR0 absent");
        }
        const uint16_t major = read_be16(bytes_, 0x04);
        const uint16_t minor = read_be16(bytes_, 0x06);
        if (major != 0 || minor != 1) {
            throw Error("version DMR non prise en charge (attendu 0.1)");
        }
        if (read_be16(bytes_, 0x08) != 64) {
            throw Error("taille de header DMR invalide");
        }
        const uint32_t declared_size = read_be32(bytes_, 0x0c);
        if (declared_size != bytes_.size() || declared_size > 0x01000000) {
            throw Error("taille DMR déclarée incohérente ou supérieure à 16 Mio");
        }
        if (std::memcmp(bytes_.data() + 0x10, "DMS1", 4) != 0) {
            throw Error("hardware ID différent de DMS1");
        }
        clock_profile_ = read_be32(bytes_, 0x14);
        if (clock_profile_ != 1) {
            throw Error("ce prototype n'accepte que le profil NATIVE89");
        }
        const uint32_t directory_offset = read_be32(bytes_, 0x18);
        const uint16_t directory_count = read_be16(bytes_, 0x1c);
        const uint16_t directory_entry_size = read_be16(bytes_, 0x1e);
        entrypoint_ = read_be32(bytes_, 0x20);
        timebase_ = read_be32(bytes_, 0x24);
        if (timebase_ != kSystemClock) {
            throw Error("timebase DMR différente de 24 MHz");
        }
        if (directory_entry_size != 16) {
            throw Error("taille d'entrée de répertoire différente de 16");
        }
        for (size_t index = 0x28; index < 0x40; ++index) {
            if (bytes_[index] != 0) {
                throw Error("octets réservés du header non nuls");
            }
        }
        const uint64_t directory_size = uint64_t(directory_count) * directory_entry_size;
        if (directory_size > std::numeric_limits<uint32_t>::max()) {
            throw Error("répertoire DMR trop grand");
        }
        require_range(directory_offset, static_cast<uint32_t>(directory_size), "répertoire DMR");

        chunks_.reserve(directory_count);
        for (uint16_t index = 0; index < directory_count; ++index) {
            const size_t base = directory_offset + size_t(index) * directory_entry_size;
            Chunk chunk;
            chunk.type.assign(reinterpret_cast<const char*>(bytes_.data() + base), 4);
            chunk.offset = read_be32(bytes_, base + 4);
            chunk.size = read_be32(bytes_, base + 8);
            chunk.flags = read_be32(bytes_, base + 12);
            require_range(chunk.offset, chunk.size, "chunk " + chunk.type);
            if (std::find_if(chunks_.begin(), chunks_.end(), [&](const Chunk& other) {
                    return other.type == chunk.type;
                }) != chunks_.end()) {
                throw Error("chunk DMR dupliqué : " + chunk.type);
            }
            chunks_.push_back(chunk);
        }

        const auto code = find_chunk("CODE");
        if (!code) {
            throw Error("chunk CODE obligatoire absent");
        }
        code_ = &chunks_[*code];
        if (entrypoint_ < code_->offset || entrypoint_ >= uint64_t(code_->offset) + code_->size) {
            throw Error("entrypoint hors du chunk CODE");
        }

        const auto sdir = find_chunk("SDIR");
        const auto samp = find_chunk("SAMP");
        if (sdir.has_value() != samp.has_value()) {
            throw Error("SDIR et SAMP doivent être présents ensemble");
        }
        if (sdir) {
            parse_samples(chunks_[*sdir], chunks_[*samp]);
        }
    }

    std::optional<size_t> find_chunk(const std::string& type) const {
        for (size_t index = 0; index < chunks_.size(); ++index) {
            if (chunks_[index].type == type) {
                return index;
            }
        }
        return std::nullopt;
    }

    void parse_samples(const Chunk& directory, const Chunk& data) {
        if ((directory.size % 16) != 0) {
            throw Error("taille SDIR non multiple de 16");
        }
        if ((data.offset & 0xff) != 0 || (data.size & 0xff) != 0) {
            throw Error("chunk SAMP non aligné sur des pages de 256 octets");
        }
        const uint32_t samp_first_page = data.offset >> 8;
        const uint32_t samp_last_page = (data.offset + data.size - 1) >> 8;
        for (uint32_t cursor = directory.offset; cursor < directory.offset + directory.size; cursor += 16) {
            SampleEntry sample;
            sample.id = read_be16(bytes_, cursor);
            sample.codec = bytes_[cursor + 2];
            sample.flags = bytes_[cursor + 3];
            sample.start_page = read_be16(bytes_, cursor + 4);
            sample.end_page = read_be16(bytes_, cursor + 6);
            sample.source_rate = read_be32(bytes_, cursor + 8);
            sample.level = bytes_[cursor + 12];
            sample.pan = bytes_[cursor + 13];
            sample.root_note = bytes_[cursor + 14];
            sample.fine_cents = static_cast<int8_t>(bytes_[cursor + 15]);
            if (sample.codec != 1 && sample.codec != 2) {
                throw Error("codec SDIR inconnu");
            }
            if (sample.start_page > sample.end_page ||
                sample.start_page < samp_first_page || sample.end_page > samp_last_page) {
                throw Error("plage de pages SDIR hors du chunk SAMP");
            }
            if (std::find_if(samples_.begin(), samples_.end(), [&](const SampleEntry& other) {
                    return other.id == sample.id;
                }) != samples_.end()) {
                throw Error("sample ID dupliqué dans SDIR");
            }
            samples_.push_back(sample);
        }
    }

    std::vector<uint8_t> bytes_;
    std::vector<Chunk> chunks_;
    const Chunk* code_ = nullptr;
    uint32_t entrypoint_ = 0;
    uint32_t clock_profile_ = 0;
    uint32_t timebase_ = 0;
    std::vector<SampleEntry> samples_;
};

class ChipInterface : public ymfm::ymfm_interface {
public:
    explicit ChipInterface(const std::vector<uint8_t>& rom) : rom_(rom) {}

    uint8_t ymfm_external_read(ymfm::access_class type, uint32_t address) override {
        if ((type == ymfm::ACCESS_ADPCM_A || type == ymfm::ACCESS_ADPCM_B) && address < rom_.size()) {
            return rom_[address];
        }
        return 0;
    }

private:
    const std::vector<uint8_t>& rom_;
};

class Machine {
public:
    explicit Machine(const std::vector<uint8_t>& sample_rom, bool trace)
        : interface_(sample_rom), opz_(interface_), ssg_(interface_),
          adpcm_a_(interface_, 8), adpcm_b_(interface_, 8), trace_(trace) {
        opz_.reset();
        ssg_.reset();
        adpcm_a_.reset();
        adpcm_b_.reset();
        sync_adpcm_a_registers();
        fm_shadow_.fill(0);
        fm_alt_eg_.fill(0x20);
    }

    explicit Machine(const RomImage& rom, bool trace)
        : Machine(rom.bytes(), trace) {}

    void write(uint16_t address, uint8_t data) {
        if (address <= 0x00ff) {
            write_opz(static_cast<uint8_t>(address), data);
            return;
        }
        if (address >= 0x0100 && address <= 0x010f) {
            const uint8_t reg = static_cast<uint8_t>(address - 0x0100);
            if (reg >= 0x0e) {
                return;
            }
            ssg_.write_address(reg);
            ssg_.write_data(data);
            return;
        }
        if (address >= 0x0120 && address <= 0x012f) {
            write_adpcm_a(static_cast<uint8_t>(address - 0x0120), data);
            return;
        }
        if (address >= 0x0140 && address <= 0x015f) {
            write_adpcm_b(static_cast<uint8_t>(address - 0x0140), data);
            return;
        }
        if (address >= 0x0180 && address <= 0x019f) {
            write_system(static_cast<uint8_t>(address - 0x0180), data);
            return;
        }
        if (address <= 0x01ff) {
            if (data != 0) {
                throw Error("écriture non nulle dans une zone MMIO réservée");
            }
            return;
        }
        std::ostringstream message;
        message << "écriture MMIO hors DMS-1 : $" << std::hex << std::setw(4)
                << std::setfill('0') << address;
        throw Error(message.str());
    }

    uint8_t read(uint16_t address) const {
        if (address == 0x0123) {
            const auto voice = static_cast<size_t>(a_selected_voice_);
            return static_cast<uint8_t>((a_busy_[voice] ? 0x01 : 0) | (a_eos_[voice] ? 0x02 : 0));
        }
        if (address == 0x0192) {
            uint8_t mask = 0;
            for (size_t voice = 0; voice < kAdpcmAMixVoices; ++voice)
                if (a_busy_[voice]) mask |= static_cast<uint8_t>(1U << voice);
            return mask;
        }
        if (address == 0x0193) {
            uint8_t mask = 0;
            for (size_t voice = 0; voice < kAdpcmAMixVoices; ++voice)
                if (a_eos_[voice]) mask |= static_cast<uint8_t>(1U << voice);
            return mask;
        }
        if (address == 0x014c) {
            const uint8_t status = adpcm_b_.status();
            return static_cast<uint8_t>((status & 0x04 ? 0x01 : 0) | (status & 0x01 ? 0x02 : 0));
        }
        if (address == 0x0180) return 0xd1;
        if (address == 0x0181) return 0x03;
        if (address == 0x0182) return 0x01;
        return 0xff;
    }

    void play_a(const SampleEntry& sample, uint8_t level, uint8_t pan) {
        if (sample.codec != 1) {
            throw Error("PLAY_A cible un sample qui n'est pas ADPCM-A");
        }
        const auto voice = static_cast<size_t>(a_selected_voice_);
        const bool retrigger = a_busy_[voice];
        write_adpcm_a(0x00, 0x02);
        write_adpcm_a(0x01, pan);
        write_adpcm_a(0x02, level);
        write_adpcm_a(0x04, static_cast<uint8_t>(sample.start_page));
        write_adpcm_a(0x05, static_cast<uint8_t>(sample.start_page >> 8));
        write_adpcm_a(0x06, static_cast<uint8_t>(sample.end_page));
        write_adpcm_a(0x07, static_cast<uint8_t>(sample.end_page >> 8));
        write_adpcm_a(0x00, 0x01);
        if (trace_) {
            std::cout << "PLAY_A id=" << sample.id << " voice=" << unsigned(a_selected_voice_)
                      << " rate=18518.519 Hz pages="
                      << (sample.end_page - sample.start_page + 1)
                      << " retrigger=" << (retrigger ? 1 : 0) << "\n";
        }
    }

    void stop_a() {
        const uint8_t saved = a_selected_voice_;
        for (uint8_t voice = 0; voice < kAdpcmAMixVoices; ++voice) {
            a_selected_voice_ = voice;
            write_adpcm_a(0x00, 0x02);
        }
        a_selected_voice_ = saved;
    }

    void play_b(const SampleEntry& sample, uint16_t delta_n, uint8_t level,
                uint8_t pan, uint8_t flags) {
        if (sample.codec != 2) {
            throw Error("PLAY_B cible un sample qui n'est pas ADPCM-B");
        }
        write_adpcm_b(0x00, 0x01);
        write_adpcm_b(0x01, pan);
        write_adpcm_b(0x02, static_cast<uint8_t>(sample.start_page));
        write_adpcm_b(0x03, static_cast<uint8_t>(sample.start_page >> 8));
        write_adpcm_b(0x04, static_cast<uint8_t>(sample.end_page));
        write_adpcm_b(0x05, static_cast<uint8_t>(sample.end_page >> 8));
        write_adpcm_b(0x09, static_cast<uint8_t>(delta_n));
        write_adpcm_b(0x0a, static_cast<uint8_t>(delta_n >> 8));
        write_adpcm_b(0x0b, level);
        write_adpcm_b(0x00, static_cast<uint8_t>(0x80 | ((flags & 1) ? 0x10 : 0)));
        if (trace_) {
            const double rate = (double(kAdpcmBServiceRateNumerator) /
                                 double(kAdpcmBServiceRateDenominator)) * delta_n / 65536.0;
            std::cout << "PLAY_B id=" << sample.id << " Delta-N=$" << std::hex
                      << std::setw(4) << std::setfill('0') << delta_n << std::dec
                      << std::setfill(' ') << " rate=" << std::fixed << std::setprecision(3)
                      << rate << " Hz\n";
        }
    }

    void stop_b() { write_adpcm_b(0x00, 0x00); }

    void advance_before(uint64_t cycle) {
        while (opz_tick_relation(next_opz_tick_, cycle) < 0) {
            clock_opz();
        }
        while (next_ssg_cycle_ < cycle) {
            clock_ssg();
        }
        while (next_a_cycle_ < cycle) {
            clock_adpcm_a();
        }
        while (next_b_cycle_ < cycle) {
            clock_adpcm_b();
        }
    }

    void advance_at(uint64_t cycle) {
        while (opz_tick_relation(next_opz_tick_, cycle) == 0) {
            clock_opz();
        }
        while (next_ssg_cycle_ == cycle) {
            clock_ssg();
        }
        while (next_a_cycle_ == cycle) {
            clock_adpcm_a();
        }
        while (next_b_cycle_ == cycle) {
            clock_adpcm_b();
        }
    }

    std::array<int16_t, 2> mix(uint64_t& clipped_samples) const {
        int64_t left = apply_gain(fm_output_[0], gain_fm_);
        int64_t right = apply_gain(fm_output_[1], gain_fm_);

        for (std::size_t channel = 0; channel < ssg_output_.size(); ++channel) {
            const int64_t routed_ssg = apply_gain(ssg_output_[channel], gain_ssg_);
            if (ssg_routes_[channel] & 0x80) left += routed_ssg;
            if (ssg_routes_[channel] & 0x40) right += routed_ssg;
        }

        left += apply_gain(adpcm_a_output_[0], gain_a_);
        right += apply_gain(adpcm_a_output_[1], gain_a_);
        left += apply_gain(adpcm_b_output_[0], gain_b_);
        right += apply_gain(adpcm_b_output_[1], gain_b_);
        left = apply_gain(left, gain_master_);
        right = apply_gain(right, gain_master_);
        left = apply_output_gain(left);
        right = apply_output_gain(right);
        clipped_samples += left < -32768 || left > 32767;
        clipped_samples += right < -32768 || right > 32767;
        return {saturate16(left), saturate16(right)};
    }

    HardwareOutputInput hardware_output() const {
        HardwareOutputInput output;
        output.fm = fm_output_;
        output.ssg = ssg_output_;
        output.adpcm_a = adpcm_a_output_;
        output.adpcm_b = adpcm_b_output_;
        output.gain_fm = gain_fm_;
        output.gain_ssg = gain_ssg_;
        output.gain_a = gain_a_;
        output.gain_b = gain_b_;
        output.gain_master = gain_master_;
        output.ssg_routes = ssg_routes_;
        return output;
    }

private:
    static int opz_tick_relation(uint64_t tick, uint64_t cycle) {
        if (tick > std::numeric_limits<uint64_t>::max() / kOpzTickNumerator ||
            cycle > std::numeric_limits<uint64_t>::max() / kOpzClock) {
            throw Error("débordement du scheduler OPZ");
        }
        const uint64_t lhs = tick * kOpzTickNumerator;
        const uint64_t rhs = cycle * uint64_t(kOpzClock);
        return lhs < rhs ? -1 : (lhs > rhs ? 1 : 0);
    }

    static int16_t saturate16(int64_t value) {
        return static_cast<int16_t>(std::clamp<int64_t>(value, -32768, 32767));
    }

    static int64_t apply_gain(int64_t value, uint8_t reg) {
        if (reg & 0x80) {
            return 0;
        }
        static const std::array<int32_t, 64> table = [] {
            std::array<int32_t, 64> result{};
            for (size_t step = 0; step < result.size(); ++step) {
                const double db = -0.75 * double(step);
                result[step] = static_cast<int32_t>(std::llround(std::pow(10.0, db / 20.0) * (1 << 20)));
            }
            return result;
        }();
        const int32_t coefficient = table[reg & 0x3f];
        const int64_t product = value * coefficient;
        return product >= 0 ? (product + (1 << 19)) >> 20 : -(((-product) + (1 << 19)) >> 20);
    }

    static int64_t apply_output_gain(int64_t value) {
        const int64_t product = value * kOutputGainQ20;
        return product >= 0 ? (product + (1 << 19)) >> 20 : -(((-product) + (1 << 19)) >> 20);
    }

    void write_opz_raw(uint8_t reg, uint8_t data) {
        opz_.write_address(reg);
        opz_.write_data(data);
    }

    void select_opz_channel(uint8_t channel) {
        write_opz_raw(0x08, channel);
        for (uint8_t group = 0; group < 4; ++group) {
            const uint8_t offset = static_cast<uint8_t>(channel + group * 8);
            write_opz_raw(static_cast<uint8_t>(0xe0 + offset), fm_shadow_[0xe0 + offset]);
            write_opz_raw(static_cast<uint8_t>(0xc0 + offset), fm_alt_eg_[offset]);
        }
    }

    void write_opz(uint8_t reg, uint8_t data) {
        const bool channel_register = reg <= 0x07 || reg >= 0x20;
        if (channel_register && (reg & 0x07) >= 4) {
            return;
        }
        if (reg == 0x08 || reg == 0x0f) {
            return;
        }

        if ((reg & 0xe0) == 0xc0 && (data & 0x20)) {
            fm_alt_eg_[reg & 0x1f] = data;
        } else {
            fm_shadow_[reg] = data;
        }

        if ((reg & 0xf8) == 0x20) {
            select_opz_channel(reg & 0x07);
        }
        write_opz_raw(reg, data);
    }

    static constexpr size_t kAdpcmAMixVoices = 3;

    void sync_adpcm_a_voice(size_t voice) {
        const auto channel = static_cast<uint8_t>(voice);
        adpcm_a_.write(static_cast<uint32_t>(0x08 + channel),
                       static_cast<uint8_t>(a_pan_[voice] | ((a_level_[voice] ^ 0x1f) & 0x1f)));
        adpcm_a_.write(static_cast<uint32_t>(0x10 + channel), static_cast<uint8_t>(a_start_page_[voice]));
        adpcm_a_.write(static_cast<uint32_t>(0x18 + channel), static_cast<uint8_t>(a_start_page_[voice] >> 8));
        adpcm_a_.write(static_cast<uint32_t>(0x20 + channel), static_cast<uint8_t>(a_end_page_[voice]));
        adpcm_a_.write(static_cast<uint32_t>(0x28 + channel), static_cast<uint8_t>(a_end_page_[voice] >> 8));
    }

    void sync_adpcm_a_registers() {
        adpcm_a_.write(0x01, 0x3f);
        for (size_t voice = 0; voice < kAdpcmAMixVoices; ++voice)
            sync_adpcm_a_voice(voice);
    }

    void write_adpcm_a(uint8_t reg, uint8_t data) {
        const auto voice = static_cast<size_t>(a_selected_voice_);
        const uint8_t bit = static_cast<uint8_t>(1U << a_selected_voice_);
        switch (reg) {
        case 0x00:
            if (data & 0x80) {
                adpcm_a_.reset();
                a_busy_.fill(false);
                a_eos_.fill(false);
                sync_adpcm_a_registers();
            }
            if (data & 0x02) {
                adpcm_a_.write(0x00, static_cast<uint8_t>(0x80 | bit));
                a_busy_[voice] = false;
            }
            if (data & 0x01) {
                if (a_start_page_[voice] > a_end_page_[voice]) {
                    throw Error("ADPCM-A start page supérieure à end page");
                }
                adpcm_a_.write(0x00, bit);
                a_busy_[voice] = true;
                a_eos_[voice] = false;
            }
            break;
        case 0x01:
            a_pan_[voice] = data & 0xc0;
            adpcm_a_.write(static_cast<uint32_t>(0x08 + a_selected_voice_),
                           static_cast<uint8_t>(a_pan_[voice] | ((a_level_[voice] ^ 0x1f) & 0x1f)));
            break;
        case 0x02:
            a_level_[voice] = data & 0x1f;
            adpcm_a_.write(static_cast<uint32_t>(0x08 + a_selected_voice_),
                           static_cast<uint8_t>(a_pan_[voice] | ((a_level_[voice] ^ 0x1f) & 0x1f)));
            break;
        case 0x03:
            if (data & 0x02) a_eos_[voice] = false;
            break;
        case 0x04:
            a_start_page_[voice] = static_cast<uint16_t>((a_start_page_[voice] & 0xff00) | data);
            adpcm_a_.write(static_cast<uint32_t>(0x10 + a_selected_voice_), data);
            break;
        case 0x05:
            a_start_page_[voice] = static_cast<uint16_t>((a_start_page_[voice] & 0x00ff) | (uint16_t(data) << 8));
            adpcm_a_.write(static_cast<uint32_t>(0x18 + a_selected_voice_), data);
            break;
        case 0x06:
            a_end_page_[voice] = static_cast<uint16_t>((a_end_page_[voice] & 0xff00) | data);
            adpcm_a_.write(static_cast<uint32_t>(0x20 + a_selected_voice_), data);
            break;
        case 0x07:
            a_end_page_[voice] = static_cast<uint16_t>((a_end_page_[voice] & 0x00ff) | (uint16_t(data) << 8));
            adpcm_a_.write(static_cast<uint32_t>(0x28 + a_selected_voice_), data);
            break;
        default:
            if (data != 0) {
                throw Error("écriture non nulle dans un registre ADPCM-A réservé");
            }
            break;
        }
    }

    void write_adpcm_b(uint8_t reg, uint8_t data) {
        switch (reg) {
        case 0x00: {
            b_ctrl_ = data;
            if (data & 0x01) {
                adpcm_b_.write(0x00, 0x21);
            } else if (data & 0x80) {
                adpcm_b_.write(0x00, static_cast<uint8_t>(0xa0 | (data & 0x10)));
            } else {
                adpcm_b_.write(0x00, 0x20);
            }
            break;
        }
        case 0x01:
            b_pan_ = data & 0xc0;
            adpcm_b_.write(0x01, static_cast<uint8_t>(b_pan_ | 0x01));
            break;
        case 0x02:
        case 0x03:
        case 0x04:
        case 0x05:
        case 0x09:
        case 0x0a:
        case 0x0b:
            adpcm_b_.write(reg, data);
            break;
        case 0x0c:
            if (data & 0x02) {
                adpcm_b_.write(0x00, 0x20);
            }
            break;
        default:
            if (data != 0) {
                throw Error("écriture non nulle dans un registre ADPCM-B réservé");
            }
            break;
        }
    }

    void write_system(uint8_t reg, uint8_t data) {
        switch (reg) {
        case 0x08: gain_fm_ = data; break;
        case 0x09: gain_ssg_ = data; break;
        case 0x0a: gain_a_ = data; break;
        case 0x0b: gain_b_ = data; break;
        case 0x0c: gain_master_ = data; break;
        case 0x0d: // legacy/global SSG pan: keep old DMR files compatible
            ssg_routes_.fill(data & 0xc0);
            break;
        case 0x0e: ssg_routes_[0] = data & 0xc0; break;
        case 0x0f: ssg_routes_[1] = data & 0xc0; break;
        case 0x10: ssg_routes_[2] = data & 0xc0; break;
        case 0x11:
            if (data >= kAdpcmAMixVoices) throw Error("sélecteur voix ADPCM-A hors plage");
            a_selected_voice_ = data;
            break;
        default:
            if (data != 0) {
                throw Error("écriture dans un registre système en lecture seule ou réservé");
            }
            break;
        }
    }

    void clock_opz() {
        ymfm::ym2414::output_data output;
        opz_.generate(&output);
        fm_output_[0] = output.data[0];
        fm_output_[1] = output.data[1];
        ++next_opz_tick_;
    }

    void clock_ssg() {
        ymfm::ym2149::output_data output;
        ssg_.generate(&output);
        for (size_t channel = 0; channel < ssg_output_.size(); ++channel) {
            ssg_output_[channel] = output.data[channel];
        }
        next_ssg_cycle_ += kSsgOutputPeriod;
    }

    void clock_adpcm_a() {
        constexpr uint32_t mixMask = (1U << kAdpcmAMixVoices) - 1U;
        const uint32_t finished = adpcm_a_.clock(mixMask);
        for (size_t voice = 0; voice < kAdpcmAMixVoices; ++voice) {
            if (finished & (1U << voice)) {
                a_busy_[voice] = false;
                a_eos_[voice] = true;
                if (trace_) {
                    std::cout << "EOS_A voice=" << voice << " cycle=" << next_a_cycle_ << "\n";
                }
            }
        }
        ymfm::ymfm_output<2> output;
        adpcm_a_.output(output.clear(), mixMask);
        adpcm_a_output_[0] = output.data[0];
        adpcm_a_output_[1] = output.data[1];
        next_a_cycle_ += kAdpcmATickPeriod;
    }

    void clock_adpcm_b() {
        adpcm_b_.clock();
        ymfm::ymfm_output<2> output;
        adpcm_b_.output(output.clear(), 0);
        adpcm_b_output_[0] = output.data[0];
        adpcm_b_output_[1] = output.data[1];
        next_b_cycle_ += kAdpcmBTickPeriod;
    }

    ChipInterface interface_;
    ymfm::ym2414 opz_;
    ymfm::ym2149 ssg_;
    ymfm::adpcm_a_engine adpcm_a_;
    ymfm::adpcm_b_engine adpcm_b_;
    bool trace_ = false;

    std::array<uint8_t, 256> fm_shadow_{};
    std::array<uint8_t, 32> fm_alt_eg_{};
    std::array<int32_t, 2> fm_output_{};
    std::array<int32_t, 3> ssg_output_{};
    std::array<int32_t, 2> adpcm_a_output_{};
    std::array<int32_t, 2> adpcm_b_output_{};
    std::array<uint16_t, kAdpcmAMixVoices> a_start_page_{};
    std::array<uint16_t, kAdpcmAMixVoices> a_end_page_{};
    std::array<uint8_t, kAdpcmAMixVoices> a_pan_{0xc0, 0xc0, 0xc0};
    std::array<uint8_t, kAdpcmAMixVoices> a_level_{};
    std::array<bool, kAdpcmAMixVoices> a_busy_{};
    std::array<bool, kAdpcmAMixVoices> a_eos_{};
    uint8_t a_selected_voice_ = 0;
    uint8_t b_ctrl_ = 0;
    uint8_t b_pan_ = 0xc0;
    uint8_t gain_fm_ = 12;
    uint8_t gain_ssg_ = 20;
    uint8_t gain_a_ = 12;
    uint8_t gain_b_ = 12;
    uint8_t gain_master_ = 4;
    std::array<uint8_t, 3> ssg_routes_ {0xc0, 0xc0, 0xc0};

    uint64_t next_opz_tick_ = 1;
    uint64_t next_ssg_cycle_ = kSsgOutputPeriod;
    uint64_t next_a_cycle_ = kAdpcmATickPeriod;
    uint64_t next_b_cycle_ = kAdpcmBTickPeriod;
};

struct RealtimeCore::Impl {
    explicit Impl(double rate, OutputStage stage) : output_stage(stage) {
        configure_rate(rate);
        reset();
    }

    void configure_rate(double rate) {
        if (!std::isfinite(rate) || rate < 8'000.0 || rate > 384'000.0) {
            throw Error("frequence de sortie temps reel hors plage 8..384 kHz");
        }
        constexpr long double q32 = 4'294'967'296.0L;
        const long double step = (static_cast<long double>(kSystemClock) * q32) /
                                 static_cast<long double>(rate);
        const auto rounded = std::llround(step);
        if (rounded <= 0) {
            throw Error("pas du scheduler temps reel invalide");
        }
        output_rate = rate;
        cycles_per_sample_q32 = static_cast<uint64_t>(rounded);
        hardware.reset(rate);
    }

    void reset() {
        machine = std::make_unique<Machine>(sample_rom, false);
        next_sample_cycle = 0;
        cycle_fraction_q32 = 0;
        clipped = 0;
        hardware_stats = {};
        hardware.reset(output_rate);
    }

    std::vector<uint8_t> sample_rom;
    std::unique_ptr<Machine> machine;
    double output_rate = 48'000.0;
    OutputStage output_stage = OutputStage::RawDigital;
    HardwareOutputStage hardware;
    HardwareOutputStats hardware_stats;
    uint64_t cycles_per_sample_q32 = 0;
    uint64_t next_sample_cycle = 0;
    uint64_t cycle_fraction_q32 = 0;
    uint64_t clipped = 0;
    RegisterWriteObserver register_write_observer = nullptr;
    void* register_write_context = nullptr;
};

RealtimeCore::RealtimeCore(double output_rate, OutputStage output_stage)
    : impl_(std::make_unique<Impl>(output_rate, output_stage)) {}

RealtimeCore::~RealtimeCore() = default;
RealtimeCore::RealtimeCore(RealtimeCore&&) noexcept = default;
RealtimeCore& RealtimeCore::operator=(RealtimeCore&&) noexcept = default;

void RealtimeCore::set_output_rate(double output_rate) {
    impl_->configure_rate(output_rate);
}

double RealtimeCore::output_rate() const noexcept {
    return impl_->output_rate;
}

void RealtimeCore::set_output_stage(OutputStage output_stage) {
    if (impl_->output_stage == output_stage) return;
    impl_->output_stage = output_stage;
    impl_->reset();
}

OutputStage RealtimeCore::output_stage() const noexcept {
    return impl_->output_stage;
}

void RealtimeCore::reset() {
    impl_->reset();
}

void RealtimeCore::write_register(uint16_t address, uint8_t data) {
    impl_->machine->write(address, data);
    if (impl_->register_write_observer != nullptr) {
        impl_->register_write_observer(impl_->register_write_context,
                                       impl_->next_sample_cycle, address, data);
    }
}

uint8_t RealtimeCore::read_register(uint16_t address) const {
    return impl_->machine->read(address);
}

void RealtimeCore::set_register_write_observer(RegisterWriteObserver observer,
                                                void* context) noexcept {
    impl_->register_write_observer = observer;
    impl_->register_write_context = context;
}

void RealtimeCore::set_sample_memory(std::vector<uint8_t> memory) {
    impl_->machine->write(0x0120, 0x80);
    impl_->machine->write(0x0140, 0x00);
    impl_->sample_rom = std::move(memory);
}

size_t RealtimeCore::sample_memory_size() const noexcept {
    return impl_->sample_rom.size();
}

void RealtimeCore::render(float* left, float* right, size_t frames) {
    if (frames != 0 && (left == nullptr || right == nullptr)) {
        throw Error("buffer temps reel nul");
    }
    constexpr uint64_t fraction_mask = 0xffff'ffffULL;
    const auto advance_scheduler = [&](uint64_t step) {
        const uint64_t whole_step = step >> 32;
        const uint64_t fraction_step = step & fraction_mask;
        const uint64_t fraction_sum = impl_->cycle_fraction_q32 + fraction_step;
        const uint64_t carry = fraction_sum >> 32;
        if (impl_->next_sample_cycle >
            std::numeric_limits<uint64_t>::max() - whole_step - carry) {
            throw Error("debordement du scheduler temps reel");
        }
        impl_->next_sample_cycle += whole_step + carry;
        impl_->cycle_fraction_q32 = fraction_sum & fraction_mask;
    };

    if (impl_->output_stage == OutputStage::RawDigital) {
        for (size_t frame = 0; frame < frames; ++frame) {
            impl_->machine->advance_before(impl_->next_sample_cycle);
            impl_->machine->advance_at(impl_->next_sample_cycle);
            const auto sample = impl_->machine->mix(impl_->clipped);
            left[frame] = static_cast<float>(sample[0]) / 32768.0f;
            right[frame] = static_cast<float>(sample[1]) / 32768.0f;
            advance_scheduler(impl_->cycles_per_sample_q32);
        }
        return;
    }

    for (size_t frame = 0; frame < frames; ++frame) {
        const uint64_t frame_start_cycle = impl_->next_sample_cycle;
        advance_scheduler(impl_->cycles_per_sample_q32);
        const uint64_t frame_end_cycle = impl_->next_sample_cycle;
        for (uint32_t phase = 0; phase < HardwareOutputStage::kOversampleFactor; ++phase) {
            const uint64_t phase_cycle = frame_start_cycle +
                ((frame_end_cycle - frame_start_cycle) * phase) /
                    HardwareOutputStage::kOversampleFactor;
            impl_->machine->advance_before(phase_cycle);
            impl_->machine->advance_at(phase_cycle);
            impl_->hardware.process_subsample(impl_->machine->hardware_output(),
                                              impl_->hardware_stats);
        }
        const auto sample = impl_->hardware.capture_frame(impl_->hardware_stats);
        left[frame] = static_cast<float>(sample[0]) / 32768.0f;
        right[frame] = static_cast<float>(sample[1]) / 32768.0f;
    }
}

uint64_t RealtimeCore::current_cycle() const noexcept {
    return impl_->next_sample_cycle;
}

uint64_t RealtimeCore::clipped_samples() const noexcept {
    return impl_->output_stage == OutputStage::Hardware
               ? impl_->hardware_stats.capture_clipped_samples
               : impl_->clipped;
}

uint64_t RealtimeCore::dac_overload_samples() const noexcept {
    return impl_->hardware_stats.dac_overload_samples;
}

uint64_t RealtimeCore::mixer_overload_samples() const noexcept {
    return impl_->hardware_stats.mixer_overload_samples;
}

class DseqDriver {
public:
    DseqDriver(const RomImage& rom, bool trace)
        : rom_(rom), code_(rom.code()), pc_(rom.entrypoint()), trace_(trace) {}

    bool halted() const { return halted_; }
    uint64_t halt_cycle() const { return halt_cycle_; }
    uint64_t next_cycle() const { return next_cycle_; }

    void execute_at(uint64_t cycle, Machine& machine) {
        if (halted_ || cycle != next_cycle_) {
            throw Error("appel du driver DSEQ à un timestamp incorrect");
        }
        size_t zero_time_budget = 1'000'000;
        while (!halted_) {
            if (zero_time_budget-- == 0) {
                throw Error("boucle DSEQ sans progression temporelle");
            }
            const uint32_t instruction_pc = pc_;
            const uint8_t opcode = read_u8();
            switch (opcode) {
            case 0x00:
                halted_ = true;
                halt_cycle_ = cycle;
                if (trace_) {
                    std::cout << "HALT cycle=" << cycle << " ("
                              << std::fixed << std::setprecision(6)
                              << double(cycle) / double(kSystemClock) << " s)\n";
                }
                return;
            case 0x01: {
                const uint64_t duration = read_uleb128();
                if (duration == 0) {
                    break;
                }
                if (duration > std::numeric_limits<uint64_t>::max() - cycle) {
                    throw Error("débordement WAIT DSEQ");
                }
                next_cycle_ = cycle + duration;
                return;
            }
            case 0x10: {
                const uint16_t address = read_u16();
                const uint8_t data = read_u8();
                machine.write(address, data);
                break;
            }
            case 0x11: {
                const uint16_t address = read_u16();
                const uint8_t length = read_u8();
                if (uint32_t(address) + length > 0x10000) {
                    throw Error("WRN dépasse le bus 16 bits");
                }
                for (uint16_t index = 0; index < length; ++index) {
                    machine.write(static_cast<uint16_t>(address + index), read_u8());
                }
                break;
            }
            case 0x20: {
                const uint16_t sample_id = read_u16();
                const uint8_t level = read_u8();
                const uint8_t pan = read_u8();
                machine.play_a(rom_.sample(sample_id), level, pan);
                break;
            }
            case 0x21:
                machine.stop_a();
                break;
            case 0x22: {
                const uint16_t sample_id = read_u16();
                const uint16_t delta_n = read_u16();
                const uint8_t level = read_u8();
                const uint8_t pan = read_u8();
                const uint8_t flags = read_u8();
                if (delta_n == 0) {
                    throw Error("PLAY_B avec Delta-N nul");
                }
                machine.play_b(rom_.sample(sample_id), delta_n, level, pan, flags);
                break;
            }
            case 0x23:
                machine.stop_b();
                break;
            case 0x30:
                jump(read_u32());
                break;
            case 0x31: {
                const uint8_t slot = read_u8();
                const uint16_t count = read_u16();
                const uint32_t target = read_u32();
                if (slot >= loops_.size() || count == 0) {
                    throw Error("paramètres LOOP DSEQ invalides");
                }
                auto& state = loops_[slot];
                if (!state.active || state.instruction_pc != instruction_pc) {
                    state = LoopState{true, instruction_pc, count};
                }
                if (state.remaining > 1) {
                    --state.remaining;
                    jump(target);
                } else {
                    state = {};
                }
                break;
            }
            default: {
                std::ostringstream message;
                message << "opcode DSEQ inconnu $" << std::hex << std::setw(2)
                        << std::setfill('0') << unsigned(opcode) << " à $" << std::setw(8)
                        << instruction_pc;
                throw Error(message.str());
            }
            }
        }
    }

private:
    struct LoopState {
        bool active = false;
        uint32_t instruction_pc = 0;
        uint16_t remaining = 0;
    };

    void require_pc(size_t count) const {
        const uint64_t end = uint64_t(code_.offset) + code_.size;
        if (pc_ < code_.offset || pc_ > end || count > end - pc_) {
            throw Error("lecture DSEQ hors du chunk CODE");
        }
    }

    uint8_t read_u8() {
        require_pc(1);
        return rom_.bytes()[pc_++];
    }

    uint16_t read_u16() {
        require_pc(2);
        const uint16_t value = read_be16(rom_.bytes(), pc_);
        pc_ += 2;
        return value;
    }

    uint32_t read_u32() {
        require_pc(4);
        const uint32_t value = read_be32(rom_.bytes(), pc_);
        pc_ += 4;
        return value;
    }

    uint64_t read_uleb128() {
        uint64_t value = 0;
        unsigned shift = 0;
        for (unsigned index = 0; index < 10; ++index) {
            const uint8_t byte = read_u8();
            const uint64_t payload = byte & 0x7f;
            if (shift >= 64 || (payload << shift) >> shift != payload) {
                throw Error("ULEB128 DSEQ trop grand");
            }
            value |= payload << shift;
            if ((byte & 0x80) == 0) {
                return value;
            }
            shift += 7;
        }
        throw Error("ULEB128 DSEQ invalide");
    }

    void jump(uint32_t target) {
        const uint64_t end = uint64_t(code_.offset) + code_.size;
        if (target < code_.offset || target >= end) {
            throw Error("cible JUMP/LOOP hors CODE");
        }
        pc_ = target;
    }

    const RomImage& rom_;
    const Chunk& code_;
    uint32_t pc_ = 0;
    bool trace_ = false;
    bool halted_ = false;
    uint64_t halt_cycle_ = 0;
    uint64_t next_cycle_ = 0;
    std::array<LoopState, 8> loops_{};
};


class NativeTraceDriver {
public:
    explicit NativeTraceDriver(const std::string& path, bool trace) : trace_(trace) {
        const std::vector<uint8_t> data = read_file(path);
        if (data.size() < 24 || std::memcmp(data.data(), "ZTR1", 4) != 0) {
            throw Error("trace native Z80 ZTR1 invalide");
        }
        if (read_be32(data, 4) != 1) {
            throw Error("version ZTR1 non supportee");
        }
        halt_cycle_ = read_be64(data, 8);
        const uint32_t count = read_be32(data, 16);
        const uint64_t expected = 24ULL + uint64_t(count) * 12ULL;
        if (expected != data.size()) {
            throw Error("taille ZTR1 incoherente");
        }
        events_.reserve(count);
        uint64_t previous = 0;
        for (uint32_t index = 0; index < count; ++index) {
            const size_t offset = 24 + size_t(index) * 12;
            Event event;
            event.cycle = read_be64(data, offset);
            event.address = read_be16(data, offset + 8);
            event.data = data[offset + 10];
            if (index != 0 && event.cycle < previous) {
                throw Error("ZTR1 non chronologique");
            }
            if (event.address > 0x01ff) {
                throw Error("ZTR1 contient une ecriture MMIO hors DMS-1");
            }
            previous = event.cycle;
            events_.push_back(event);
        }
        if (!events_.empty() && halt_cycle_ < events_.back().cycle) {
            throw Error("HALT ZTR1 precede la derniere ecriture");
        }
        if (trace_) {
            std::cout << "Z80 native trace: " << events_.size()
                      << " writes, halt cycle=" << halt_cycle_ << "\n";
        }
    }

    bool halted() const { return halted_; }
    uint64_t halt_cycle() const { return halt_cycle_; }
    uint64_t next_cycle() const {
        if (halted_) return halt_cycle_;
        if (index_ < events_.size()) return events_[index_].cycle;
        return halt_cycle_;
    }

    void execute_at(uint64_t cycle, Machine& machine) {
        if (halted_ || cycle != next_cycle()) {
            throw Error("appel du driver Z80 natif a un timestamp incorrect");
        }
        while (index_ < events_.size() && events_[index_].cycle == cycle) {
            machine.write(events_[index_].address, events_[index_].data);
            ++index_;
        }
        if (index_ == events_.size() && cycle == halt_cycle_) {
            halted_ = true;
            if (trace_) {
                std::cout << "Z80 HALT cycle=" << cycle << " ("
                          << std::fixed << std::setprecision(6)
                          << double(cycle) / double(kSystemClock) << " s)\n";
            }
        }
    }

private:
    struct Event {
        uint64_t cycle = 0;
        uint16_t address = 0;
        uint8_t data = 0;
    };
    std::vector<Event> events_;
    size_t index_ = 0;
    uint64_t halt_cycle_ = 0;
    bool halted_ = false;
    bool trace_ = false;
};

class DriverMux {
public:
    DriverMux(const RomImage& rom, const RenderConfig& options)
        : driver_(options.native_trace_path.empty()
                      ? Variant(DseqDriver(rom, options.trace))
                      : Variant(NativeTraceDriver(options.native_trace_path, options.trace))) {}

    bool halted() const {
        return std::visit([](const auto& driver) { return driver.halted(); }, driver_);
    }
    uint64_t halt_cycle() const {
        return std::visit([](const auto& driver) { return driver.halt_cycle(); }, driver_);
    }
    uint64_t next_cycle() const {
        return std::visit([](const auto& driver) { return driver.next_cycle(); }, driver_);
    }
    void execute_at(uint64_t cycle, Machine& machine) {
        std::visit([&](auto& driver) { driver.execute_at(cycle, machine); }, driver_);
    }

private:
    using Variant = std::variant<DseqDriver, NativeTraceDriver>;
    Variant driver_;
};

void write_le16(std::ofstream& stream, uint16_t value) {
    const std::array<uint8_t, 2> bytes{static_cast<uint8_t>(value), static_cast<uint8_t>(value >> 8)};
    stream.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
}

void write_le32(std::ofstream& stream, uint32_t value) {
    const std::array<uint8_t, 4> bytes{
        static_cast<uint8_t>(value), static_cast<uint8_t>(value >> 8),
        static_cast<uint8_t>(value >> 16), static_cast<uint8_t>(value >> 24)};
    stream.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
}

void write_wav(const std::string& path, const std::vector<int16_t>& samples) {
    if ((samples.size() & 1) != 0) {
        throw Error("buffer WAV stéréo de taille impaire");
    }
    const uint64_t data_size_64 = samples.size() * sizeof(int16_t);
    if (data_size_64 > std::numeric_limits<uint32_t>::max() - 36) {
        throw Error("rendu WAV trop grand");
    }
    const uint32_t data_size = static_cast<uint32_t>(data_size_64);
    std::ofstream stream(path.c_str(), std::ios::binary);
    if (!stream) {
        throw Error("impossible de créer " + path);
    }
    stream.write("RIFF", 4);
    write_le32(stream, 36 + data_size);
    stream.write("WAVEfmt ", 8);
    write_le32(stream, 16);
    write_le16(stream, 1);
    write_le16(stream, 2);
    write_le32(stream, kOfflineOutputRate);
    write_le32(stream, kOfflineOutputRate * 2 * sizeof(int16_t));
    write_le16(stream, 2 * sizeof(int16_t));
    write_le16(stream, 16);
    stream.write("data", 4);
    write_le32(stream, data_size);
    for (int16_t sample : samples) {
        write_le16(stream, static_cast<uint16_t>(sample));
    }
    if (!stream) {
        throw Error("échec pendant l'écriture WAV");
    }
}

struct RenderedPcm {
    std::vector<int16_t> samples;
    uint32_t peak = 0;
    uint64_t clipped_samples = 0;
    HardwareOutputStats hardware_stats;
};

RenderedPcm render_raw(const RomImage& rom, const RenderConfig& options) {
    Machine machine(rom, options.trace);
    DriverMux driver(rom, options);
    const uint64_t tail_cycles = uint64_t(options.tail_ms) * kSystemClock / 1000;
    const uint64_t max_cycles = uint64_t(options.max_seconds) * kSystemClock;
    RenderedPcm output;
    output.samples.reserve(size_t(options.max_seconds) * kOfflineOutputRate * 2);

    constexpr uint64_t fraction_mask = 0xffff'ffffULL;
    constexpr uint64_t q32 = uint64_t{1} << 32;
    constexpr uint64_t cycles_per_sample_q32 =
        (kSystemClock * q32 + kOfflineOutputRate / 2) / kOfflineOutputRate;
    uint64_t next_sample_cycle = 0;
    uint64_t cycle_fraction_q32 = 0;

    for (uint64_t frame = 0;; ++frame) {
        const uint64_t target_cycle = next_sample_cycle;
        if (target_cycle > max_cycles) {
            throw Error("driver audio n'a pas atteint HALT avant la limite de rendu");
        }

        while (!driver.halted() && driver.next_cycle() <= target_cycle) {
            const uint64_t event_cycle = driver.next_cycle();
            machine.advance_before(event_cycle);
            driver.execute_at(event_cycle, machine);
            machine.advance_at(event_cycle);
        }
        machine.advance_before(target_cycle);
        machine.advance_at(target_cycle);
        const auto sample = machine.mix(output.clipped_samples);
        output.samples.push_back(sample[0]);
        output.samples.push_back(sample[1]);
        const uint32_t left_peak = sample[0] == std::numeric_limits<int16_t>::min()
                                       ? 32768U
                                       : static_cast<uint32_t>(std::abs(sample[0]));
        const uint32_t right_peak = sample[1] == std::numeric_limits<int16_t>::min()
                                        ? 32768U
                                        : static_cast<uint32_t>(std::abs(sample[1]));
        output.peak = std::max(output.peak, std::max(left_peak, right_peak));

        if (driver.halted() && target_cycle >= driver.halt_cycle() + tail_cycles) {
            break;
        }

        const uint64_t whole_step = cycles_per_sample_q32 >> 32;
        const uint64_t fraction_step = cycles_per_sample_q32 & fraction_mask;
        const uint64_t fraction_sum = cycle_fraction_q32 + fraction_step;
        const uint64_t carry = fraction_sum >> 32;
        if (next_sample_cycle >
            std::numeric_limits<uint64_t>::max() - whole_step - carry) {
            throw Error("débordement du scheduler de rendu 44,1 kHz");
        }
        next_sample_cycle += whole_step + carry;
        cycle_fraction_q32 = fraction_sum & fraction_mask;
    }
    return output;
}

RenderedPcm render_hardware(const RomImage& rom, const RenderConfig& options) {
    Machine machine(rom, options.trace);
    DriverMux driver(rom, options);
    HardwareOutputStage hardware(kOfflineOutputRate);
    const uint64_t tail_cycles = uint64_t(options.tail_ms) * kSystemClock / 1000;
    const uint64_t max_cycles = uint64_t(options.max_seconds) * kSystemClock;
    RenderedPcm output;
    output.samples.reserve(size_t(options.max_seconds) * kOfflineOutputRate * 2);

    constexpr uint64_t fraction_mask = 0xffff'ffffULL;
    constexpr uint64_t q32 = uint64_t{1} << 32;
    constexpr uint64_t cycles_per_sample_q32 =
        (kSystemClock * q32 + kOfflineOutputRate / 2) / kOfflineOutputRate;
    uint64_t next_sample_cycle = 0;
    uint64_t cycle_fraction_q32 = 0;

    for (uint64_t frame = 0;; ++frame) {
        const uint64_t frame_start_cycle = next_sample_cycle;
        if (frame_start_cycle > max_cycles) {
            throw Error("driver audio n'a pas atteint HALT avant la limite de rendu");
        }
        const uint64_t whole_step = cycles_per_sample_q32 >> 32;
        const uint64_t fraction_step = cycles_per_sample_q32 & fraction_mask;
        const uint64_t fraction_sum = cycle_fraction_q32 + fraction_step;
        const uint64_t carry = fraction_sum >> 32;
        if (next_sample_cycle >
            std::numeric_limits<uint64_t>::max() - whole_step - carry) {
            throw Error("débordement du scheduler matériel 44,1 kHz");
        }
        const uint64_t frame_end_cycle = next_sample_cycle + whole_step + carry;

        for (uint32_t phase = 0; phase < HardwareOutputStage::kOversampleFactor; ++phase) {
            const uint64_t target_cycle = frame_start_cycle +
                ((frame_end_cycle - frame_start_cycle) * phase) /
                    HardwareOutputStage::kOversampleFactor;

            while (!driver.halted() && driver.next_cycle() <= target_cycle) {
                const uint64_t event_cycle = driver.next_cycle();
                machine.advance_before(event_cycle);
                driver.execute_at(event_cycle, machine);
                machine.advance_at(event_cycle);
            }
            machine.advance_before(target_cycle);
            machine.advance_at(target_cycle);
            hardware.process_subsample(machine.hardware_output(), output.hardware_stats);
        }

        const auto sample = hardware.capture_frame(output.hardware_stats);
        output.samples.push_back(sample[0]);
        output.samples.push_back(sample[1]);
        const uint32_t left_peak = sample[0] == std::numeric_limits<int16_t>::min()
                                       ? 32768U
                                       : static_cast<uint32_t>(std::abs(sample[0]));
        const uint32_t right_peak = sample[1] == std::numeric_limits<int16_t>::min()
                                        ? 32768U
                                        : static_cast<uint32_t>(std::abs(sample[1]));
        output.peak = std::max(output.peak, std::max(left_peak, right_peak));

        if (driver.halted() && frame_start_cycle >= driver.halt_cycle() + tail_cycles) {
            break;
        }
        next_sample_cycle = frame_end_cycle;
        cycle_fraction_q32 = fraction_sum & fraction_mask;
    }
    output.clipped_samples = output.hardware_stats.capture_clipped_samples;
    return output;
}

RenderedPcm render(const RomImage& rom, const RenderConfig& options) {
    return options.output_stage == OutputStage::Hardware
               ? render_hardware(rom, options)
               : render_raw(rom, options);
}

RenderReport render_rom_to_wav(const std::string& rom_path,
                               const std::string& wav_path,
                               const RenderConfig& config) {
    const PcmRender rendered = render_rom_to_pcm(rom_path, config);
    write_pcm_to_wav(wav_path, rendered);
    return rendered.report;
}

PcmRender render_rom_to_pcm(const std::string& rom_path,
                            const RenderConfig& config) {
    if (config.max_seconds == 0 || config.max_seconds > 600) {
        throw Error("max_seconds doit être compris entre 1 et 600");
    }
    const RomImage rom = RomImage::load(rom_path);
    auto rendered = render(rom, config);
    PcmRender result;
    result.report.rom_bytes = rom.bytes().size();
    result.report.frames = rendered.samples.size() / 2;
    result.report.output_rate = kOfflineOutputRate;
    result.report.peak = rendered.peak;
    result.report.clipped_samples = rendered.clipped_samples;
    result.report.dac_overload_samples = rendered.hardware_stats.dac_overload_samples;
    result.report.mixer_overload_samples = rendered.hardware_stats.mixer_overload_samples;
    result.report.output_stage = config.output_stage;
    result.interleaved_stereo = std::move(rendered.samples);
    return result;
}

void write_pcm_to_wav(const std::string& wav_path,
                      const PcmRender& render) {
    if (render.report.output_rate != kOfflineOutputRate ||
        render.report.frames * 2 != render.interleaved_stereo.size()) {
        throw Error("buffer PCM incompatible avec le profil de sortie DMS-1");
    }
    write_wav(wav_path, render.interleaved_stereo);
}

} // namespace dms1
