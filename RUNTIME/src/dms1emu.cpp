#include "dms1_core.hpp"

#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

namespace {

constexpr const char* kVersionBanner =
    "DMS-1 P0.6.1 | engine P0.2.4 | DMR 0.1 | Z80 NATIVE DRIVER | RAW/HARDWARE | OPZ FM1-4 FIX | 44.1 kHz/16-bit";

struct CliOptions {
    std::string rom;
    std::string wav;
    dms1::RenderConfig render;
};

CliOptions parse_options(int argc, char** argv) {
    if (argc < 3) {
        throw std::runtime_error(
            "usage: dms1emu ROM.dmr SORTIE.wav [--output-stage raw|hardware] "
            "[--tail-ms N] [--max-seconds N] [--native-trace trace.ztr] [--trace]");
    }
    CliOptions options;
    options.render.output_stage = dms1::OutputStage::Hardware;
    options.rom = argv[1];
    options.wav = argv[2];
    for (int index = 3; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--trace") {
            options.render.trace = true;
            continue;
        }
        if (argument == "--native-trace") {
            if (++index >= argc) {
                throw std::runtime_error("valeur manquante après " + argument);
            }
            options.render.native_trace_path = argv[index];
            continue;
        }
        if (argument == "--output-stage") {
            if (++index >= argc) {
                throw std::runtime_error("valeur manquante après " + argument);
            }
            const std::string value = argv[index];
            if (value == "raw") {
                options.render.output_stage = dms1::OutputStage::RawDigital;
            } else if (value == "hardware") {
                options.render.output_stage = dms1::OutputStage::Hardware;
            } else {
                throw std::runtime_error(
                    "étage de sortie invalide : " + value + " (attendu raw ou hardware)");
            }
            continue;
        }
        if (argument != "--tail-ms" && argument != "--max-seconds") {
            throw std::runtime_error("option inconnue : " + argument);
        }
        if (++index >= argc) {
            throw std::runtime_error("valeur manquante après " + argument);
        }
        const std::string value = argv[index];
        size_t consumed = 0;
        const unsigned long parsed = std::stoul(value, &consumed, 10);
        if (consumed != value.size() || parsed > std::numeric_limits<uint32_t>::max()) {
            throw std::runtime_error("valeur numérique invalide : " + value);
        }
        if (argument == "--tail-ms") {
            options.render.tail_ms = static_cast<uint32_t>(parsed);
        } else {
            options.render.max_seconds = static_cast<uint32_t>(parsed);
        }
    }
    return options;
}

} // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--version") {
            std::cout << kVersionBanner << '\n';
            return 0;
        }
        const CliOptions options = parse_options(argc, argv);
        std::cout << kVersionBanner << '\n'
                  << "ROM: \"" << options.rom << "\"\n"
                  << "Output stage: " << dms1::output_stage_name(options.render.output_stage)
                  << "\n"
                  << "Driver: " << (options.render.native_trace_path.empty() ? "DSEQ legacy" : "Z80 native / ZTR1") << "\n"
                  << "ymfm pin: 81aec25ccbb98f4873a255f7551ac4dadac59b4a\n";
        const dms1::RenderReport report =
            dms1::render_rom_to_wav(options.rom, options.wav, options.render);
        std::cout << "ROM validée: " << report.rom_bytes << " octets\n"
                  << "WAV: \"" << options.wav << "\" | " << report.frames << " frames | "
                  << std::fixed << std::setprecision(3)
                  << double(report.frames) / report.output_rate << " s | "
                  << std::setprecision(1) << double(report.output_rate) / 1000.0
                  << " kHz / 16-bit stéréo\n"
                  << "Peak: " << report.peak << " | clipped samples: "
                  << report.clipped_samples << "\n";
        if (report.output_stage == dms1::OutputStage::Hardware) {
            std::cout << "DAC overload samples: " << report.dac_overload_samples
                      << " | mixer overload samples: " << report.mixer_overload_samples
                      << "\n";
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "dms1emu: " << error.what() << '\n';
        return 1;
    }
}
