CXX ?= c++
PYTHON ?= python3

CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra -Wpedantic
CPPFLAGS += -Ithird_party/ymfm/src

YMFM_SOURCES = \
	third_party/ymfm/src/ymfm_adpcm.cpp \
	third_party/ymfm/src/ymfm_misc.cpp \
	third_party/ymfm/src/ymfm_opz.cpp \
	third_party/ymfm/src/ymfm_ssg.cpp

EMU = build/dms1emu
REALTIME_TEST = build/dms1_realtime_core_test
CODEC_TEST = build/dms1_sample_codec_test
FM_SLOT_TEST = build/dms1_fm_slot_protocol_test
RECORDING_TEST = build/dms1_recording_rom_test
TX81Z_IMPORT_TEST = build/dms1_tx81z_import_test
ROM = roms/dms1_nine_voices.dmr
WAV = renders/dms1_nine_voices_p03.wav
P04_PROJECT = examples/p04/p04_midi_bridge.dmsproj
P04_MIDI = examples/p04/p04_midi_bridge.mid
P04_TEXTURE = examples/p04/texture_26k.wav
P04_ROM = roms/dms1_p04_midi_bridge.dmr
P04_WAV = renders/dms1_p04_midi_bridge.wav
P041_SYX = assets/user/TX81Z_G-StormPatches_01.syx
P041_BANK = instruments/tx81z_gstorm_01.json
P041_IMPORT_REPORT = reports/tx81z_gstorm_01_import.txt
P041_TIMELINE = reports/dms1_p041_tx81z_audition_timeline.txt
P041_ROM = roms/dms1_p041_tx81z_audition.dmr
P041_WAV = renders/dms1_p041_tx81z_audition.wav
MUSIC_DMC = roms/dac_master_music_rom.dmc

.PHONY: all rom demo p04 p041 music_rom test clean

all: $(EMU)

$(EMU): src/dms1emu.cpp src/dms1_core.cpp src/dms1_core.hpp \
		src/dms1_output_stage.cpp src/dms1_output_stage.hpp $(YMFM_SOURCES) third_party/ymfm/LICENSE
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) src/dms1emu.cpp src/dms1_core.cpp \
		src/dms1_output_stage.cpp $(YMFM_SOURCES) -o $@

$(REALTIME_TEST): tests/realtime_core_test.cpp src/dms1_core.cpp src/dms1_core.hpp \
		src/dms1_output_stage.cpp src/dms1_output_stage.hpp $(YMFM_SOURCES)
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -Isrc tests/realtime_core_test.cpp \
		src/dms1_core.cpp src/dms1_output_stage.cpp $(YMFM_SOURCES) -o $@

$(CODEC_TEST): tests/sample_codec_test.cpp src/dms1_sample_codec.cpp src/dms1_sample_codec.hpp
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -Isrc tests/sample_codec_test.cpp src/dms1_sample_codec.cpp -o $@

$(FM_SLOT_TEST): tests/fm_slot_protocol_test.cpp src/dms1_midi_protocol.hpp
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -Isrc tests/fm_slot_protocol_test.cpp -o $@

$(RECORDING_TEST): tests/recording_rom_test.cpp src/dms1_core.cpp src/dms1_core.hpp \
		src/dms1_output_stage.cpp src/dms1_output_stage.hpp \
		src/dms1_recording.cpp src/dms1_recording.hpp $(YMFM_SOURCES)
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -Isrc tests/recording_rom_test.cpp \
		src/dms1_core.cpp src/dms1_output_stage.cpp src/dms1_recording.cpp \
		$(YMFM_SOURCES) -o $@

$(TX81Z_IMPORT_TEST): tests/tx81z_import_test.cpp src/dms1_tx81z_import.cpp \
		src/dms1_tx81z_import.hpp
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -Isrc tests/tx81z_import_test.cpp \
		src/dms1_tx81z_import.cpp -o $@

$(ROM): tools/make_demo.py tools/dms_audio.py \
	assets/source/Streets\ of\ Rage\ 1\ Kick.wav \
	assets/source/Streets\ of\ Rage\ 1\ Snare.wav \
	assets/source/Streets\ of\ Rage\ 1\ Hi\ Hat\ 1.wav
	$(PYTHON) tools/make_demo.py --out $@

rom: $(ROM)

$(WAV): $(EMU) $(ROM)
	mkdir -p renders
	$(EMU) $(ROM) $(WAV) --trace

demo: $(WAV)

$(P04_MIDI) $(P04_TEXTURE): tools/make_p04_example.py tools/make_demo.py tools/dms_audio.py
	$(PYTHON) tools/make_p04_example.py --directory examples/p04

$(P04_ROM): tools/dms_compile.py tools/make_demo.py tools/dms_audio.py \
	$(P04_PROJECT) $(P04_MIDI) $(P04_TEXTURE) instruments/factory_opz.json \
	assets/source/Streets\ of\ Rage\ 1\ Kick.wav \
	assets/source/Streets\ of\ Rage\ 1\ Snare.wav \
	assets/source/Streets\ of\ Rage\ 1\ Hi\ Hat\ 1.wav
	$(PYTHON) tools/dms_compile.py $(P04_PROJECT) --out $@

$(P04_WAV): $(EMU) $(P04_ROM)
	mkdir -p renders
	$(EMU) $(P04_ROM) $(P04_WAV) --trace

p04: $(P04_WAV)

$(P041_BANK): tools/dms_syx_import.py $(P041_SYX)
	mkdir -p instruments reports
	$(PYTHON) tools/dms_syx_import.py $(P041_SYX) --out $(P041_BANK) \
		--report $(P041_IMPORT_REPORT) --bank-name "TX81Z G-Storm Patches 01 - DMS import"

$(P041_ROM): tools/make_tx81z_audition.py tools/dms_compile.py tools/make_demo.py $(P041_BANK)
	mkdir -p roms reports
	$(PYTHON) tools/make_tx81z_audition.py $(P041_BANK) --out $(P041_ROM) \
		--report $(P041_TIMELINE)

$(P041_WAV): $(EMU) $(P041_ROM)
	mkdir -p renders
	$(EMU) $(P041_ROM) $(P041_WAV) --trace

p041: $(P041_WAV)

$(MUSIC_DMC): tools/dms_cartridge.py MUSIQUES_DMR/CARTOUCHE.json \
	MUSIQUES_DMR/01\ -\ DMS-1\ Nine\ Voices.dmr \
	MUSIQUES_DMR/02\ -\ DMS-1\ MIDI\ Bridge.dmr \
	MUSIQUES_DMR/03\ -\ TX81Z\ OPZ\ Audition.dmr
	$(PYTHON) tools/dms_cartridge.py build MUSIQUES_DMR \
		--config MUSIQUES_DMR/CARTOUCHE.json --out $@

music_rom: $(MUSIC_DMC)

test: $(EMU) $(REALTIME_TEST) $(CODEC_TEST) $(FM_SLOT_TEST) $(RECORDING_TEST) \
		$(TX81Z_IMPORT_TEST)
	$(REALTIME_TEST)
	$(CODEC_TEST)
	$(FM_SLOT_TEST)
	$(RECORDING_TEST)
	$(TX81Z_IMPORT_TEST)
	$(PYTHON) tests/run_tests.py

clean:
	rm -rf build roms/*.dmr roms/*.dmc renders/*.wav

.PHONY: console90 console90_test
console90:
	$(PYTHON) tools/build_console90_demo.py --out roms/dms1_console90_cpu_demo.dmc

console90_test:
	$(PYTHON) tests/console90_test.py
