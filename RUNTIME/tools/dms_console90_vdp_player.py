#!/usr/bin/env python3
"""P1.0.5 DMS-1 host-timing + double-buffer frontend: decoupled 60 Hz console + real-time native audio.

The visible DMS-1 framebuffer comes entirely from DMS-1 VRAM/CRAM/VDP state.
Keyboard input is exposed as the virtual PAD register and consumed by the 68000
cartridge program. A/B audio commands still travel through the P0.6 Z80 driver.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import os
import subprocess
import tempfile
import shutil
import struct
import zlib
import threading
import queue
import time
from pathlib import Path

from dms1_player import MciWave, analyze_dmr, render_limit_seconds
from dms_console90_machine import (
    Console90Machine,
    PAD_UP, PAD_DOWN, PAD_LEFT, PAD_RIGHT, PAD_A, PAD_B, PAD_C, PAD_START,
)
from dms_z80_native import compile_and_run, write_ztr1
from dms_realtime_audio import RealtimeAudioClient
from dms_console90_vdp import MODE_PROFILES, HEIGHT
from dms_vdp_native import NativeVdpRenderer
from dms_runtime_profiler import RuntimeProfiler

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROM = ROOT / "roms" / "dms1_console90_cpu_demo.dmc"
SCALE = 3
DISPLAY_WIDTH = 320 * SCALE
DISPLAY_HEIGHT = 224 * SCALE
HUD_HEIGHT = 84
LOW_RES_ZOOM_NUM = 5
LOW_RES_ZOOM_DEN = 2


def rgb_png(width: int, height: int, pixels: bytes) -> bytes:
    """Encode a variable-resolution RGB framebuffer as dependency-free PNG."""
    if len(pixels) != width * height * 3:
        raise ValueError(f"RGB framebuffer size mismatch: {len(pixels)} != {width}x{height}x3")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    stride = width * 3
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        start = y * stride
        scanlines.extend(pixels[start:start + stride])
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(scanlines), level=1))
            + chunk(b"IEND", b""))


class VdpDemoPlayer:
    def __init__(self, rom_path: Path) -> None:
        import tkinter as tk
        self.tk = tk
        self.machine = Console90Machine.from_path(rom_path)
        self.root = tk.Tk()
        self.root.title("DAC MASTER - DMS-1 P1.0.5 HOST TIMING + DOUBLE BUFFER")
        # The physical emulator monitor never changes size. 320x224 modes fill it;
        # LOW RES remains a real 256x224 framebuffer but is letterboxed inside it.
        self.root.geometry(f"{DISPLAY_WIDTH}x{DISPLAY_HEIGHT + HUD_HEIGHT}")
        self.root.resizable(False, False)
        self.root.configure(bg="#000000")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        # P1.0.4: fixed monitor canvas. Replacing a Label whose requested image
        # size changes avoids geometry churn/flicker, especially in LOW RES.
        self.viewport = tk.Canvas(
            self.root, bg="#000000", width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT,
            borderwidth=0, highlightthickness=0, relief="flat",
        )
        self.viewport.pack()
        self._screen_item = self.viewport.create_image(
            DISPLAY_WIDTH // 2, DISPLAY_HEIGHT // 2, anchor="center"
        )
        # Emulator-side diagnostic HUD. This is deliberately outside the DMS-1
        # framebuffer so the video proof remains 100% VRAM/CRAM/VDP output.
        self.hud = tk.Frame(self.root, bg="#0b1016", height=HUD_HEIGHT)
        self.hud.pack(fill="x")
        self.hud.pack_propagate(False)
        self.video_status_var = tk.StringVar(value="VIDEO: MODE 0 STANDARD | 320x224 | 64C | BG A+B | SPR 80/20")
        self.audio_status_var = tk.StringVar(value="AUDIO: PREPARING Z80 NATIVE HARDWARE...")
        self.audio_writes_var = tk.StringVar(value="WRITES 000000 | OPZ 000000 | SSG 000000 | A 000000 | B 000000")
        tk.Label(self.hud, textvariable=self.video_status_var, anchor="w",
                 bg="#0b1016", fg="#ffd166", font=("Consolas", 9, "bold")).pack(fill="x", padx=10, pady=(6, 0))
        tk.Label(self.hud, textvariable=self.audio_status_var, anchor="w",
                 bg="#0b1016", fg="#55e6ff", font=("Consolas", 10, "bold")).pack(fill="x", padx=10, pady=(2, 1))
        tk.Label(self.hud, textvariable=self.audio_writes_var, anchor="w",
                 bg="#0b1016", fg="#b7c3cf", font=("Consolas", 9)).pack(fill="x", padx=10)
        self._photo = None
        self._pressed: set[str] = set()
        self.closed = False
        # P1.0 primary path: Z80-produced register writes are streamed to the
        # C++ RealtimeCore and sent to Windows waveOut. The P0.8.1 WAV cache is
        # retained only as an explicit compatibility fallback if the runtime
        # bridge cannot be built or started.
        self.audio = MciWave()
        self.temp = tempfile.TemporaryDirectory(prefix="dms1_p10_")
        self.sample_bus_path = Path(self.temp.name) / "p10-live-sample-bus.dmr"
        self.sample_bus_path.write_bytes(self.machine.audio_rom)
        self.rt_client: RealtimeAudioClient | None = None
        self.rt_sent_events = 0
        self.rt_sent_until = 0
        self.rt_active = False
        self.rt_failed = False
        self.cached_wav: Path | None = None
        self.audio_prepare_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.audio_prepare_started = False
        self.audio_ready = False
        self.audio_error: str | None = None
        self.pending_play = False
        self.mci_playing = False
        self._last_console_audio_status = None
        self._hud_counted_events = 0
        self._hud_opz = self._hud_ssg = self._hud_adpa = self._hud_adpb = 0

        # P1.0.5 host acceleration + telemetry. The Python VDP remains the reference and
        # fallback; the C++ renderer was regression-compared byte-for-byte for
        # all five hardware modes. This changes host cost, not DMS-1 behaviour.
        try:
            self.native_vdp = NativeVdpRenderer(ROOT)
            self.video_backend = "C++ GDI" if self.native_vdp.has_win32_presenter else "C++ NATIVE"
        except Exception as exc:
            self.native_vdp = None
            self.video_backend = "PYTHON FALLBACK"
            print(f"[VIDEO] native renderer unavailable: {exc}", flush=True)
        self._last_render_frame = -1
        self._last_video_mode = None
        self._render_ms = 0.0
        self._displayed_frames = 0
        self._fps_window_start = time.perf_counter()
        self._display_fps = 0.0

        # P1.0.1: the emulated console clock is no longer tied to expensive Tk
        # framebuffer redraws. A monotonic 60 Hz accumulator advances as many
        # hardware frames as real time requires; video is sampled separately.
        self._sim_last_wall = time.perf_counter()
        self._sim_frame_accum = 0.0
        self._max_catchup_frames = 2
        self._timer_period_active = False
        if os.name == "nt":
            try:
                # Tk/Win32 timers otherwise commonly quantize after(1..8) around
                # the legacy ~15.6 ms Windows timer period. The P1.0.4 report
                # showed 19-25 ms presentation gaps despite ~1 ms raster time.
                if ctypes.windll.winmm.timeBeginPeriod(1) == 0:
                    self._timer_period_active = True
                    print("[HOST] Windows timer resolution: 1 ms", flush=True)
            except Exception as exc:
                print(f"[HOST] timeBeginPeriod unavailable: {exc}", flush=True)

        # P1.0.4: always-on host telemetry. CSV is flushed continuously so it
        # remains useful even if the UI later freezes and Windows kills it.
        self.profiler = RuntimeProfiler(ROOT)

        for key in ("Up", "Down", "Left", "Right", "z", "Z", "x", "X", "c", "C", "Return"):
            self.root.bind(f"<KeyPress-{key}>", self._key_down)
            self.root.bind(f"<KeyRelease-{key}>", self._key_up)
        self.root.bind("<Escape>", lambda _e: self.close())
        self.root.focus_force()
        self._start_realtime_audio()

    def _key_down(self, event) -> None:
        self._pressed.add(event.keysym.lower())

    def _key_up(self, event) -> None:
        self._pressed.discard(event.keysym.lower())

    def _pad_bits(self) -> int:
        k = self._pressed
        bits = 0
        if "up" in k: bits |= PAD_UP
        if "down" in k: bits |= PAD_DOWN
        if "left" in k: bits |= PAD_LEFT
        if "right" in k: bits |= PAD_RIGHT
        if "z" in k: bits |= PAD_A
        if "x" in k: bits |= PAD_B
        if "c" in k: bits |= PAD_C
        if "return" in k: bits |= PAD_START
        return bits

    def _start_realtime_audio(self) -> None:
        runtime = ROOT / "build" / "dms1_rt_audio.exe"
        if os.name == "nt" and runtime.exists() and self.machine.audio_rom:
            try:
                self.rt_client = RealtimeAudioClient(runtime, self.sample_bus_path, ROOT)
                self._set_audio_status("AUDIO: REALTIME RUNTIME STARTING | Z80 -> DMS-1 HARDWARE")
                return
            except Exception as exc:
                self.rt_failed = True
                self.audio_error = str(exc)
                print(f"[AUDIO] realtime runtime unavailable: {exc}", flush=True)
        self._set_audio_status("AUDIO: FALLBACK PREPARING | WAV DEBUG PATH")
        self._start_audio_prepare()

    def _poll_realtime_status(self) -> None:
        if not self.rt_client:
            return
        for status in self.rt_client.poll_status():
            if status == "READY":
                self.audio_ready = True
                self._set_audio_status("AUDIO: READY | REALTIME | Z80 NATIVE -> DMS-1 HARDWARE")
            elif status == "PRIMING":
                self._set_audio_status("AUDIO: PRIMING LIVE BUFFER | Z80 NATIVE")
            elif status == "PLAYING":
                self._set_audio_status("AUDIO: PLAY | REALTIME | Z80 NATIVE | DMS-1 HARDWARE")
            elif status == "BUFFERING":
                self._set_audio_status("AUDIO: BUFFERING LIVE Z80 DATA...")
            elif status == "STOPPED":
                self._set_audio_status("AUDIO: STOP | REALTIME READY | Z=PLAY")
            elif status.startswith("ERROR"):
                self.rt_failed = True
                self.audio_error = status
                self._set_audio_status("AUDIO REALTIME ERROR | FALLBACK AVAILABLE")

    def _feed_realtime_audio(self) -> None:
        if not self.rt_client or not self.machine.audio_running:
            self._poll_realtime_status()
            return
        events = self.machine.native_audio_events
        if len(events) < self.rt_sent_events:
            self.rt_client.reset_stream()
            self.rt_sent_events = 0
            self.rt_sent_until = 0
        new_events = events[self.rt_sent_events:]
        fed_until = max(0, self.machine.z80_master_cycle - self.machine.z80_song_start_master)
        # Do not flood the subprocess pipe with identical F markers from the 2 ms
        # UI timer. We only transmit when the emulated Z80 timeline advanced.
        if not new_events and fed_until <= self.rt_sent_until:
            self._poll_realtime_status()
            return
        try:
            self.rt_client.feed(new_events, fed_until)
            self.rt_sent_events = len(events)
            self.rt_sent_until = fed_until
        except Exception as exc:
            self.rt_failed = True
            self.audio_error = str(exc)
            self._set_audio_status("AUDIO REALTIME ERROR: " + str(exc)[:80])
        self._poll_realtime_status()

    def _emulator_path(self) -> Path:
        exe = ROOT / "build" / "dms1emu.exe"
        if exe.exists():
            return exe
        return ROOT / "build" / "dms1emu"

    def _render_native_audio(self) -> Path:
        if not self.machine.audio_rom:
            raise RuntimeError("cartouche sans DMR0 audio")
        emulator = self._emulator_path()
        if not emulator.exists():
            raise RuntimeError(f"moteur dms1emu introuvable: {emulator}")
        temp = Path(self.temp.name)
        dmr = temp / "p081.dmr"
        ztr = temp / "p081.ztr"
        wav = temp / "p081-hardware-z80.wav"
        dmr.write_bytes(self.machine.audio_rom)
        native = compile_and_run(self.machine.audio_rom)
        write_ztr1(ztr, native)
        analysis = analyze_dmr(dmr)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run([
            str(emulator), str(dmr), str(wav),
            "--output-stage", "hardware",
            "--max-seconds", str(render_limit_seconds(analysis)),
            "--native-trace", str(ztr),
        ], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
           creationflags=flags, check=False)
        if result.returncode:
            detail = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(detail or f"dms1emu code {result.returncode}")
        if not wav.exists() or wav.stat().st_size <= 44:
            raise RuntimeError("rendu WAV natif vide")
        return wav

    def _audio_cache_path(self) -> Path:
        cache_dir = ROOT / "build" / "audio_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Internal cache identifier only; no checksum sidecar is created.
        crc = zlib.crc32(self.machine.audio_rom) & 0xFFFFFFFF
        return cache_dir / f"dms1_p10_fallback_{crc:08x}_z80_hardware.wav"

    def _set_audio_status(self, text: str) -> None:
        self.audio_status_var.set(text)
        if hasattr(self, "profiler"):
            self.profiler.note_audio_status(text)
        if text != self._last_console_audio_status:
            print("[AUDIO] " + text, flush=True)
            self._last_console_audio_status = text

    def _start_audio_prepare(self) -> None:
        if self.audio_prepare_started:
            return
        self.audio_prepare_started = True
        cache = self._audio_cache_path()
        if cache.exists() and cache.stat().st_size > 44:
            self.audio_prepare_queue.put(("ready", cache))
            self._set_audio_status("AUDIO: READY (CACHE) | Z=PLAY | Z80 NATIVE -> DMS-1 HARDWARE")
            return

        self._set_audio_status("AUDIO: PREPARING | Z80 NATIVE -> DMS-1 HARDWARE")

        def worker() -> None:
            try:
                wav = self._render_native_audio()
                cache_tmp = cache.with_suffix(".tmp.wav")
                shutil.copyfile(wav, cache_tmp)
                cache_tmp.replace(cache)
                self.audio_prepare_queue.put(("ready", cache))
            except Exception as exc:
                self.audio_prepare_queue.put(("error", str(exc)))

        threading.Thread(target=worker, name="dms1-p081-audio", daemon=True).start()

    def _poll_audio_prepare(self) -> None:
        while True:
            try:
                kind, payload = self.audio_prepare_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "ready":
                self.cached_wav = Path(payload)
                self.audio_ready = True
                self.audio_error = None
                self._set_audio_status("AUDIO: READY | Z=PLAY | Z80 NATIVE -> DMS-1 HARDWARE")
                if self.pending_play and self.machine.audio_running:
                    self._start_mci_playback()
            else:
                self.audio_ready = False
                self.audio_error = str(payload)
                self.pending_play = False
                self._set_audio_status("AUDIO ERROR: " + self.audio_error[:100])

    def _start_mci_playback(self) -> None:
        if not self.cached_wav or not self.cached_wav.exists():
            self.pending_play = True
            self._set_audio_status("AUDIO: PLAY QUEUED - PREPARING NATIVE RENDER...")
            return
        try:
            self.audio.open(self.cached_wav)
            self.audio.play()
            self.pending_play = False
            self.mci_playing = True
            try:
                mci_mode = self.audio.mode().upper()
            except Exception:
                mci_mode = "PLAY"
            self._set_audio_status(f"AUDIO: {mci_mode} | Z80 NATIVE | DMS-1 HARDWARE")
        except Exception as exc:
            self.mci_playing = False
            self.audio_error = str(exc)
            self._set_audio_status("AUDIO ERROR: " + self.audio_error[:100])
            print(f"P1.0.1 fallback audio: {exc}", flush=True)

    def _audio_actions(self) -> None:
        for action in self.machine.pop_audio_actions():
            if action.kind == "play":
                if self.rt_client and not self.rt_failed:
                    try:
                        self.rt_sent_events = 0
                        self.rt_sent_until = 0
                        self.rt_client.play()
                        self.rt_active = True
                        self._set_audio_status("AUDIO: PRIMING 90ms LIVE Z80 BUFFER...")
                        # Do not fast-forward the whole console here. The C++ audio
                        # runtime waits for a short live Z80 lead before opening
                        # waveOut, while the 60 Hz scheduler continues in real time.
                    except Exception as exc:
                        self.rt_failed = True
                        self.audio_error = str(exc)
                        self._set_audio_status("AUDIO REALTIME ERROR: " + str(exc)[:80])
                elif self.audio_ready:
                    self._start_mci_playback()
                elif self.audio_error:
                    self._set_audio_status("AUDIO ERROR: " + self.audio_error[:100])
                else:
                    self.pending_play = True
                    self._set_audio_status("AUDIO: FALLBACK PLAY QUEUED...")
            elif action.kind == "stop":
                self.pending_play = False
                if self.rt_client and not self.rt_failed:
                    try:
                        self.rt_client.stop()
                    except Exception:
                        pass
                    self.rt_active = False
                    self.rt_sent_events = 0
                    self.rt_sent_until = 0
                try:
                    self.audio.stop()
                except Exception:
                    pass
                self.mci_playing = False
                if self.rt_client and not self.rt_failed:
                    self._set_audio_status("AUDIO: STOP | REALTIME READY | Z=PLAY")
                elif self.audio_ready:
                    self._set_audio_status("AUDIO: STOP | FALLBACK READY | Z=PLAY")

    def _update_audio_hud(self) -> None:
        events = self.machine.native_audio_events
        if len(events) < self._hud_counted_events:
            self._hud_counted_events = 0
            self._hud_opz = self._hud_ssg = self._hud_adpa = self._hud_adpb = 0
        # Count only newly generated writes. P1.0 rescanned the entire song every
        # UI tick, which became increasingly expensive as Marble approached 215k writes.
        for _cycle, address, _value in events[self._hud_counted_events:]:
            if 0x0000 <= address <= 0x00FF:
                self._hud_opz += 1
            elif 0x0100 <= address <= 0x010F:
                self._hud_ssg += 1
            elif 0x0120 <= address <= 0x012F:
                self._hud_adpa += 1
            elif 0x0140 <= address <= 0x015F:
                self._hud_adpb += 1
        self._hud_counted_events = len(events)
        self.audio_writes_var.set(
            f"WRITES {len(events):06d} | OPZ {self._hud_opz:06d} | SSG {self._hud_ssg:06d} | "
            f"A {self._hud_adpa:06d} | B {self._hud_adpb:06d}"
        )

    def _redraw(self) -> None:
        t0 = time.perf_counter()
        # P1.0.5 Windows fast path: render + present directly through GDI into the
        # Tk viewport HWND. This removes the per-frame PNG/base64/PhotoImage/zoom
        # lifecycle that could stall Tcl/Tk after a few seconds of 60 Hz updates.
        if self.native_vdp is not None and self.native_vdp.has_win32_presenter:
            try:
                self.native_vdp.present_win32(
                    self.viewport.winfo_id(), self.machine.vdp,
                    DISPLAY_WIDTH, DISPLAY_HEIGHT,
                )
                self._render_ms = (time.perf_counter() - t0) * 1000.0
                self._displayed_frames += 1
                return
            except Exception as exc:
                print(f"[VIDEO] Win32 presenter failed, image fallback: {exc}", flush=True)
                # Keep native raster if possible; only disable direct presentation.
                self.native_vdp.present_fn = None
                self.video_backend = "C++ IMAGE FALLBACK"

        if self.native_vdp is not None:
            try:
                width, rgb = self.native_vdp.render(self.machine.vdp)
            except Exception as exc:
                print(f"[VIDEO] native renderer failed, Python fallback: {exc}", flush=True)
                self.native_vdp = None
                self.video_backend = "PYTHON FALLBACK"
                rgb = self.machine.render_video()
                width = self.machine.vdp.active_width
        else:
            rgb = self.machine.render_video()
            width = self.machine.vdp.active_width
        encoded = base64.b64encode(rgb_png(width, HEIGHT, rgb))
        photo = self.tk.PhotoImage(data=encoded, format="PNG")
        if width == 256:
            shown = photo.zoom(LOW_RES_ZOOM_NUM, LOW_RES_ZOOM_NUM).subsample(
                LOW_RES_ZOOM_DEN, LOW_RES_ZOOM_DEN
            )
        else:
            shown = photo.zoom(SCALE, SCALE)
        self._photo = shown
        self.viewport.itemconfigure(self._screen_item, image=self._photo)
        self._render_ms = (time.perf_counter() - t0) * 1000.0
        self._displayed_frames += 1

    def _advance_console_clock(self) -> int:
        """Advance DMS-1 near 60 Hz without entering a catch-up death spiral.

        P1.0.4 proved that the VDP raster itself is cheap (~1 ms) while delayed
        Tk callbacks could accumulate 10-15 due frames. Executing all of them in
        one callback created 150-300 ms stalls, which generated still more debt.
        P1.0.5 limits each host turn to two emulated frames and discards only
        excessive *wall-clock debt* after pathological host stalls. Normal 60 Hz
        operation remains one frame per VBlank.
        """
        now = time.perf_counter()
        elapsed = now - self._sim_last_wall
        self._sim_last_wall = now
        elapsed = max(0.0, min(elapsed, 0.10))
        self._sim_frame_accum += elapsed * self.machine.FPS
        raw_due = int(self._sim_frame_accum)
        if raw_due <= 0:
            return 0
        due = min(raw_due, self._max_catchup_frames)
        pad = self._pad_bits()
        for _ in range(due):
            self.machine.set_pad(pad)
            self.machine.step_frame()
        self._sim_frame_accum -= due
        if raw_due > self._max_catchup_frames:
            # A 200-300 ms OS/UI stall must not become a self-sustaining series
            # of 10-15-frame catch-up bursts. Resynchronise host wall time after
            # doing two legitimate frames; this is a host safety policy only.
            self._sim_frame_accum = min(self._sim_frame_accum, 0.999)
        return due

    def sim_tick(self) -> None:
        if self.closed:
            return
        t0 = time.perf_counter()
        due = self._advance_console_clock()
        if self.rt_client is None:
            self._poll_audio_prepare()
        self._audio_actions()
        self._feed_realtime_audio()
        self.profiler.note_sim(
            int(self.machine.vdp.mode), due, (time.perf_counter() - t0) * 1000.0
        )
        # No Tk label/title updates here. This loop exists for the console/audio
        # scheduler and must stay cheap and predictable.
        self.root.after(1, self.sim_tick)

    def _refresh_video_status(self) -> None:
        state = self.machine.debug_state()
        profile = MODE_PROFILES[state["video_mode"]]
        planes = "BG A+B" if profile.bg_b_base is not None else "BG A"
        colours = profile.palettes * 16
        extra = " | LINE SCROLL" if profile.line_scroll else ""
        presentation = " | LETTERBOX" if profile.width == 256 else ""
        self.video_status_var.set(
            f"VIDEO: MODE {profile.mode} {profile.name} | {profile.width}x224 | {colours}C/512 | "
            f"{planes}{extra} | SPR {profile.sprite_total}/{profile.sprite_per_scanline}{presentation} | "
            f"HOST {self._display_fps:04.1f} FPS | {self.video_backend} {self._render_ms:04.1f} ms"
        )
        # Updating the OS window caption every frame caused avoidable Windows/Tk
        # work and visible jitter. It now changes only when the video mode does.
        if profile.mode != self._last_video_mode:
            self._last_video_mode = profile.mode
            self.root.title(
                f"DMS-1 P1.0.5 | MODE {profile.mode} {profile.name} | C=NEXT MODE | D-PAD=SPRITE | "
                f"Z=PLAY X=STOP"
            )

    def hud_tick(self) -> None:
        if self.closed:
            return
        self._update_audio_hud()
        now = time.perf_counter()
        span = now - self._fps_window_start
        if span >= 0.75:
            self._display_fps = self._displayed_frames / span
            self._displayed_frames = 0
            self._fps_window_start = now
        self._refresh_video_status()
        audio_stats = self.rt_client.diagnostic_stats() if self.rt_client else None
        self.profiler.sample(
            machine=self.machine, display_fps=self._display_fps,
            sim_accum=self._sim_frame_accum, audio_status=self.audio_status_var.get(),
            audio_stats=audio_stats,
        )
        # 10 Hz HUD + profiler snapshot.
        self.root.after(100, self.hud_tick)

    def video_tick(self) -> None:
        if self.closed:
            return
        frame = self.machine.frame_counter
        # If the host misses a presentation deadline, display the newest complete
        # DMS-1 frame once. Never replay stale frames just to "catch up" visually.
        if frame != self._last_render_frame:
            delta = 1 if self._last_render_frame < 0 else max(1, frame - self._last_render_frame)
            self._redraw()
            self.profiler.note_present(int(self.machine.vdp.mode), delta, self._render_ms)
            self._last_render_frame = frame
        self.root.after(1, self.video_tick)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.rt_client:
            try:
                self.rt_client.close()
            except Exception:
                pass
        try:
            self.audio.close()
        except Exception:
            pass
        try:
            self.profiler.close()
            print(f"[DIAG] report: {self.profiler.last_txt}", flush=True)
            print(f"[DIAG] csv   : {self.profiler.last_csv}", flush=True)
        except Exception as exc:
            print(f"[DIAG] unable to finalize report: {exc}", flush=True)
        if self._timer_period_active and os.name == "nt":
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
        self.temp.cleanup()
        self.root.destroy()

    def run(self) -> None:
        self.root.after(1, self.sim_tick)
        self.root.after(1, self.video_tick)
        self.root.after(50, self.hud_tick)
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", nargs="?", type=Path, default=DEFAULT_ROM)
    args = parser.parse_args()
    VdpDemoPlayer(args.rom).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
