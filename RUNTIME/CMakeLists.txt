cmake_minimum_required(VERSION 3.25)
project(DAC_Master_DMS1_VST3 VERSION 0.4.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(DMS1_BUILD_VST3 "Build the DAC MASTER DMS-1 VST3" ON)
option(DMS1_BUILD_TESTS "Build DMS-1 regression tests" ON)

add_library(dms1_core STATIC
    src/dms1_core.cpp
    src/dms1_output_stage.cpp
    src/dms1_recording.cpp
    src/dms1_sample_codec.cpp
    src/dms1_tx81z_import.cpp
    third_party/ymfm/src/ymfm_adpcm.cpp
    third_party/ymfm/src/ymfm_misc.cpp
    third_party/ymfm/src/ymfm_opz.cpp
    third_party/ymfm/src/ymfm_ssg.cpp)
target_include_directories(dms1_core PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
    "${CMAKE_CURRENT_SOURCE_DIR}/third_party/ymfm/src")
target_compile_features(dms1_core PUBLIC cxx_std_20)
set_target_properties(dms1_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(MSVC)
    target_compile_options(dms1_core PRIVATE /W4 /permissive- /utf-8)
else()
    target_compile_options(dms1_core PRIVATE -Wall -Wextra -Wpedantic)
endif()


if(WIN32)
    add_executable(dms1_rt_audio frontends/runtime/dms1_rt_audio.cpp)
    target_link_libraries(dms1_rt_audio PRIVATE dms1_core winmm)
endif()

if(DMS1_BUILD_TESTS)
    enable_testing()
    add_executable(dms1_realtime_core_test tests/realtime_core_test.cpp)
    target_link_libraries(dms1_realtime_core_test PRIVATE dms1_core)
    add_test(NAME dms1_realtime_core_test COMMAND dms1_realtime_core_test)
    add_executable(dms1_sample_codec_test tests/sample_codec_test.cpp)
    target_link_libraries(dms1_sample_codec_test PRIVATE dms1_core)
    add_test(NAME dms1_sample_codec_test COMMAND dms1_sample_codec_test)
    add_executable(dms1_fm_slot_protocol_test tests/fm_slot_protocol_test.cpp)
    target_include_directories(dms1_fm_slot_protocol_test PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src")
    add_test(NAME dms1_fm_slot_protocol_test COMMAND dms1_fm_slot_protocol_test)
    add_executable(dms1_recording_rom_test tests/recording_rom_test.cpp)
    target_link_libraries(dms1_recording_rom_test PRIVATE dms1_core)
    add_test(NAME dms1_recording_rom_test COMMAND dms1_recording_rom_test)
    add_executable(dms1_tx81z_import_test tests/tx81z_import_test.cpp)
    target_link_libraries(dms1_tx81z_import_test PRIVATE dms1_core)
    add_test(NAME dms1_tx81z_import_test COMMAND dms1_tx81z_import_test)
endif()

if(DMS1_BUILD_VST3)
    set(JUCE_DIR "" CACHE PATH "Path to a JUCE 8 source checkout")
    if(NOT JUCE_DIR AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/third_party/JUCE/CMakeLists.txt")
        set(JUCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party/JUCE" CACHE PATH "Path to JUCE" FORCE)
    elseif(NOT JUCE_DIR AND DEFINED ENV{JUCE_DIR})
        file(TO_CMAKE_PATH "$ENV{JUCE_DIR}" JUCE_DIR_FROM_ENV)
        if(EXISTS "${JUCE_DIR_FROM_ENV}/CMakeLists.txt")
            set(JUCE_DIR "${JUCE_DIR_FROM_ENV}" CACHE PATH "Path to JUCE" FORCE)
        endif()
    endif()
    if(NOT JUCE_DIR OR NOT EXISTS "${JUCE_DIR}/CMakeLists.txt")
        message(FATAL_ERROR "JUCE 8 introuvable. Utiliser BUILD_AND_INSTALL_DMS1_VST3.bat ou definir JUCE_DIR.")
    endif()
    add_subdirectory("${JUCE_DIR}" "${CMAKE_BINARY_DIR}/JUCE")

    juce_add_binary_data(DMS1VSTAssets
        HEADER_NAME DMS1BinaryData.h
        NAMESPACE DMS1BinaryData
        SOURCES
            instruments/tx81z_gstorm_01.json
            instruments/factory_kick.adpcma
            instruments/factory_snare.adpcma
            instruments/factory_hihat.adpcma
            instruments/factory_texture.adpcmb)

    juce_add_plugin(DMS1VST3
        COMPANY_NAME "DAC Master"
        BUNDLE_ID "com.dacmaster.dms1"
        PLUGIN_MANUFACTURER_CODE DACM
        PLUGIN_CODE DMS1
        FORMATS VST3
        PRODUCT_NAME "DAC MASTER DMS-1"
        IS_SYNTH TRUE
        NEEDS_MIDI_INPUT TRUE
        NEEDS_MIDI_OUTPUT FALSE
        IS_MIDI_EFFECT FALSE
        EDITOR_WANTS_KEYBOARD_FOCUS FALSE
        COPY_PLUGIN_AFTER_BUILD FALSE
        VST3_CATEGORIES "Instrument;Synth")

    juce_generate_juce_header(DMS1VST3)
    target_sources(DMS1VST3 PRIVATE
        frontends/vst3/source/PluginProcessor.cpp
        frontends/vst3/source/PluginProcessor.h
        frontends/vst3/source/PluginEditor.cpp
        frontends/vst3/source/PluginEditor.h)
    target_include_directories(DMS1VST3 PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/frontends/vst3/source"
        "${CMAKE_CURRENT_SOURCE_DIR}/src")
    target_compile_definitions(DMS1VST3 PUBLIC
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_VST3_CAN_REPLACE_VST2=0)
    if(MSVC)
        target_compile_options(DMS1VST3 PRIVATE /utf-8)
    endif()
    target_link_libraries(DMS1VST3 PRIVATE
        dms1_core
        DMS1VSTAssets
        juce::juce_audio_utils
        PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags)
endif()
