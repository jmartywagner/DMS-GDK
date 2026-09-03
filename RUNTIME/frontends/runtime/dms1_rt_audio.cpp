#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "dms1_core.hpp"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <mmsystem.h>
#endif

namespace {
constexpr uint64_t kMasterHz = 24'000'000;
constexpr uint32_t kRate = 44'100;
constexpr size_t kFramesPerBuffer = 256; // ~5.8 ms
constexpr size_t kBufferCount = 12;
constexpr uint64_t kPrimeCycles = kMasterHz * 90 / 1000; // 90 ms live Z80 lead

struct Event {
    uint64_t cycle{};
    uint16_t address{};
    uint8_t data{};
};

std::vector<uint8_t> read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f) throw std::runtime_error("cannot open sample ROM: " + path);
    const auto n = f.tellg();
    if (n < 0) throw std::runtime_error("invalid file size");
    std::vector<uint8_t> v(static_cast<size_t>(n));
    f.seekg(0);
    if (!v.empty() && !f.read(reinterpret_cast<char*>(v.data()), n))
        throw std::runtime_error("cannot read sample ROM");
    return v;
}

struct Shared {
    std::mutex m;
    std::condition_variable cv;
    std::vector<Event> events;
    uint64_t fed_until = 0;
    uint64_t generation = 1;
    bool play = false;
    bool stop = false;
    bool quit = false;
    bool eof = false;
};

#ifdef _WIN32
class WaveOut {
public:
    WaveOut() {
        WAVEFORMATEX fmt{};
        fmt.wFormatTag = WAVE_FORMAT_PCM;
        fmt.nChannels = 2;
        fmt.nSamplesPerSec = kRate;
        fmt.wBitsPerSample = 16;
        fmt.nBlockAlign = static_cast<WORD>(fmt.nChannels * fmt.wBitsPerSample / 8);
        fmt.nAvgBytesPerSec = fmt.nSamplesPerSec * fmt.nBlockAlign;
        MMRESULT r = waveOutOpen(&out_, WAVE_MAPPER, &fmt, 0, 0, CALLBACK_NULL);
        if (r != MMSYSERR_NOERROR) throw std::runtime_error("waveOutOpen failed");
        buffers_.resize(kBufferCount);
        headers_.resize(kBufferCount);
        submitted_.assign(kBufferCount, false);
        for (size_t i = 0; i < kBufferCount; ++i) {
            buffers_[i].resize(kFramesPerBuffer * 2);
            auto& h = headers_[i];
            std::memset(&h, 0, sizeof(h));
            h.lpData = reinterpret_cast<LPSTR>(buffers_[i].data());
            h.dwBufferLength = static_cast<DWORD>(buffers_[i].size() * sizeof(int16_t));
            r = waveOutPrepareHeader(out_, &h, sizeof(h));
            if (r != MMSYSERR_NOERROR) throw std::runtime_error("waveOutPrepareHeader failed");
        }
    }
    ~WaveOut() {
        if (!out_) return;
        waveOutReset(out_);
        for (auto& h : headers_) waveOutUnprepareHeader(out_, &h, sizeof(h));
        waveOutClose(out_);
    }
    void reset() {
        if (out_) waveOutReset(out_);
        std::fill(submitted_.begin(), submitted_.end(), false);
    }
    bool done(size_t i) const { return !submitted_[i] || (headers_[i].dwFlags & WHDR_DONE) != 0; }
    void wait_done(size_t i) const {
        while (!done(i)) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    int16_t* data(size_t i) { return buffers_[i].data(); }
    void submit(size_t i) {
        auto& h = headers_[i];
        h.dwFlags &= ~WHDR_DONE;
        h.dwBufferLength = static_cast<DWORD>(buffers_[i].size() * sizeof(int16_t));
        MMRESULT r = waveOutWrite(out_, &h, sizeof(h));
        if (r != MMSYSERR_NOERROR) throw std::runtime_error("waveOutWrite failed");
        submitted_[i] = true;
    }
private:
    HWAVEOUT out_{};
    std::vector<std::vector<int16_t>> buffers_;
    std::vector<WAVEHDR> headers_;
    std::vector<bool> submitted_;
};
#else
class WaveOut {
public:
    void reset() {}
    bool done(size_t) const { return true; }
    void wait_done(size_t) const {}
    int16_t* data(size_t) { return buffer_.data(); }
    void submit(size_t) {}
private:
    std::array<int16_t, kFramesPerBuffer * 2> buffer_{};
};
#endif

int16_t pcm16(float x) {
    x = std::max(-1.0f, std::min(0.999969f, x));
    return static_cast<int16_t>(std::lrint(x * 32768.0f));
}

void reader(Shared& s) {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        char op = 0; in >> op;
        std::unique_lock lk(s.m);
        if (op == 'P') {
            s.play = true; s.stop = false; s.events.clear(); s.fed_until = 0; ++s.generation;
        } else if (op == 'S') {
            s.stop = true; s.play = false; s.events.clear(); s.fed_until = 0; ++s.generation;
        } else if (op == 'R') {
            s.events.clear(); s.fed_until = 0; ++s.generation;
        } else if (op == 'E') {
            Event e{}; unsigned addr = 0, data = 0;
            in >> e.cycle >> std::hex >> addr >> data;
            e.address = static_cast<uint16_t>(addr & 0xffff);
            e.data = static_cast<uint8_t>(data & 0xff);
            s.events.push_back(e);
        } else if (op == 'F') {
            uint64_t cycle = 0; in >> cycle; s.fed_until = std::max(s.fed_until, cycle);
        } else if (op == 'Q') {
            s.quit = true;
        }
        lk.unlock();
        s.cv.notify_all();
        if (op == 'Q') return;
    }
    std::lock_guard lk(s.m); s.eof = true; s.quit = true; s.cv.notify_all();
}

void emit_status(const char* status) {
    std::cout << status << '\n' << std::flush;
}

int run_audio(const std::string& sample_path) {
    Shared shared;
    std::thread input(reader, std::ref(shared));
    WaveOut wave;
    dms1::RealtimeCore core(kRate, dms1::OutputStage::Hardware);
    core.set_sample_memory(read_file(sample_path));
    std::vector<float> left(kFramesPerBuffer), right(kFramesPerBuffer);
    emit_status("CAPS ANALOG90 V0.8.0");
    emit_status("READY");

    uint64_t local_generation = 1;
    size_t event_cursor = 0;
    size_t buffer_index = 0;
    bool active = false;
    bool reported_underrun = false;

    while (true) {
        {
            std::unique_lock lk(shared.m);
            shared.cv.wait_for(lk, std::chrono::milliseconds(2), [&] {
                return shared.quit || shared.play || shared.stop || shared.generation != local_generation;
            });
            if (shared.quit) break;
            if (shared.generation != local_generation) {
                local_generation = shared.generation;
                core.reset();
                event_cursor = 0;
                wave.reset();
                active = false;
                reported_underrun = false;
                if (shared.play) emit_status("PRIMING"); else emit_status("STOPPED");
            }
            if (!shared.play) {
                active = false;
                continue;
            }
        }

        // Before waveOut starts, collect a short 90 ms lead generated by the LIVE
        // Z80. This is not pre-rendered audio: it is only a normal device buffer
        // that absorbs Tk/Windows scheduling jitter without advancing gameplay.
        if (!active) {
            std::unique_lock lk(shared.m);
            if (shared.fed_until < kPrimeCycles) {
                shared.cv.wait_for(lk, std::chrono::milliseconds(20), [&] {
                    return shared.quit || !shared.play || shared.fed_until >= kPrimeCycles || shared.generation != local_generation;
                });
                if (shared.quit) break;
                if (!shared.play || shared.generation != local_generation) continue;
                if (shared.fed_until < kPrimeCycles) continue;
            }
            active = true;
            emit_status("PLAYING");
        }

        // Require one full output block of future Z80 data for every render block.
        const uint64_t block_start = core.current_cycle();
        const uint64_t block_span = (kMasterHz * kFramesPerBuffer + kRate - 1) / kRate;
        const uint64_t block_end = block_start + block_span;
        {
            std::unique_lock lk(shared.m);
            if (shared.fed_until < block_end) {
                if (!reported_underrun) { emit_status("BUFFERING"); reported_underrun = true; }
                shared.cv.wait_for(lk, std::chrono::milliseconds(30), [&] {
                    return shared.quit || !shared.play || shared.fed_until >= block_end || shared.generation != local_generation;
                });
                if (shared.quit) break;
                if (!shared.play || shared.generation != local_generation) continue;
                if (shared.fed_until < block_end) continue;
            }
        }

        if (reported_underrun) { reported_underrun = false; emit_status("PLAYING"); }

        // Snapshot all events relevant to this output block under ONE mutex lock.
        // P1.0.2 locked Shared::m once per audio sample (~44,100 locks/sec), which
        // could starve the stdin reader during dense register bursts and create
        // pipe backpressure all the way up to the UI. Timing remains sample-accurate:
        // events are still applied only when RealtimeCore reaches their cycle.
        std::vector<Event> block_events;
        {
            std::lock_guard lk(shared.m);
            size_t cursor = event_cursor;
            while (cursor < shared.events.size() && shared.events[cursor].cycle <= block_end) {
                block_events.push_back(shared.events[cursor]);
                ++cursor;
            }
        }
        size_t block_cursor = 0;
        for (size_t frame = 0; frame < kFramesPerBuffer; ++frame) {
            const uint64_t now = core.current_cycle();
            while (block_cursor < block_events.size() && block_events[block_cursor].cycle <= now) {
                const auto& e = block_events[block_cursor++];
                core.write_register(e.address, e.data);
            }
            core.render(&left[frame], &right[frame], 1);
        }
        // Apply any event that landed exactly at the block tail before the next block.
        while (block_cursor < block_events.size() && block_events[block_cursor].cycle <= core.current_cycle()) {
            const auto& e = block_events[block_cursor++];
            core.write_register(e.address, e.data);
        }
        // Advance the shared-vector cursor only for events actually consumed.
        event_cursor += block_cursor;

        wave.wait_done(buffer_index);
        auto* dst = wave.data(buffer_index);
        for (size_t i = 0; i < kFramesPerBuffer; ++i) {
            dst[i * 2 + 0] = pcm16(left[i]);
            dst[i * 2 + 1] = pcm16(right[i]);
        }
        wave.submit(buffer_index);
        buffer_index = (buffer_index + 1) % kBufferCount;
    }
    wave.reset();
    if (input.joinable()) input.join();
    return 0;
}

int selftest(const std::string& sample_path) {
    dms1::RealtimeCore core(kRate, dms1::OutputStage::Hardware);
    core.set_sample_memory(read_file(sample_path));
    std::vector<float> l(1024), r(1024);
    core.render(l.data(), r.data(), l.size());
    if (core.current_cycle() == 0) return 2;
    std::cout << "SELFTEST OK cycle=" << core.current_cycle() << " sample_rom=" << core.sample_memory_size() << "\n";
    return 0;
}
}

int main(int argc, char** argv) {
    try {
        if (argc < 2) {
            std::cerr << "usage: dms1_rt_audio <DMR sample bus> [--selftest]\n";
            return 64;
        }
        if (argc >= 3 && std::string(argv[2]) == "--selftest") return selftest(argv[1]);
        return run_audio(argv[1]);
    } catch (const std::exception& e) {
        std::cerr << "DMS1_RT_AUDIO_ERROR: " << e.what() << "\n";
        return 1;
    }
}
