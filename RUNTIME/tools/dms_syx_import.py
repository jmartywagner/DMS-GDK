#!/usr/bin/env python3
"""Inspect Yamaha SysEx and import native TX81Z VMEM banks into DMS-OPZ."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BANK_FORMAT = "DMS-OPZ-BANK-0.1"
TX81Z_VMEM_BYTES = 4104
TX81Z_PAYLOAD_BYTES = 4096
TX81Z_VOICE_BYTES = 128
TX81Z_VOICE_COUNT = 32
OPERATOR_ORDER = ("OP4", "OP2", "OP3", "OP1")


class SyxError(ValueError):
    pass


@dataclass(frozen=True)
class SyxInspection:
    format_id: str
    description: str
    messages: int
    convertible: bool
    checksum_valid: bool | None


@dataclass(frozen=True)
class Tx81zOperator:
    attack: int
    decay_1: int
    decay_2: int
    release: int
    decay_1_level: int
    level_scaling: int
    am_enable: int
    eg_bias_sensitivity: int
    velocity_sensitivity: int
    output_level: int
    multiple: int
    detune_2: int
    rate_scaling: int
    detune_display: int
    detune_register: int
    eg_shift: int
    fixed_mode: int
    fixed_range: int
    waveform: int
    fine: int


@dataclass(frozen=True)
class Tx81zVoice:
    index: int
    name: str
    operators: tuple[Tx81zOperator, ...]
    algorithm: int
    feedback: int
    lfo_sync: int
    lfo_speed: int
    lfo_delay: int
    pitch_mod_depth: int
    amplitude_mod_depth: int
    pitch_mod_sensitivity: int
    amplitude_mod_sensitivity: int
    lfo_waveform: int
    transpose: int
    pitch_bend_range: int
    chorus: int
    mono: int
    sustain: int
    portamento: int
    portamento_mode: int
    portamento_time: int
    foot_volume: int
    mod_wheel_pitch: int
    mod_wheel_amplitude: int
    breath_pitch: int
    breath_amplitude: int
    breath_pitch_bias: int
    breath_eg_bias: int
    reverb_rate: int
    foot_pitch: int
    foot_amplitude: int


_LOW_OUTPUT_LEVELS = (
    0, 5, 9, 13, 17, 20, 23, 25, 27, 29,
    31, 33, 35, 37, 39, 41, 42, 44, 46, 47,
)


def split_sysex(data: bytes) -> tuple[bytes, ...]:
    """Split a file containing one or more adjacent complete SysEx messages."""
    messages: list[bytes] = []
    cursor = 0
    while cursor < len(data):
        if data[cursor] != 0xF0:
            raise SyxError(f"octet hors message SysEx a l'adresse {cursor}")
        end = data.find(b"\xF7", cursor + 1)
        if end < 0:
            raise SyxError("message SysEx sans terminaison F7")
        message = data[cursor:end + 1]
        if any(byte & 0x80 for byte in message[1:-1]):
            raise SyxError("un octet de donnees SysEx depasse 7 bits")
        messages.append(message)
        cursor = end + 1
    if not messages:
        raise SyxError("fichier SysEx vide")
    return tuple(messages)


def _is_reface_dx(messages: tuple[bytes, ...]) -> bool:
    if len(messages) < 3:
        return False
    yamaha_packets = [
        message for message in messages
        if len(message) >= 13 and message[:2] == b"\xF0\x43"
        and message[3:6] == b"\x7F\x1C\x00"
    ]
    addresses = {message[7:9] for message in yamaha_packets if len(message) >= 10}
    return len(yamaha_packets) == len(messages) and b"\x05\x30" in addresses and b"\x05\x31" in addresses


def inspect_bytes(data: bytes) -> SyxInspection:
    messages = split_sysex(data)
    if len(messages) == 1:
        message = messages[0]
        if len(message) == TX81Z_VMEM_BYTES and message[:2] == b"\xF0\x43" and message[3] == 0x04:
            if message[2] > 0x0F:
                raise SyxError("canal Yamaha TX81Z invalide")
            declared = (message[4] << 7) | message[5]
            if declared != TX81Z_PAYLOAD_BYTES:
                raise SyxError(
                    f"taille VMEM declaree {declared}, attendu {TX81Z_PAYLOAD_BYTES}"
                )
            payload = message[6:6 + TX81Z_PAYLOAD_BYTES]
            checksum = message[-2]
            valid = (sum(payload) + checksum) & 0x7F == 0
            return SyxInspection(
                "YAMAHA-TX81Z-32-VOICE-VMEM",
                "Yamaha TX81Z natif : 32 voix VMEM avec extensions ACED",
                1,
                valid,
                valid,
            )
    if _is_reface_dx(messages):
        return SyxInspection(
            "YAMAHA-REFACE-DX-BULK",
            "Yamaha Reface DX : format multi-paquets 7F 1C, pas un TX81Z VMEM",
            len(messages),
            False,
            None,
        )
    return SyxInspection(
        "UNKNOWN-SYSEX",
        "SysEx reconnu comme conteneur MIDI, mais format de voix non pris en charge",
        len(messages),
        False,
        None,
    )


def inspect_syx(path: Path) -> SyxInspection:
    try:
        return inspect_bytes(path.read_bytes())
    except OSError as error:
        raise SyxError(f"{path}: {error}") from error


def _decode_name(raw: bytes, index: int) -> str:
    try:
        name = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise SyxError(f"voix {index}: nom non ASCII") from error
    if any(ord(character) < 32 or ord(character) > 126 for character in name):
        raise SyxError(f"voix {index}: caractere de nom hors plage Yamaha")
    return name.rstrip() or f"Voice {index:02d}"


def _detune_to_register(value: int) -> int:
    # TX81Z display order is -3,-2,-1,0,+1,+2,+3. OPZ register encoding is
    # 7,6,5,0,1,2,3 for the same signed offsets.
    mapping = (7, 6, 5, 0, 1, 2, 3)
    if not 0 <= value < len(mapping):
        raise SyxError(f"detune TX81Z invalide: {value}")
    return mapping[value]


def _decode_operator(base: bytes, additional_a: int, additional_b: int) -> Tx81zOperator:
    return Tx81zOperator(
        attack=base[0] & 0x1F,
        decay_1=base[1] & 0x1F,
        decay_2=base[2] & 0x1F,
        release=base[3] & 0x0F,
        decay_1_level=base[4] & 0x0F,
        level_scaling=base[5],
        am_enable=(base[6] >> 6) & 1,
        eg_bias_sensitivity=(base[6] >> 3) & 7,
        velocity_sensitivity=base[6] & 7,
        output_level=base[7],
        multiple=base[8] & 0x0F,
        detune_2=(base[8] >> 4) & 3,
        rate_scaling=(base[9] >> 3) & 3,
        detune_display=base[9] & 7,
        detune_register=_detune_to_register(base[9] & 7),
        eg_shift=(additional_a >> 4) & 3,
        fixed_mode=(additional_a >> 3) & 1,
        fixed_range=additional_a & 7,
        waveform=(additional_b >> 4) & 7,
        fine=additional_b & 0x0F,
    )


def _validate_voice_ranges(raw: bytes, index: int) -> None:
    for operator in range(4):
        base = operator * 10
        ranges = (
            (raw[base + 0] & 0x1F, 31, "AR"),
            (raw[base + 1] & 0x1F, 31, "D1R"),
            (raw[base + 2] & 0x1F, 31, "D2R"),
            (raw[base + 3] & 0x0F, 15, "RR"),
            (raw[base + 4] & 0x0F, 15, "D1L"),
            (raw[base + 5], 99, "LS"),
            (raw[base + 7], 99, "OUT"),
        )
        for value, maximum, label in ranges:
            if value > maximum:
                raise SyxError(f"voix {index} {OPERATOR_ORDER[operator]}: {label}={value} invalide")
        if (raw[base + 9] & 7) > 6:
            raise SyxError(f"voix {index} {OPERATOR_ORDER[operator]}: detune invalide")
    simple_ranges = (
        (raw[41], 99, "LFO speed"),
        (raw[42], 99, "LFO delay"),
        (raw[43], 99, "PMD"),
        (raw[44], 99, "AMD"),
        (raw[46], 48, "transpose"),
        (raw[47] & 0x0F, 12, "pitch bend range"),
        (raw[49], 99, "portamento time"),
        (raw[50], 99, "FC volume"),
        (raw[51], 99, "MW pitch"),
        (raw[52], 99, "MW amplitude"),
        (raw[53], 99, "BC pitch"),
        (raw[54], 99, "BC amplitude"),
        (raw[55], 99, "BC pitch bias"),
        (raw[56], 99, "BC EG bias"),
        (raw[82], 99, "FC pitch"),
        (raw[83], 99, "FC amplitude"),
    )
    for value, maximum, label in simple_ranges:
        if value > maximum:
            raise SyxError(f"voix {index}: {label}={value} invalide")


def decode_voice(raw: bytes, index: int) -> Tx81zVoice:
    if len(raw) != TX81Z_VOICE_BYTES:
        raise SyxError(f"voix {index}: taille VMEM differente de 128 octets")
    _validate_voice_ranges(raw, index)
    operators = tuple(
        _decode_operator(raw[operator * 10:operator * 10 + 10], raw[73 + operator * 2], raw[74 + operator * 2])
        for operator in range(4)
    )
    control = raw[40]
    modulation = raw[45]
    switches = raw[48]
    return Tx81zVoice(
        index=index,
        name=_decode_name(raw[57:67], index),
        operators=operators,
        algorithm=control & 7,
        feedback=(control >> 3) & 7,
        lfo_sync=(control >> 6) & 1,
        lfo_speed=raw[41],
        lfo_delay=raw[42],
        pitch_mod_depth=raw[43],
        amplitude_mod_depth=raw[44],
        pitch_mod_sensitivity=(modulation >> 4) & 7,
        amplitude_mod_sensitivity=(modulation >> 2) & 3,
        lfo_waveform=modulation & 3,
        transpose=raw[46],
        pitch_bend_range=raw[47] & 0x0F,
        chorus=(switches >> 4) & 1,
        mono=(switches >> 3) & 1,
        sustain=(switches >> 2) & 1,
        portamento=(switches >> 1) & 1,
        portamento_mode=switches & 1,
        portamento_time=raw[49],
        foot_volume=raw[50],
        mod_wheel_pitch=raw[51],
        mod_wheel_amplitude=raw[52],
        breath_pitch=raw[53],
        breath_amplitude=raw[54],
        breath_pitch_bias=raw[55],
        breath_eg_bias=raw[56],
        reverb_rate=raw[81] & 7,
        foot_pitch=raw[82],
        foot_amplitude=raw[83],
    )


def parse_tx81z_vmem(path: Path) -> tuple[Tx81zVoice, ...]:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SyxError(f"{path}: {error}") from error
    inspection = inspect_bytes(data)
    if inspection.format_id != "YAMAHA-TX81Z-32-VOICE-VMEM":
        raise SyxError(inspection.description)
    if not inspection.checksum_valid:
        raise SyxError("checksum Yamaha TX81Z invalide; import refuse")
    payload = data[6:-2]
    return tuple(
        decode_voice(payload[offset:offset + TX81Z_VOICE_BYTES], index + 1)
        for index, offset in enumerate(range(0, TX81Z_PAYLOAD_BYTES, TX81Z_VOICE_BYTES))
    )


def tx_output_to_total_level(output_level: int) -> int:
    """Convert Yamaha's 0..99 output display law to inverse OPZ TL 0..127."""
    if not 0 <= output_level <= 99:
        raise SyxError(f"operator output level outside 0..99: {output_level}")
    linear_level = _LOW_OUTPUT_LEVELS[output_level] if output_level < 20 else output_level + 28
    return 127 - linear_level


def _opz_lfo_frequency(register: int) -> float:
    mantissa = 0x10 | (register & 0x0F)
    increment = mantissa << (register >> 4)
    tick_rate = 3_579_545 / 64
    return tick_rate * increment / (1 << 30)


def tx_lfo_speed_to_opz(speed: int) -> int:
    """Map the TX81Z 0..99 UI span to the nearest OPZ 4.4 rate byte.

    Yamaha documents roughly 0.007 Hz at 1, about 10 Hz near the broad
    lower-range knee, and 50 Hz at 99. The chip register itself is a 4.4
    floating-point increment. This explicit V0 approximation can later be
    replaced by hardware measurements without touching the source SysEx.
    """
    if not 0 <= speed <= 99:
        raise SyxError(f"LFO speed outside 0..99: {speed}")
    if speed == 0:
        target = 0.0
    elif speed <= 63:
        target = 0.007 + (10.1 - 0.007) * (speed - 1) / 62
    else:
        target = 10.1 + (50.0 - 10.1) * (speed - 63) / 36
    if target == 0.0:
        return 0
    return min(range(256), key=lambda register: abs(_opz_lfo_frequency(register) - target))


def tx_lfo_depth_to_opz(depth: int) -> int:
    if not 0 <= depth <= 99:
        raise SyxError(f"LFO depth outside 0..99: {depth}")
    return round(depth * 127 / 99)


def _slug(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return normalized or "voice"


def voice_key(voice: Tx81zVoice) -> str:
    return f"{voice.index:02d}_{_slug(voice.name)}"


def voice_to_patch(voice: Tx81zVoice) -> dict[str, Any]:
    operators = voice.operators
    patch: dict[str, Any] = {
        "display_name": voice.name,
        "algorithm": voice.algorithm,
        "feedback": voice.feedback,
        "multiples": [operator.multiple for operator in operators],
        "fine": [operator.fine for operator in operators],
        "waves": [operator.waveform for operator in operators],
        "levels": [tx_output_to_total_level(operator.output_level) for operator in operators],
        "attacks": [operator.attack for operator in operators],
        "decays": [operator.decay_1 for operator in operators],
        "sustains": [operator.decay_2 for operator in operators],
        "sustain_levels": [operator.decay_1_level for operator in operators],
        "releases": [operator.release for operator in operators],
        "reverb_rates": [voice.reverb_rate] * 4,
        "detunes": [operator.detune_register for operator in operators],
        "key_scale_rates": [operator.rate_scaling for operator in operators],
        "fixed_modes": [operator.fixed_mode for operator in operators],
        "fixed_ranges": [operator.fixed_range for operator in operators],
        "fixed_frequencies": [operator.multiple for operator in operators],
        "am_enables": [operator.am_enable for operator in operators],
        "detune2": [operator.detune_2 for operator in operators],
        "eg_shifts": [operator.eg_shift for operator in operators],
        "velocity_sensitivities": [operator.velocity_sensitivity for operator in operators],
        "transpose": voice.transpose,
        "lfo_speed": tx_lfo_speed_to_opz(voice.lfo_speed),
        "lfo_pitch_depth": tx_lfo_depth_to_opz(voice.pitch_mod_depth),
        "lfo_amplitude_depth": tx_lfo_depth_to_opz(voice.amplitude_mod_depth),
        "lfo_pitch_sensitivity": voice.pitch_mod_sensitivity,
        "lfo_amplitude_sensitivity": voice.amplitude_mod_sensitivity,
        "lfo_waveform": voice.lfo_waveform,
        "lfo_sync": voice.lfo_sync,
        "tx81z": {
            "source_slot": voice.index,
            "operator_order": list(OPERATOR_ORDER),
            "output_levels_0_99": [operator.output_level for operator in operators],
            "level_scaling": [operator.level_scaling for operator in operators],
            "eg_bias_sensitivities": [operator.eg_bias_sensitivity for operator in operators],
            "detune_display_0_6": [operator.detune_display for operator in operators],
            "lfo_speed_0_99": voice.lfo_speed,
            "pitch_mod_depth_0_99": voice.pitch_mod_depth,
            "amplitude_mod_depth_0_99": voice.amplitude_mod_depth,
            "lfo_delay": voice.lfo_delay,
            "pitch_bend_range": voice.pitch_bend_range,
            "chorus": voice.chorus,
            "mono": voice.mono,
            "sustain_switch": voice.sustain,
            "portamento": voice.portamento,
            "portamento_mode": voice.portamento_mode,
            "portamento_time": voice.portamento_time,
            "foot_volume": voice.foot_volume,
            "mod_wheel_pitch": voice.mod_wheel_pitch,
            "mod_wheel_amplitude": voice.mod_wheel_amplitude,
            "breath_pitch": voice.breath_pitch,
            "breath_amplitude": voice.breath_amplitude,
            "breath_pitch_bias": voice.breath_pitch_bias,
            "breath_eg_bias": voice.breath_eg_bias,
            "foot_pitch": voice.foot_pitch,
            "foot_amplitude": voice.foot_amplitude,
        },
    }
    return patch


def build_bank(voices: tuple[Tx81zVoice, ...], source_name: str, bank_name: str) -> dict[str, Any]:
    return {
        "format": BANK_FORMAT,
        "name": bank_name,
        "source": {
            "format": "YAMAHA-TX81Z-32-VOICE-VMEM",
            "file": source_name,
            "voices": len(voices),
            "checksum": "valid",
            "operator_order": list(OPERATOR_ORDER),
            "notice": "User-supplied preset data; verify redistribution rights before publishing.",
        },
        "conversion": {
            "version": "DMS TX81Z Bridge P0.4.1",
            "output_level": "Yamaha-family 0..99 display curve converted to inverse OPZ TL 0..127; provisional V0 pending TX81Z A/B",
            "lfo_rate": "TX81Z documented 0.007..50 Hz span mapped to nearest OPZ 4.4 rate; provisional V0 curve",
            "lfo_depth": "TX81Z 0..99 mapped linearly to OPZ 0..127",
            "preserved_not_rendered": [
                "operator level scaling",
                "operator EG bias sensitivity",
                "LFO delay",
                "performance/controller switches and depths",
            ],
            "shared_lfo_constraint": "The last patch configured owns the single DMS-1 OPZ LFO globals.",
        },
        "patches": {voice_key(voice): voice_to_patch(voice) for voice in voices},
    }


def make_report(path: Path, inspection: SyxInspection, voices: tuple[Tx81zVoice, ...] = ()) -> str:
    lines = [
        "DAC MASTER DMS-1 - RAPPORT D'IMPORT SYSEX P0.4.1",
        "=================================================",
        f"Source : {path.name}",
        f"Taille : {path.stat().st_size} octets",
        f"Format : {inspection.format_id}",
        f"Diagnostic : {inspection.description}",
        f"Messages SysEx : {inspection.messages}",
        f"Convertible : {'oui' if inspection.convertible else 'non'}",
    ]
    if inspection.checksum_valid is not None:
        lines.append(f"Checksum Yamaha : {'valide' if inspection.checksum_valid else 'INVALIDE'}")
    if not voices:
        if inspection.format_id == "YAMAHA-REFACE-DX-BULK":
            lines.extend((
                "",
                "Ce fichier est un bulk Reface DX (paquets 7F 1C / modele 05).",
                "Il ne contient pas la structure VMEM 128 octets du TX81Z et n'est pas",
                "converti silencieusement : une traduction Reface separee sera necessaire.",
            ))
        return "\n".join(lines) + "\n"

    lines.extend((
        f"Voix VMEM : {len(voices)} x {TX81Z_VOICE_BYTES} octets",
        f"Ordre physique DMS/OPZ : {', '.join(OPERATOR_ORDER)}",
        "",
        "MAPPING APPLIQUE",
        "----------------",
        "AR/D1R/D2R/RR/D1L, ratio, DT/DT2, RS, KVS, AME, forme d'onde,",
        "fine, mode/range fixe, EG shift, reverb, algorithme et feedback sont importes.",
        "Le niveau OUT Yamaha 0..99 suit la courbe de famille DX/TX retenue en V0,",
        "puis devient le TL inverse OPZ 0..127; un A/B TX81Z reste necessaire.",
        "Transpose et LFO (sans son delai) sont interpretes.",
        "La courbe de vitesse LFO V0 vise les reperes documentes 0,007..50 Hz,",
        "puis choisit le registre flottant 4.4 OPZ le plus proche; elle reste a mesurer.",
        "",
        "CONSERVE DANS LE JSON MAIS PAS ENCORE RENDU",
        "--------------------------------------------",
        "Level Scaling, EG Bias Sensitivity, LFO Delay, chorus/mono/portamento,",
        "pitch bend et affectations de controleurs. Le LFO global est partage :",
        "le dernier patch initialise determine ses registres communs.",
        "",
        "VOIX",
        "-----",
    ))
    for voice in voices:
        waves = "/".join(str(operator.waveform) for operator in voice.operators)
        fixed = "".join("F" if operator.fixed_mode else "-" for operator in voice.operators)
        levels = "/".join(str(operator.output_level) for operator in voice.operators)
        lines.append(
            f"{voice.index:02d}  {voice.name:<10}  key={voice_key(voice):<22} "
            f"ALG={voice.algorithm + 1} FB={voice.feedback} W={waves} FIX={fixed} OUT={levels}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("syx", type=Path, help="fichier SysEx Yamaha")
    parser.add_argument("--out", type=Path, help="banque DMS-OPZ JSON de sortie")
    parser.add_argument("--report", type=Path, help="rapport texte de conversion")
    parser.add_argument("--bank-name", help="nom affiche de la banque")
    parser.add_argument("--inspect", action="store_true", help="identifier sans convertir")
    args = parser.parse_args()

    try:
        inspection = inspect_syx(args.syx)
        voices: tuple[Tx81zVoice, ...] = ()
        if inspection.convertible:
            voices = parse_tx81z_vmem(args.syx)
        report = make_report(args.syx, inspection, voices)
        print(report, end="")
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report, encoding="utf-8", newline="\n")
        if args.inspect:
            return 0
        if (
            inspection.format_id == "YAMAHA-TX81Z-32-VOICE-VMEM"
            and inspection.checksum_valid is False
        ):
            raise SyxError("checksum Yamaha TX81Z invalide; import refuse")
        if not inspection.convertible:
            raise SyxError(inspection.description)
        output = args.out or args.syx.with_suffix(".dmsopz.json")
        bank_name = args.bank_name or f"TX81Z import - {args.syx.stem}"
        bank = build_bank(voices, args.syx.name, bank_name)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(bank, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"Banque DMS-OPZ : {output} | {len(voices)} voix")
        return 0
    except (OSError, SyxError, ValueError) as error:
        print(f"DMS SysEx Import: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
