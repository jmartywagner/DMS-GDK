#!/usr/bin/env python3
"""DAC MASTER DMS-1 Player Lab P0.4.1 for native DMR ROMs.

The emulator remains authoritative: a ROM is executed by dms1emu into one
deterministic PCM buffer when loaded. Windows then plays that exact buffer and
the Render WAV button copies the same bytes, with no normalization or effects.
"""

from __future__ import annotations

import argparse
import bisect
import ctypes
import json
import math
import os
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path


SYSTEM_CLOCK = 24_000_000
ADPCM_A_RATE = 8_000_000 / 432
ADPCM_B_SERVICE_RATE = 8_000_000 / 144
TAIL_SECONDS = 0.5
MAX_RENDER_SECONDS = 600
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHANNEL_NAMES = (
    "FM 1", "FM 2", "FM 3", "FM 4",
    "SSG A", "SSG B", "SSG C",
    "ADPCM-A", "ADPCM-B",
)


class DmrError(ValueError):
    pass


def render_limit_seconds(analysis: "RomAnalysis") -> int:
    """Choose the smallest safe engine limit for this already-analysed ROM."""
    required = max(1, math.ceil(analysis.duration_seconds + 1.0))
    if required > MAX_RENDER_SECONDS:
        raise DmrError(
            f"ROM de {analysis.duration_seconds:.1f} s: le rendu DMS-1 V0 est "
            f"limite a {MAX_RENDER_SECONDS} s"
        )
    return max(60, required)


@dataclass(frozen=True)
class SampleEntry:
    sample_id: int
    codec: int
    start_page: int
    end_page: int
    source_rate: int

    @property
    def pages(self) -> int:
        return self.end_page - self.start_page + 1


@dataclass(frozen=True)
class RomAnalysis:
    path: Path
    title: str
    author: str
    rom_bytes: int
    duration_seconds: float
    samples_a: int
    samples_b: int
    intervals: dict[str, tuple[tuple[float, float], ...]]

    def active(self, channel: str, seconds: float) -> bool:
        entries = self.intervals[channel]
        if not entries:
            return False
        starts = [entry[0] for entry in entries]
        index = bisect.bisect_right(starts, seconds) - 1
        return index >= 0 and seconds < entries[index][1]


class IntervalTracker:
    def __init__(self) -> None:
        self.intervals: dict[str, list[tuple[float, float]]] = {
            name: [] for name in CHANNEL_NAMES
        }
        self.opened: dict[str, float | None] = {name: None for name in CHANNEL_NAMES}

    def set(self, channel: str, active: bool, seconds: float) -> None:
        opened = self.opened[channel]
        if active and opened is None:
            self.opened[channel] = seconds
        elif not active and opened is not None:
            if seconds > opened:
                self.intervals[channel].append((opened, seconds))
            self.opened[channel] = None

    def retrigger(self, channel: str, seconds: float) -> None:
        self.set(channel, False, seconds)
        self.set(channel, True, seconds)

    def close_all(self, seconds: float) -> None:
        for channel in CHANNEL_NAMES:
            self.set(channel, False, seconds)


def _be16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise DmrError("lecture 16 bits hors ROM")
    return struct.unpack_from(">H", data, offset)[0]


def _be32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise DmrError("lecture 32 bits hors ROM")
    return struct.unpack_from(">I", data, offset)[0]


def _require(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise DmrError(f"{label} hors limites")


def _read_uleb(data: bytes, cursor: int, code_end: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(10):
        if cursor >= code_end:
            raise DmrError("ULEB128 tronque")
        byte = data[cursor]
        cursor += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, cursor
        shift += 7
    raise DmrError("ULEB128 invalide")


def analyze_dmr(path: Path) -> RomAnalysis:
    path = path.resolve()
    data = path.read_bytes()
    if len(data) < 64 or data[:4] != b"DMR0":
        raise DmrError("ce fichier n'est pas une ROM DMR")
    if data[0x10:0x14] != b"DMS1":
        raise DmrError("hardware ID different de DMS1")
    if _be32(data, 0x0C) != len(data):
        raise DmrError("taille DMR incoherente")
    if _be32(data, 0x24) != SYSTEM_CLOCK:
        raise DmrError("timebase differente de 24 MHz")

    directory = _be32(data, 0x18)
    count = _be16(data, 0x1C)
    entry_size = _be16(data, 0x1E)
    if entry_size != 16:
        raise DmrError("repertoire DMR incompatible")
    _require(data, directory, count * entry_size, "repertoire")
    chunks: dict[bytes, tuple[int, int]] = {}
    for index in range(count):
        kind, offset, size, _flags = struct.unpack_from(">4sIII", data, directory + index * 16)
        _require(data, offset, size, f"chunk {kind!r}")
        chunks[kind] = (offset, size)
    if b"CODE" not in chunks:
        raise DmrError("chunk CODE absent")

    metadata: dict[str, str] = {}
    if b"META" in chunks:
        offset, size = chunks[b"META"]
        text = data[offset:offset + size].decode("utf-8", errors="replace")
        for line in text.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                metadata[key.strip()] = value.strip()

    if (b"SDIR" in chunks) != (b"SAMP" in chunks):
        raise DmrError("SDIR et SAMP doivent etre presents ensemble")

    samples: dict[int, SampleEntry] = {}
    if b"SDIR" in chunks:
        offset, size = chunks[b"SDIR"]
        if size % 16:
            raise DmrError("SDIR non multiple de 16")
        for cursor in range(offset, offset + size, 16):
            sample_id, codec, _flags, start, end, rate, _level, _pan, _root, _fine = (
                struct.unpack_from(">HBBHHIBBBb", data, cursor)
            )
            samples[sample_id] = SampleEntry(sample_id, codec, start, end, rate)

    code_offset, code_size = chunks[b"CODE"]
    entrypoint = _be32(data, 0x20)
    code_end = code_offset + code_size
    if not code_offset <= entrypoint < code_end:
        raise DmrError("entrypoint hors CODE")

    tracker = IntervalTracker()
    cursor = entrypoint
    cycle = 0
    loops: dict[int, tuple[int, int]] = {}
    a_end: float | None = None
    b_end: float | None = None
    instruction_budget = 1_000_000

    def seconds_now() -> float:
        return cycle / SYSTEM_CLOCK

    def expire_samples(now: float) -> None:
        nonlocal a_end, b_end
        if a_end is not None and a_end <= now:
            tracker.set("ADPCM-A", False, a_end)
            a_end = None
        if b_end is not None and b_end <= now:
            tracker.set("ADPCM-B", False, b_end)
            b_end = None

    def write_mmio(address: int, value: int, now: float) -> None:
        if 0x0020 <= address <= 0x0023:
            tracker.set(f"FM {address - 0x001F}", bool(value & 0x40), now)
        elif 0x0108 <= address <= 0x010A:
            tracker.set(f"SSG {chr(ord('A') + address - 0x0108)}", bool(value & 0x1F), now)

    halt_seconds: float | None = None
    while instruction_budget:
        instruction_budget -= 1
        if not code_offset <= cursor < code_end:
            raise DmrError("execution DSEQ hors CODE")
        expire_samples(seconds_now())
        instruction_pc = cursor
        opcode = data[cursor]
        cursor += 1

        if opcode == 0x00:
            halt_seconds = seconds_now()
            break
        if opcode == 0x01:
            duration, cursor = _read_uleb(data, cursor, code_end)
            cycle += duration
            continue
        if opcode == 0x10:
            _require(data, cursor, 3, "WR8")
            address = _be16(data, cursor)
            value = data[cursor + 2]
            cursor += 3
            write_mmio(address, value, seconds_now())
            continue
        if opcode == 0x11:
            _require(data, cursor, 3, "WRN")
            address = _be16(data, cursor)
            length = data[cursor + 2]
            cursor += 3
            _require(data, cursor, length, "donnees WRN")
            for index in range(length):
                write_mmio(address + index, data[cursor + index], seconds_now())
            cursor += length
            continue
        if opcode == 0x20:
            _require(data, cursor, 4, "PLAY_A")
            sample_id = _be16(data, cursor)
            cursor += 4
            sample = samples.get(sample_id)
            if sample is None or sample.codec != 1:
                raise DmrError("PLAY_A cible un sample invalide")
            now = seconds_now()
            tracker.retrigger("ADPCM-A", now)
            a_end = now + sample.pages * 512 / ADPCM_A_RATE
            continue
        if opcode == 0x21:
            tracker.set("ADPCM-A", False, seconds_now())
            a_end = None
            continue
        if opcode == 0x22:
            _require(data, cursor, 7, "PLAY_B")
            sample_id = _be16(data, cursor)
            delta_n = _be16(data, cursor + 2)
            flags = data[cursor + 6]
            cursor += 7
            sample = samples.get(sample_id)
            if sample is None or sample.codec != 2 or delta_n == 0:
                raise DmrError("PLAY_B cible un sample invalide")
            now = seconds_now()
            tracker.retrigger("ADPCM-B", now)
            if flags & 1:
                b_end = None
            else:
                rate = ADPCM_B_SERVICE_RATE * delta_n / 65536
                b_end = now + sample.pages * 512 / rate
            continue
        if opcode == 0x23:
            tracker.set("ADPCM-B", False, seconds_now())
            b_end = None
            continue
        if opcode == 0x30:
            _require(data, cursor, 4, "JUMP")
            target = _be32(data, cursor)
            if not code_offset <= target < code_end:
                raise DmrError("cible JUMP hors CODE")
            cursor = target
            continue
        if opcode == 0x31:
            _require(data, cursor, 7, "LOOP")
            slot = data[cursor]
            count_value = _be16(data, cursor + 1)
            target = _be32(data, cursor + 3)
            cursor += 7
            if slot >= 8 or count_value == 0 or not code_offset <= target < code_end:
                raise DmrError("parametres LOOP invalides")
            state = loops.get(slot)
            if state is None or state[0] != instruction_pc:
                state = (instruction_pc, count_value)
            if state[1] > 1:
                loops[slot] = (state[0], state[1] - 1)
                cursor = target
            else:
                loops.pop(slot, None)
            continue
        raise DmrError(f"opcode DSEQ inconnu ${opcode:02X}")

    if halt_seconds is None:
        raise DmrError("DSEQ sans HALT ou budget d'analyse depasse")
    final_seconds = halt_seconds + TAIL_SECONDS
    expire_samples(final_seconds)
    tracker.close_all(final_seconds)
    frozen = {
        name: tuple(sorted(tracker.intervals[name]))
        for name in CHANNEL_NAMES
    }
    return RomAnalysis(
        path=path,
        title=metadata.get("title", path.stem),
        author=metadata.get("author", "Auteur non renseigne"),
        rom_bytes=len(data),
        duration_seconds=final_seconds,
        samples_a=sum(sample.codec == 1 for sample in samples.values()),
        samples_b=sum(sample.codec == 2 for sample in samples.values()),
        intervals=frozen,
    )


class MciWave:
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("la lecture MCI est reservee a Windows")
        self.alias = "dms1playerlab"
        self.winmm = ctypes.WinDLL("winmm")
        self.winmm.mciSendStringW.argtypes = (
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p
        )
        self.winmm.mciSendStringW.restype = ctypes.c_uint
        self.winmm.mciGetErrorStringW.argtypes = (
            ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint
        )
        self.opened = False

    def send(self, command: str, result: bool = False) -> str:
        buffer = ctypes.create_unicode_buffer(512) if result else None
        code = self.winmm.mciSendStringW(command, buffer, 512 if buffer else 0, None)
        if code:
            message = ctypes.create_unicode_buffer(512)
            self.winmm.mciGetErrorStringW(code, message, 512)
            raise RuntimeError(f"MCI {code}: {message.value} [{command}]")
        return buffer.value if buffer else ""

    def open(self, path: Path) -> None:
        self.close()
        self.send(f'open "{path}" type waveaudio alias {self.alias}')
        self.opened = True
        self.send(f"set {self.alias} time format milliseconds")

    def play(self) -> None:
        mode = self.mode()
        if mode == "paused":
            self.send(f"resume {self.alias}")
        else:
            self.send(f"play {self.alias}")

    def pause(self) -> None:
        if self.opened and self.mode() == "playing":
            self.send(f"pause {self.alias}")

    def stop(self) -> None:
        if self.opened:
            self.send(f"stop {self.alias}")
            self.send(f"seek {self.alias} to start")

    def position_ms(self) -> int:
        return int(self.send(f"status {self.alias} position", True)) if self.opened else 0

    def length_ms(self) -> int:
        return int(self.send(f"status {self.alias} length", True)) if self.opened else 0

    def mode(self) -> str:
        return self.send(f"status {self.alias} mode", True).strip().lower() if self.opened else "closed"

    def close(self) -> None:
        if self.opened:
            try:
                self.send(f"close {self.alias}")
            finally:
                self.opened = False


class PlayerLab:
    COLORS = {
        "FM": "#34d8ff",
        "SSG": "#5ee06f",
        "ADPCM-A": "#ffae42",
        "ADPCM-B": "#c778ff",
    }

    def __init__(self, initial_rom: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.root = tk.Tk()
        self.root.title("DAC MASTER - DMS-1 Player Lab P0.4.1")
        self.root.geometry("820x540")
        self.root.minsize(760, 480)
        self.root.configure(bg="#10151c")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.mci = MciWave()
        self.analysis: RomAnalysis | None = None
        self.cache_wav: Path | None = None
        self.worker: threading.Thread | None = None
        self.result_queue: queue.Queue[tuple[object, ...]] = queue.Queue()
        self.length_ms = 0
        self.channel_widgets: dict[str, tk.Label] = {}
        self.buttons: list[ttk.Button] = []

        self.title_var = tk.StringVar(value="Aucune ROM chargee")
        self.meta_var = tk.StringVar(value="DMR 0.1 / NATIVE89 / sortie +9 dB")
        self.status_var = tk.StringVar(value="Ouvre une ROM DMS-1 .dmr")
        self.time_var = tk.StringVar(value="00:00.000 / 00:00.000")
        self.level_var = tk.StringVar(value="Peak : -")
        self.output_stage_var = tk.StringVar(value="DMS-1 HARDWARE")
        self.loaded_output_stage = "DMS-1 HARDWARE"
        self._build_ui()
        self.root.after(50, self._poll)
        if initial_rom and initial_rom.exists():
            self.root.after(200, lambda: self.load_rom(initial_rom))

    def _build_ui(self) -> None:
        tk, ttk = self.tk, self.ttk
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=(12, 7), background="#263344", foreground="#f2f5f8")
        style.map("TButton", background=[("active", "#34465c")])
        style.configure("Horizontal.TProgressbar", troughcolor="#202a36", background="#34d8ff")

        header = tk.Frame(self.root, bg="#10151c")
        header.pack(fill="x", padx=22, pady=(18, 10))
        tk.Label(header, text="DMS-1 PLAYER LAB", bg="#10151c", fg="#34d8ff",
                 font=("Segoe UI Semibold", 18)).pack(anchor="w")
        tk.Label(header, textvariable=self.title_var, bg="#10151c", fg="#ffffff",
                 font=("Segoe UI", 13)).pack(anchor="w", pady=(4, 0))
        tk.Label(header, textvariable=self.meta_var, bg="#10151c", fg="#9baabd",
                 font=("Consolas", 9)).pack(anchor="w", pady=(2, 0))

        controls = tk.Frame(self.root, bg="#10151c")
        controls.pack(fill="x", padx=22, pady=6)
        open_button = ttk.Button(controls, text="Ouvrir DMR", command=self.choose_rom)
        open_button.pack(side="left", padx=(0, 8))
        play = ttk.Button(controls, text="Lecture", command=self.play, state="disabled")
        pause = ttk.Button(controls, text="Pause", command=self.pause, state="disabled")
        stop = ttk.Button(controls, text="Stop", command=self.stop, state="disabled")
        export = ttk.Button(controls, text="Render WAV", command=self.export_wav, state="disabled")
        for button in (play, pause, stop, export):
            button.pack(side="left", padx=4)
            self.buttons.append(button)
        self.stage_selector = ttk.Combobox(
            controls,
            textvariable=self.output_stage_var,
            values=("DMS-1 HARDWARE", "RAW DIGITAL"),
            width=17,
            state="readonly",
        )
        self.stage_selector.pack(side="left", padx=(12, 4))
        self.stage_selector.bind("<<ComboboxSelected>>", self._output_stage_changed)
        tk.Label(controls, textvariable=self.level_var, bg="#10151c", fg="#ffcf70",
                 font=("Consolas", 10)).pack(side="right")

        progress_frame = tk.Frame(self.root, bg="#10151c")
        progress_frame.pack(fill="x", padx=22, pady=(10, 6))
        self.progress = ttk.Progressbar(progress_frame, maximum=1000, value=0)
        self.progress.pack(fill="x")
        tk.Label(progress_frame, textvariable=self.time_var, bg="#10151c", fg="#c5d0dd",
                 font=("Consolas", 10)).pack(anchor="e", pady=(4, 0))

        panel = tk.Frame(self.root, bg="#151d27", highlightthickness=1,
                         highlightbackground="#2b394a")
        panel.pack(fill="both", expand=True, padx=22, pady=10)
        tk.Label(panel, text="ACTIVITE DES 9 VOIX", bg="#151d27", fg="#9baabd",
                 font=("Segoe UI Semibold", 10)).grid(row=0, column=0, columnspan=3,
                                                       sticky="w", padx=14, pady=(12, 8))
        for index, channel in enumerate(CHANNEL_NAMES):
            row = 1 + index // 3
            column = index % 3
            widget = tk.Label(panel, text=channel, bg="#242e3a", fg="#728195",
                              width=18, height=3, relief="flat",
                              font=("Segoe UI Semibold", 10))
            widget.grid(row=row, column=column, sticky="nsew", padx=8, pady=8)
            self.channel_widgets[channel] = widget
        for column in range(3):
            panel.columnconfigure(column, weight=1)
        for row in range(1, 4):
            panel.rowconfigure(row, weight=1)

        footer = tk.Frame(self.root, bg="#10151c")
        footer.pack(fill="x", padx=22, pady=(0, 14))
        tk.Label(footer, textvariable=self.status_var, bg="#10151c", fg="#9baabd",
                 font=("Segoe UI", 9)).pack(anchor="w")

    def set_ready(self, ready: bool) -> None:
        for button in self.buttons:
            button.configure(state="normal" if ready else "disabled")

    def choose_rom(self) -> None:
        filename = self.filedialog.askopenfilename(
            title="Ouvrir une ROM DMS-1",
            filetypes=(("DAC MASTER ROM", "*.dmr"), ("Tous les fichiers", "*.*")),
        )
        if filename:
            self.load_rom(Path(filename))

    def emulator_path(self) -> Path:
        return PROJECT_ROOT / "build" / ("dms1emu.exe" if os.name == "nt" else "dms1emu")

    def output_stage_cli(self) -> str:
        return "raw" if self.output_stage_var.get() == "RAW DIGITAL" else "hardware"

    def _output_stage_changed(self, _event: object | None = None) -> None:
        if self.analysis and not (self.worker and self.worker.is_alive()):
            self.load_rom(self.analysis.path)
        else:
            self.status_var.set(f"Sortie selectionnee : {self.output_stage_var.get()}")

    def load_rom(self, path: Path) -> None:
        if self.worker and self.worker.is_alive():
            return
        emulator = self.emulator_path()
        if not emulator.exists():
            self.messagebox.showerror(
                "Emulateur absent",
                "build\\dms1emu.exe est absent. Lance d'abord "
                "DMS1_BUILD_AUTO_MSYS2_R3.bat.",
            )
            return
        self.stop()
        self.set_ready(False)
        self.stage_selector.configure(state="disabled")
        self.title_var.set(path.name)
        stage_cli = self.output_stage_cli()
        stage_label = self.output_stage_var.get()
        self.status_var.set(
            f"Execution de la ROM et preparation du buffer {stage_label}..."
        )

        def worker() -> None:
            temporary: Path | None = None
            try:
                analysis = analyze_dmr(path)
                descriptor, temp_name = tempfile.mkstemp(prefix="dms1-player-", suffix=".wav")
                os.close(descriptor)
                temporary = Path(temp_name)
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                result = subprocess.run(
                    [str(emulator), str(path), str(temporary),
                     "--output-stage", stage_cli,
                     "--max-seconds", str(render_limit_seconds(analysis))],
                    cwd=PROJECT_ROOT,
                    text=True,
                    capture_output=True,
                    creationflags=flags,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError(result.stderr.strip() or result.stdout.strip())
                output = result.stdout
                self.result_queue.put(("loaded", analysis, temporary, output, stage_label))
            except Exception as error:  # GUI boundary: preserve the full diagnostic.
                if temporary:
                    temporary.unlink(missing_ok=True)
                self.result_queue.put(("error", str(error)))

        self.worker = threading.Thread(target=worker, name="dms1-render", daemon=True)
        self.worker.start()

    def _load_finished(self, analysis: RomAnalysis, temporary: Path,
                       output: str, stage_label: str) -> None:
        try:
            self.mci.close()
            if self.cache_wav:
                self.cache_wav.unlink(missing_ok=True)
            self.mci.open(temporary)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            self._load_failed(str(error))
            return
        self.analysis = analysis
        self.loaded_output_stage = stage_label
        self.cache_wav = temporary
        self.length_ms = self.mci.length_ms()
        peak = None
        for line in output.splitlines():
            if line.startswith("Peak:"):
                try:
                    peak = int(line.split()[1])
                except (ValueError, IndexError):
                    pass
        peak_db = 20 * math.log10(peak / 32767) if peak else float("-inf")
        self.title_var.set(analysis.title)
        self.meta_var.set(
            f"{analysis.author}  |  DMR 0.1 / NATIVE89  |  "
            f"{stage_label}  |  {analysis.rom_bytes:,} octets  |  "
            f"A:{analysis.samples_a} B:{analysis.samples_b}"
        )
        self.level_var.set(f"Peak : {peak_db:.2f} dBFS" if peak else "Peak : -")
        self.status_var.set(
            f"ROM executee. Buffer {stage_label} pret pour lecture ou export WAV."
        )
        self.stage_selector.configure(state="readonly")
        self.set_ready(True)
        self._update_display(0)

    def _load_failed(self, message: str) -> None:
        self.status_var.set("Echec du chargement DMR")
        self.set_ready(False)
        self.stage_selector.configure(state="readonly")
        self.messagebox.showerror("Erreur DMS-1", message)

    def play(self) -> None:
        if not self.analysis:
            return
        if self.mci.position_ms() >= max(0, self.length_ms - 20):
            self.mci.stop()
        self.mci.play()
        self.status_var.set("Lecture de la ROM DMR")

    def pause(self) -> None:
        self.mci.pause()
        self.status_var.set("Lecture en pause")

    def stop(self) -> None:
        if self.mci.opened:
            self.mci.stop()
        self.status_var.set("Lecture arretee")
        self._update_display(0)

    def export_wav(self) -> None:
        if not self.cache_wav or not self.analysis:
            return
        filename = self.filedialog.asksaveasfilename(
            title="Render WAV",
            defaultextension=".wav",
            initialfile=(
                f"{self.analysis.path.stem} - "
                f"{'DMS-1 HARDWARE' if self.loaded_output_stage == 'DMS-1 HARDWARE' else 'RAW DIGITAL'}.wav"
            ),
            filetypes=(("WAV 44,1 kHz / 16-bit stereo", "*.wav"),),
        )
        if filename:
            shutil.copyfile(self.cache_wav, filename)
            self.status_var.set(f"WAV rendu : {filename}")

    @staticmethod
    def _format_ms(milliseconds: int) -> str:
        minutes, remainder = divmod(max(0, milliseconds), 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f"{minutes:02d}:{seconds:02d}.{millis:03d}"

    def _channel_color(self, channel: str) -> str:
        if channel.startswith("FM"):
            return self.COLORS["FM"]
        if channel.startswith("SSG"):
            return self.COLORS["SSG"]
        return self.COLORS[channel]

    def _update_display(self, position_ms: int) -> None:
        self.progress.configure(value=(position_ms / self.length_ms * 1000) if self.length_ms else 0)
        self.time_var.set(f"{self._format_ms(position_ms)} / {self._format_ms(self.length_ms)}")
        seconds = position_ms / 1000
        for channel, widget in self.channel_widgets.items():
            active = bool(self.analysis and self.analysis.active(channel, seconds))
            widget.configure(
                bg=self._channel_color(channel) if active else "#242e3a",
                fg="#091018" if active else "#728195",
            )

    def _poll(self) -> None:
        try:
            while True:
                result = self.result_queue.get_nowait()
                if result[0] == "loaded":
                    self._load_finished(result[1], result[2], result[3], result[4])
                else:
                    self._load_failed(str(result[1]))
        except queue.Empty:
            pass
        try:
            if self.mci.opened:
                self._update_display(self.mci.position_ms())
        except RuntimeError as error:
            self.status_var.set(str(error))
        self.root.after(50, self._poll)

    def close(self) -> None:
        self.mci.close()
        if self.cache_wav:
            self.cache_wav.unlink(missing_ok=True)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def self_test(path: Path) -> int:
    analysis = analyze_dmr(path)
    counts = {name: len(analysis.intervals[name]) for name in CHANNEL_NAMES}
    missing = [name for name, count in counts.items() if count == 0]
    if missing:
        raise DmrError(f"voix sans activite dans la demo: {', '.join(missing)}")
    print(json.dumps({
        "title": analysis.title,
        "duration_seconds": round(analysis.duration_seconds, 6),
        "rom_bytes": analysis.rom_bytes,
        "samples_a": analysis.samples_a,
        "samples_b": analysis.samples_b,
        "activity_intervals": counts,
    }, ensure_ascii=False, sort_keys=True))
    return 0


def inspect_json(path: Path) -> int:
    analysis = analyze_dmr(path)
    counts = {name: len(analysis.intervals[name]) for name in CHANNEL_NAMES}
    print(json.dumps({
        "title": analysis.title,
        "duration_seconds": round(analysis.duration_seconds, 6),
        "rom_bytes": analysis.rom_bytes,
        "samples_a": analysis.samples_a,
        "samples_b": analysis.samples_b,
        "activity_intervals": counts,
    }, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--inspect-json", action="store_true")
    args = parser.parse_args()
    default_rom = PROJECT_ROOT / "roms" / "dms1_p041_tx81z_audition.dmr"
    rom = args.rom or default_rom
    if args.self_test:
        return self_test(rom)
    if args.inspect_json:
        return inspect_json(rom)
    if os.name != "nt":
        print("DMS-1 Player Lab P0.4.1: l'interface audio est reservee a Windows.", file=sys.stderr)
        return 2
    PlayerLab(rom if rom.exists() else None).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
