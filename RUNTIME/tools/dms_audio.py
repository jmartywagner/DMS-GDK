#!/usr/bin/env python3
"""Deterministic WAV preparation and Yamaha ADPCM codecs for DMS-1 tools."""

from __future__ import annotations

import argparse
import math
import struct
import wave
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Sequence


ADPCM_A_RATE = Fraction(8_000_000, 432)
ADPCM_B_SERVICE_RATE = Fraction(8_000_000, 144)
PAGE_BYTES = 256
PAGE_DECODED_SAMPLES = PAGE_BYTES * 2


@dataclass(frozen=True)
class WavData:
    samples: tuple[int, ...]
    sample_rate: int
    channels: int
    bits_per_sample: int


@dataclass(frozen=True)
class EncodedSample:
    encoded: bytes
    pcm: tuple[int, ...]
    nominal_rate: int
    exact_rate: Fraction
    source: WavData

    @property
    def pages(self) -> int:
        return len(self.encoded) // PAGE_BYTES


def _signed_24(data: bytes, offset: int) -> int:
    value = data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16)
    if value & 0x800000:
        value -= 1 << 24
    return value


def read_pcm_wav(path: Path) -> WavData:
    with wave.open(str(path), "rb") as stream:
        if stream.getcomptype() != "NONE":
            raise ValueError(f"{path}: only uncompressed PCM WAV is accepted")
        channels = stream.getnchannels()
        width = stream.getsampwidth()
        sample_rate = stream.getframerate()
        frame_count = stream.getnframes()
        raw = stream.readframes(frame_count)

    if channels < 1 or channels > 8:
        raise ValueError(f"{path}: unsupported channel count {channels}")
    if width not in (1, 2, 3, 4):
        raise ValueError(f"{path}: unsupported PCM width {width * 8} bits")
    expected = frame_count * channels * width
    if len(raw) != expected:
        raise ValueError(f"{path}: truncated WAV payload")

    mono: list[int] = []
    cursor = 0
    for _frame in range(frame_count):
        total = 0
        for _channel in range(channels):
            if width == 1:
                value = (raw[cursor] - 128) << 8
            elif width == 2:
                value = struct.unpack_from("<h", raw, cursor)[0]
            elif width == 3:
                value = _signed_24(raw, cursor) >> 8
            else:
                value = struct.unpack_from("<i", raw, cursor)[0] >> 16
            total += value
            cursor += width
        averaged = int(total / channels)
        mono.append(max(-32768, min(32767, averaged)))
    return WavData(tuple(mono), sample_rate, channels, width * 8)


def resample_windowed_sinc(
    samples: Sequence[int], source_rate: int, target_rate: Fraction, radius: int = 18
) -> tuple[int, ...]:
    if not samples:
        return ()
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    target_count = max(1, round(Fraction(len(samples), source_rate) * target_rate))
    ratio = float(target_rate) / source_rate
    cutoff = 0.5 * min(1.0, ratio) * 0.94
    source_step = source_rate / float(target_rate)
    output: list[int] = []

    for output_index in range(target_count):
        position = output_index * source_step
        center = math.floor(position)
        first = max(0, center - radius + 1)
        last = min(len(samples) - 1, center + radius)
        weighted = 0.0
        weight_sum = 0.0
        for source_index in range(first, last + 1):
            distance = source_index - position
            if abs(distance) >= radius:
                continue
            argument = 2.0 * cutoff * distance
            sinc = 1.0 if argument == 0.0 else math.sin(math.pi * argument) / (math.pi * argument)
            window = 0.5 + 0.5 * math.cos(math.pi * distance / radius)
            weight = 2.0 * cutoff * sinc * window
            weighted += samples[source_index] * weight
            weight_sum += weight
        value = 0 if weight_sum == 0.0 else round(weighted / weight_sum)
        output.append(max(-32768, min(32767, value)))
    return tuple(output)


def pad_pcm_to_page(samples: Sequence[int]) -> tuple[int, ...]:
    padding = (-len(samples)) % PAGE_DECODED_SAMPLES
    return tuple(samples) + (0,) * padding


ADPCM_A_STEPS = (
    16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73,
    80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166,
    1282, 1411, 1552,
)
ADPCM_A_INDEX = (-1, -1, -1, -1, 2, 5, 7, 9)


def _signed_12(value: int) -> int:
    value &= 0xFFF
    return value - 0x1000 if value & 0x800 else value


def adpcm_a_encode(pcm: Sequence[int]) -> bytes:
    accumulator = 0
    step_index = 0
    nibbles: list[int] = []
    for pcm_value in pad_pcm_to_page(pcm):
        target = max(-2048, min(2047, round(pcm_value / 16)))
        step = ADPCM_A_STEPS[step_index]
        best_nibble = 0
        best_accumulator = accumulator
        best_error = 1 << 60
        for nibble in range(16):
            delta = (2 * (nibble & 7) + 1) * step // 8
            if nibble & 8:
                delta = -delta
            candidate = (accumulator + delta) & 0xFFF
            error = abs(target - _signed_12(candidate))
            if error < best_error:
                best_error = error
                best_nibble = nibble
                best_accumulator = candidate
        nibbles.append(best_nibble)
        accumulator = best_accumulator
        step_index = max(0, min(48, step_index + ADPCM_A_INDEX[best_nibble & 7]))

    encoded = bytearray()
    for index in range(0, len(nibbles), 2):
        encoded.append((nibbles[index] << 4) | nibbles[index + 1])
    if len(encoded) % PAGE_BYTES:
        raise AssertionError("ADPCM-A encoder did not produce whole pages")
    return bytes(encoded)


def adpcm_a_decode(encoded: bytes) -> tuple[int, ...]:
    if len(encoded) % PAGE_BYTES:
        raise ValueError("ADPCM-A data must contain whole 256-byte pages")
    accumulator = 0
    step_index = 0
    output: list[int] = []
    for byte in encoded:
        for nibble in (byte >> 4, byte & 0x0F):
            step = ADPCM_A_STEPS[step_index]
            delta = (2 * (nibble & 7) + 1) * step // 8
            if nibble & 8:
                delta = -delta
            accumulator = (accumulator + delta) & 0xFFF
            step_index = max(0, min(48, step_index + ADPCM_A_INDEX[nibble & 7]))
            output.append(_signed_12(accumulator) * 16)
    return tuple(output)


ADPCM_B_SCALES = (57, 57, 57, 57, 77, 102, 128, 153)


def adpcm_b_encode(pcm: Sequence[int]) -> bytes:
    accumulator = 0
    step = 127
    nibbles: list[int] = []
    for target in pad_pcm_to_page(pcm):
        best_nibble = 0
        best_value = accumulator
        best_error = 1 << 60
        for nibble in range(16):
            delta = (2 * (nibble & 7) + 1) * step // 8
            if nibble & 8:
                delta = -delta
            candidate = max(-32768, min(32767, accumulator + delta))
            error = abs(target - candidate)
            if error < best_error:
                best_error = error
                best_nibble = nibble
                best_value = candidate
        nibbles.append(best_nibble)
        accumulator = best_value
        step = max(127, min(24576, step * ADPCM_B_SCALES[best_nibble & 7] // 64))

    encoded = bytearray()
    for index in range(0, len(nibbles), 2):
        encoded.append((nibbles[index] << 4) | nibbles[index + 1])
    if len(encoded) % PAGE_BYTES:
        raise AssertionError("ADPCM-B encoder did not produce whole pages")
    return bytes(encoded)


def adpcm_b_decode(encoded: bytes) -> tuple[int, ...]:
    if len(encoded) % PAGE_BYTES:
        raise ValueError("ADPCM-B data must contain whole 256-byte pages")
    accumulator = 0
    step = 127
    output: list[int] = []
    for byte in encoded:
        for nibble in (byte >> 4, byte & 0x0F):
            delta = (2 * (nibble & 7) + 1) * step // 8
            if nibble & 8:
                delta = -delta
            accumulator = max(-32768, min(32767, accumulator + delta))
            step = max(127, min(24576, step * ADPCM_B_SCALES[nibble & 7] // 64))
            output.append(accumulator)
    return tuple(output)


def prepare_adpcm_a(path: Path) -> EncodedSample:
    source = read_pcm_wav(path)
    converted = resample_windowed_sinc(source.samples, source.sample_rate, ADPCM_A_RATE)
    return EncodedSample(adpcm_a_encode(converted), converted, round(ADPCM_A_RATE), ADPCM_A_RATE, source)


def prepare_adpcm_b(path: Path, rate: int = 26_000) -> EncodedSample:
    if rate <= 0 or rate > round(ADPCM_B_SERVICE_RATE):
        raise ValueError("ADPCM-B target rate is outside the DMS-1 range")
    exact_rate = Fraction(rate, 1)
    source = read_pcm_wav(path)
    converted = resample_windowed_sinc(source.samples, source.sample_rate, exact_rate)
    return EncodedSample(adpcm_b_encode(converted), converted, rate, exact_rate, source)


def write_mono_wav(path: Path, samples: Iterable[int], sample_rate: int) -> None:
    values = tuple(max(-32768, min(32767, int(value))) for value in samples)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(struct.pack(f"<{len(values)}h", *values))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--codec", choices=("a", "b"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rate", type=int, default=26_000, help="ADPCM-B source rate")
    parser.add_argument("--preview", type=Path, help="decoded 16-bit mono WAV")
    args = parser.parse_args()

    if args.codec == "a":
        result = prepare_adpcm_a(args.input)
        decoded = adpcm_a_decode(result.encoded)
    else:
        result = prepare_adpcm_b(args.input, args.rate)
        decoded = adpcm_b_decode(result.encoded)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(result.encoded)
    if args.preview:
        write_mono_wav(args.preview, decoded, result.nominal_rate)

    print(
        f"{args.input.name}: {result.source.sample_rate} Hz, {result.source.channels} ch, "
        f"{result.source.bits_per_sample} bit -> ADPCM-{args.codec.upper()} "
        f"{float(result.exact_rate):.6f} Hz, {len(result.encoded)} bytes, {result.pages} pages"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
