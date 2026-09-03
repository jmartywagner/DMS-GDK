#include "dms1_recording.hpp"

#include <algorithm>
#include <array>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <unordered_set>

namespace dms1 {
namespace {

constexpr uint32_t kMaximumRomBytes = 0x01000000;
constexpr uint32_t kHeaderBytes = 64;
constexpr uint32_t kDirectoryEntryBytes = 16;
constexpr uint32_t kSamplePageBytes = 256;

uint32_t checked_u32(uint64_t value, const char* message) {
    if (value > std::numeric_limits<uint32_t>::max()) throw std::runtime_error(message);
    return static_cast<uint32_t>(value);
}

uint32_t align_up(uint32_t value, uint32_t alignment) {
    const uint64_t aligned = (uint64_t(value) + alignment - 1) & ~(uint64_t(alignment) - 1);
    return checked_u32(aligned, "debordement d'alignement DMR");
}

void append_be16(std::vector<uint8_t>& output, uint16_t value) {
    output.push_back(static_cast<uint8_t>(value >> 8));
    output.push_back(static_cast<uint8_t>(value));
}

void append_be32(std::vector<uint8_t>& output, uint32_t value) {
    output.push_back(static_cast<uint8_t>(value >> 24));
    output.push_back(static_cast<uint8_t>(value >> 16));
    output.push_back(static_cast<uint8_t>(value >> 8));
    output.push_back(static_cast<uint8_t>(value));
}

void write_be16(std::vector<uint8_t>& output, size_t offset, uint16_t value) {
    output.at(offset) = static_cast<uint8_t>(value >> 8);
    output.at(offset + 1) = static_cast<uint8_t>(value);
}

void write_be32(std::vector<uint8_t>& output, size_t offset, uint32_t value) {
    output.at(offset) = static_cast<uint8_t>(value >> 24);
    output.at(offset + 1) = static_cast<uint8_t>(value >> 16);
    output.at(offset + 2) = static_cast<uint8_t>(value >> 8);
    output.at(offset + 3) = static_cast<uint8_t>(value);
}

void append_uleb128(std::vector<uint8_t>& output, uint64_t value) {
    do {
        uint8_t byte = static_cast<uint8_t>(value & 0x7f);
        value >>= 7;
        if (value != 0) byte |= 0x80;
        output.push_back(byte);
    } while (value != 0);
}

void append_wait(std::vector<uint8_t>& code, uint64_t cycles) {
    if (cycles == 0) return;
    code.push_back(0x01);
    append_uleb128(code, cycles);
}

std::vector<uint8_t> compile_dseq(std::vector<RecordedEvent>& events,
                                  const RecordedRomOptions& options) {
    std::stable_sort(events.begin(), events.end(), [](const auto& left, const auto& right) {
        return left.cycle < right.cycle;
    });

    std::vector<uint8_t> code;
    const uint64_t estimated = std::min<uint64_t>(
        uint64_t(events.size()) * 5 + 32, std::numeric_limits<size_t>::max());
    code.reserve(static_cast<size_t>(estimated));
    uint64_t cursor_cycle = 0;
    for (const auto& event : events) {
        if (event.cycle < cursor_cycle) throw std::runtime_error("ordre temporel DSEQ invalide");
        append_wait(code, event.cycle - cursor_cycle);
        cursor_cycle = event.cycle;
        switch (event.kind) {
        case RecordedEventKind::RegisterWrite:
            code.push_back(0x10);
            append_be16(code, event.address_or_sample);
            code.push_back(event.value);
            break;
        case RecordedEventKind::PlayAdpcmA:
            code.push_back(0x20);
            append_be16(code, event.address_or_sample);
            code.push_back(event.level);
            code.push_back(event.pan);
            break;
        case RecordedEventKind::StopAdpcmA:
            code.push_back(0x21);
            break;
        case RecordedEventKind::PlayAdpcmB:
            if (event.argument16 == 0) throw std::invalid_argument("Delta-N ADPCM-B nul");
            code.push_back(0x22);
            append_be16(code, event.address_or_sample);
            append_be16(code, event.argument16);
            code.push_back(event.level);
            code.push_back(event.pan);
            code.push_back(event.flags);
            break;
        case RecordedEventKind::StopAdpcmB:
            code.push_back(0x23);
            break;
        }
    }

    const uint64_t capture_end = std::max(cursor_cycle, options.capture_cycles);
    append_wait(code, capture_end - cursor_cycle);
    if (options.tail_cycles > std::numeric_limits<uint64_t>::max() - capture_end)
        throw std::runtime_error("duree DMR trop grande");
    append_wait(code, options.tail_cycles);
    code.push_back(0x00);
    return code;
}

struct ChunkDescription {
    std::array<char, 4> type {};
    uint32_t offset = 0;
    uint32_t size = 0;
};

} // namespace

RecordedEvent RecordedEvent::register_write(uint64_t cycle, uint16_t address,
                                             uint8_t data) noexcept {
    RecordedEvent event;
    event.cycle = cycle;
    event.kind = RecordedEventKind::RegisterWrite;
    event.address_or_sample = address;
    event.value = data;
    return event;
}

RecordedEvent RecordedEvent::play_a(uint64_t cycle, uint16_t sample_id,
                                     uint8_t level, uint8_t pan) noexcept {
    RecordedEvent event;
    event.cycle = cycle;
    event.kind = RecordedEventKind::PlayAdpcmA;
    event.address_or_sample = sample_id;
    event.level = level;
    event.pan = pan;
    return event;
}

RecordedEvent RecordedEvent::stop_a(uint64_t cycle) noexcept {
    RecordedEvent event;
    event.cycle = cycle;
    event.kind = RecordedEventKind::StopAdpcmA;
    return event;
}

RecordedEvent RecordedEvent::play_b(uint64_t cycle, uint16_t sample_id,
                                     uint16_t delta_n, uint8_t level,
                                     uint8_t pan, uint8_t flags) noexcept {
    RecordedEvent event;
    event.cycle = cycle;
    event.kind = RecordedEventKind::PlayAdpcmB;
    event.address_or_sample = sample_id;
    event.argument16 = delta_n;
    event.level = level;
    event.pan = pan;
    event.flags = flags;
    return event;
}

RecordedEvent RecordedEvent::stop_b(uint64_t cycle) noexcept {
    RecordedEvent event;
    event.cycle = cycle;
    event.kind = RecordedEventKind::StopAdpcmB;
    return event;
}

std::vector<uint8_t> build_recorded_dmr(
    std::vector<RecordedEvent> events,
    const std::vector<RecordedSampleResource>& resources,
    const RecordedRomOptions& options) {
    std::unordered_set<uint16_t> sample_ids;
    for (const auto& resource : resources) {
        if (resource.sample_id == 0 || !sample_ids.insert(resource.sample_id).second)
            throw std::invalid_argument("sample ID DMR nul ou duplique");
        if (resource.codec != 1 && resource.codec != 2)
            throw std::invalid_argument("codec ADPCM DMR inconnu");
        if (resource.data.empty() || resource.data.size() % kSamplePageBytes != 0)
            throw std::invalid_argument("sample DMR non aligne sur 256 octets");
        if (resource.source_rate == 0)
            throw std::invalid_argument("frequence source DMR nulle");
    }
    for (const auto& event : events) {
        if ((event.kind == RecordedEventKind::PlayAdpcmA
             || event.kind == RecordedEventKind::PlayAdpcmB)
            && sample_ids.count(event.address_or_sample) == 0)
            throw std::invalid_argument("PLAY ADPCM cible un sample absent de la ROM");
    }

    auto code = compile_dseq(events, options);
    std::ostringstream metadata;
    metadata << "title=" << options.title << "\n"
             << "author=" << options.author << "\n"
             << "compiler=DMS-1 Hardware Write Recorder V0.1\n"
             << "timing=exact NATIVE89 24 MHz register cycles\n"
             << "capture_cycles=" << options.capture_cycles << "\n"
             << "event_count=" << events.size() << "\n"
             << "adpcm_b_nominal_rate=26000\n";
    const std::string meta_string = metadata.str();
    const std::vector<uint8_t> meta(meta_string.begin(), meta_string.end());

    const uint16_t chunk_count = resources.empty() ? 2 : 4;
    const uint32_t directory_offset = kHeaderBytes;
    const uint32_t code_offset = directory_offset + chunk_count * kDirectoryEntryBytes;
    const uint32_t meta_offset = align_up(
        checked_u32(uint64_t(code_offset) + code.size(), "chunk CODE DMR trop grand"), 4);

    uint32_t sdir_offset = 0;
    uint32_t samp_offset = 0;
    uint32_t sample_bytes = 0;
    std::vector<uint8_t> sdir;
    if (!resources.empty()) {
        sdir_offset = align_up(
            checked_u32(uint64_t(meta_offset) + meta.size(), "chunk META DMR trop grand"), 4);
        const uint32_t sdir_size = checked_u32(
            uint64_t(resources.size()) * 16, "repertoire samples DMR trop grand");
        samp_offset = align_up(
            checked_u32(uint64_t(sdir_offset) + sdir_size, "chunk SDIR DMR trop grand"),
            kSamplePageBytes);
        for (const auto& resource : resources) {
            sample_bytes = checked_u32(uint64_t(sample_bytes) + resource.data.size(),
                                       "chunk SAMP DMR trop grand");
        }
        uint32_t sample_cursor = samp_offset;
        sdir.reserve(sdir_size);
        for (const auto& resource : resources) {
            const uint32_t last_byte = checked_u32(
                uint64_t(sample_cursor) + resource.data.size() - 1,
                "adresse sample DMR trop grande");
            const uint32_t start_page = sample_cursor / kSamplePageBytes;
            const uint32_t end_page = last_byte / kSamplePageBytes;
            if (end_page > std::numeric_limits<uint16_t>::max())
                throw std::runtime_error("pages sample hors SDIR V0.1");
            append_be16(sdir, resource.sample_id);
            sdir.push_back(resource.codec);
            sdir.push_back(0);
            append_be16(sdir, static_cast<uint16_t>(start_page));
            append_be16(sdir, static_cast<uint16_t>(end_page));
            append_be32(sdir, resource.source_rate);
            sdir.push_back(resource.level);
            sdir.push_back(resource.pan);
            sdir.push_back(resource.root_note);
            sdir.push_back(static_cast<uint8_t>(resource.fine_cents));
            sample_cursor = last_byte + 1;
        }
    }

    const uint32_t total_size = resources.empty()
        ? checked_u32(uint64_t(meta_offset) + meta.size(), "ROM DMR trop grande")
        : checked_u32(uint64_t(samp_offset) + sample_bytes, "ROM DMR trop grande");
    if (total_size > kMaximumRomBytes) throw std::runtime_error("DMR depasse 16 Mio");

    std::vector<ChunkDescription> chunks;
    chunks.push_back({{'C', 'O', 'D', 'E'}, code_offset,
                      checked_u32(code.size(), "CODE DMR trop grand")});
    chunks.push_back({{'M', 'E', 'T', 'A'}, meta_offset,
                      checked_u32(meta.size(), "META DMR trop grand")});
    if (!resources.empty()) {
        chunks.push_back({{'S', 'D', 'I', 'R'}, sdir_offset,
                          checked_u32(sdir.size(), "SDIR DMR trop grand")});
        chunks.push_back({{'S', 'A', 'M', 'P'}, samp_offset, sample_bytes});
    }

    std::vector<uint8_t> rom(total_size, 0);
    std::copy_n("DMR0", 4, rom.begin());
    write_be16(rom, 0x04, 0);
    write_be16(rom, 0x06, 1);
    write_be16(rom, 0x08, kHeaderBytes);
    write_be16(rom, 0x0a, 0);
    write_be32(rom, 0x0c, total_size);
    std::copy_n("DMS1", 4, rom.begin() + 0x10);
    write_be32(rom, 0x14, 1);
    write_be32(rom, 0x18, directory_offset);
    write_be16(rom, 0x1c, chunk_count);
    write_be16(rom, 0x1e, kDirectoryEntryBytes);
    write_be32(rom, 0x20, code_offset);
    write_be32(rom, 0x24, kDms1SystemClock);

    for (size_t index = 0; index < chunks.size(); ++index) {
        const size_t base = directory_offset + index * kDirectoryEntryBytes;
        std::copy(chunks[index].type.begin(), chunks[index].type.end(), rom.begin() + base);
        write_be32(rom, base + 4, chunks[index].offset);
        write_be32(rom, base + 8, chunks[index].size);
        write_be32(rom, base + 12, 0);
    }
    std::copy(code.begin(), code.end(), rom.begin() + code_offset);
    std::copy(meta.begin(), meta.end(), rom.begin() + meta_offset);
    if (!resources.empty()) {
        std::copy(sdir.begin(), sdir.end(), rom.begin() + sdir_offset);
        uint32_t cursor = samp_offset;
        for (const auto& resource : resources) {
            std::copy(resource.data.begin(), resource.data.end(), rom.begin() + cursor);
            cursor += static_cast<uint32_t>(resource.data.size());
        }
    }
    return rom;
}

} // namespace dms1
