#!/usr/bin/env python3
"""DMS-1 P1.0.9 - final runtime lock console orchestrator.

No Tk/Tcl is imported. The Python process advances the frozen DMS-1 machine at
60 Hz, streams only merged VDP memory deltas after the first frame, and feeds
the validated Z80 -> RealtimeCore audio bridge asynchronously. Profiler disk
I/O and native-host telemetry are both isolated from the realtime scheduler.
"""
from __future__ import annotations

import argparse
import ctypes
import gc
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import zlib
from pathlib import Path

from dms1_player import MciWave, analyze_dmr, render_limit_seconds
from dms_console90_machine import Console90Machine
from dms_console90_vdp import MODE_PROFILES
from dms_realtime_audio import RealtimeAudioClient
from dms_z80_native import compile_and_run, write_ztr1
from dms_native_host import NativeHostClient
from dms_native_runtime_profiler import NativeRuntimeProfiler
from dms_debugger import DmsDebugger

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROM = ROOT / "roms" / "dms1_gdk_system_demo.dmc"


class NativeConsolePlayer:
    def __init__(self, rom_path: Path) -> None:
        if os.name != "nt":
            raise RuntimeError("P1.0.9 FINAL RUNTIME LOCK requires Windows")
        self.machine = Console90Machine.from_path(rom_path)
        host_exe = ROOT / "build" / "dms1_native_host.exe"
        self.host = NativeHostClient(host_exe, ROOT)
        self.profiler = NativeRuntimeProfiler(ROOT)
        self.debugger = DmsDebugger(self.machine, rom_path, ROOT)
        # Debug-key safety net: some canonical archives contained a native host
        # executable older than the Python debugger source.  Poll F1..F10 from
        # Win32 as an edge-triggered fallback, but only while the game host owns
        # the foreground window.  A newer native host may also emit KEY lines;
        # _debug_key_down prevents a held key from being applied repeatedly.
        self._debug_key_down: dict[int, bool] = {}
        self._debug_last_fire: dict[str, float] = {}
        self._debug_vk = {0x70 + i: f"F{i + 1}" for i in range(10)}
        self.closed = False
        self._timer_period = False
        try:
            if ctypes.windll.winmm.timeBeginPeriod(1) == 0:
                self._timer_period = True
                print("[HOST] Python scheduler timer resolution: 1 ms", flush=True)
        except Exception as exc:
            print(f"[HOST] timeBeginPeriod unavailable: {exc}", flush=True)

        # Audio: same P1.0.5 real-time path. WAV remains an explicit emergency
        # fallback only; video no longer depends on any audio subprocess pipe.
        self.audio = MciWave()
        self.temp = tempfile.TemporaryDirectory(prefix="dms1_p109_")
        self.sample_bus_path = Path(self.temp.name) / "p109-live-sample-bus.dmr"
        self.sample_bus_path.write_bytes(self.machine.audio_sample_bus)
        self.rt_client: RealtimeAudioClient | None = None
        self.rt_sent_events = 0
        self.rt_sent_until = 0
        self.rt_failed = False
        self.audio_ready = False
        self.audio_error: str | None = None
        self.audio_status = "AUDIO: STARTING"
        self._last_console_audio_status: str | None = None
        self.cached_wav: Path | None = None
        self.pending_play = False
        self.mci_playing = False
        self.audio_prepare_started = False
        self.audio_prepare_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self._hud_counted_events = 0
        self._hud_opz = self._hud_ssg = self._hud_adpa = self._hud_adpb = 0
        self._scheduler_resyncs = 0
        self._start_realtime_audio()
        self._send_hud()
        # Give the native HWND a complete retained first frame before the clock starts.
        self.host.send_frame(self.machine.vdp, self.machine.frame_counter)

    # ---------------- audio ----------------
    def _set_audio_status(self, text: str) -> None:
        self.audio_status = text
        self.profiler.note_audio_status(text)
        if text != self._last_console_audio_status:
            print("[AUDIO] " + text, flush=True)
            self._last_console_audio_status = text

    def _start_realtime_audio(self) -> None:
        runtime = ROOT / "build" / "dms1_rt_audio.exe"
        if runtime.exists() and self.machine.audio_rom:
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

    def _audio_actions(self) -> None:
        for action in self.machine.pop_audio_actions():
            if action.kind == "play":
                if self.rt_client and not self.rt_failed:
                    try:
                        self.rt_sent_events = 0
                        self.rt_sent_until = 0
                        self.rt_client.play()
                        self._set_audio_status("AUDIO: PRIMING 90ms LIVE Z80 BUFFER...")
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
                    try: self.rt_client.stop()
                    except Exception: pass
                    self.rt_sent_events = 0
                    self.rt_sent_until = 0
                try: self.audio.stop()
                except Exception: pass
                self.mci_playing = False
                if self.rt_client and not self.rt_failed:
                    self._set_audio_status("AUDIO: STOP | REALTIME READY | Z=PLAY")
                elif self.audio_ready:
                    self._set_audio_status("AUDIO: STOP | FALLBACK READY | Z=PLAY")

    def _emulator_path(self) -> Path:
        exe = ROOT / "build" / "dms1emu.exe"
        return exe if exe.exists() else ROOT / "build" / "dms1emu"

    def _render_native_audio(self) -> Path:
        if not self.machine.audio_rom:
            raise RuntimeError("cartouche sans DMR0 audio")
        emulator = self._emulator_path()
        if not emulator.exists():
            raise RuntimeError(f"moteur dms1emu introuvable: {emulator}")
        temp = Path(self.temp.name)
        dmr = temp / "p109.dmr"; ztr = temp / "p109.ztr"; wav = temp / "p109-hardware-z80.wav"
        dmr.write_bytes(self.machine.audio_rom)
        write_ztr1(ztr, compile_and_run(self.machine.audio_rom))
        analysis = analyze_dmr(dmr)
        flags = subprocess.CREATE_NO_WINDOW
        result = subprocess.run([
            str(emulator), str(dmr), str(wav), "--output-stage", "hardware",
            "--max-seconds", str(render_limit_seconds(analysis)), "--native-trace", str(ztr),
        ], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
           creationflags=flags, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace").strip() or f"dms1emu code {result.returncode}")
        if not wav.exists() or wav.stat().st_size <= 44:
            raise RuntimeError("rendu WAV natif vide")
        return wav

    def _audio_cache_path(self) -> Path:
        cache_dir = ROOT / "build" / "audio_cache"; cache_dir.mkdir(parents=True, exist_ok=True)
        crc = zlib.crc32(self.machine.audio_rom) & 0xFFFFFFFF
        return cache_dir / f"dms1_p10_fallback_{crc:08x}_z80_hardware.wav"

    def _start_audio_prepare(self) -> None:
        if self.audio_prepare_started: return
        self.audio_prepare_started = True
        cache = self._audio_cache_path()
        if cache.exists() and cache.stat().st_size > 44:
            self.audio_prepare_queue.put(("ready", cache)); return
        def worker() -> None:
            try:
                wav = self._render_native_audio()
                tmp = cache.with_suffix(".tmp.wav")
                shutil.copyfile(wav, tmp); tmp.replace(cache)
                self.audio_prepare_queue.put(("ready", cache))
            except Exception as exc:
                self.audio_prepare_queue.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="dms1-p108-fallback-audio").start()

    def _poll_audio_prepare(self) -> None:
        while True:
            try: kind, payload = self.audio_prepare_queue.get_nowait()
            except queue.Empty: break
            if kind == "ready":
                self.cached_wav = Path(payload); self.audio_ready = True; self.audio_error = None
                self._set_audio_status("AUDIO: READY | FALLBACK CACHE | Z=PLAY")
                if self.pending_play and self.machine.audio_running: self._start_mci_playback()
            else:
                self.audio_error = str(payload); self.pending_play = False
                self._set_audio_status("AUDIO ERROR: " + self.audio_error[:100])

    def _start_mci_playback(self) -> None:
        if not self.cached_wav or not self.cached_wav.exists():
            self.pending_play = True; return
        try:
            self.audio.open(self.cached_wav); self.audio.play(); self.pending_play = False; self.mci_playing = True
            self._set_audio_status("AUDIO: PLAY | FALLBACK WAV DEBUG PATH")
        except Exception as exc:
            self.audio_error = str(exc); self._set_audio_status("AUDIO ERROR: " + self.audio_error[:100])

    # ---------------- HUD / host ----------------
    def _audio_counts(self) -> str:
        events = self.machine.native_audio_events
        if len(events) < self._hud_counted_events:
            self._hud_counted_events = 0
            self._hud_opz = self._hud_ssg = self._hud_adpa = self._hud_adpb = 0
        for _cycle, address, _value in events[self._hud_counted_events:]:
            if 0x0000 <= address <= 0x00FF: self._hud_opz += 1
            elif 0x0100 <= address <= 0x010F: self._hud_ssg += 1
            elif 0x0120 <= address <= 0x012F: self._hud_adpa += 1
            elif 0x0140 <= address <= 0x015F: self._hud_adpb += 1
        self._hud_counted_events = len(events)
        return (f"WRITES {len(events):06d} | OPZ {self._hud_opz:06d} | SSG {self._hud_ssg:06d} | "
                f"A {self._hud_adpa:06d} | B {self._hud_adpb:06d}")

    def _send_hud(self) -> None:
        hs = self.host.diagnostic_stats()
        astats = self.rt_client.diagnostic_stats() if self.rt_client else {}
        self.debugger.set_runtime_context(host_stats=hs, audio_stats=astats, audio_status=self.audio_status)
        self.host.send_hud(self.debugger.hud_text())

    def _handle_debug_key(self, key: str) -> bool:
        """Apply one debugger command and refresh HUD; return True if handled.

        Native-host KEY events and the Win32 fallback may see the same physical
        press.  A short debounce makes the two paths coexist without toggling a
        command twice.
        """
        now = time.perf_counter()
        if now - self._debug_last_fire.get(key, -10.0) < 0.12:
            return False
        self._debug_last_fire[key] = now
        action = self.debugger.handle_key(key)
        if action == "capture":
            self.debugger.set_runtime_context(
                host_stats=self.host.diagnostic_stats(),
                audio_stats=self.rt_client.diagnostic_stats() if self.rt_client else {},
                audio_status=self.audio_status,
            )
            try:
                cap = self.debugger.capture(reason="MANUAL")
                print(f"[DEBUG] capture: {cap}", flush=True)
            except Exception as exc:
                print(f"[DEBUG] capture impossible: {exc}", flush=True)
        self._send_hud()
        print(f"[DEBUG] key {key} -> view={self.debugger.view} visible={int(self.debugger.visible)} paused={int(self.debugger.paused)}", flush=True)
        return True

    def _poll_debug_keys_win32(self) -> None:
        """Fallback input path for stale native-host executables.

        GetAsyncKeyState is intentionally gated to the native host process being
        foreground, so F-keys pressed in another application cannot alter the
        running DMS debugger.
        """
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return
            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) != int(self.host.proc.pid):
                # Clear edge state when focus leaves the game, otherwise a key
                # released outside the window could look permanently held.
                for vk in self._debug_vk:
                    self._debug_key_down[vk] = False
                return
            for vk, key in self._debug_vk.items():
                down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                was = bool(self._debug_key_down.get(vk, False))
                if down and not was:
                    self._handle_debug_key(key)
                self._debug_key_down[vk] = down
        except Exception:
            # Native-host KEY events remain the primary path when available.
            return

    def _poll_host(self) -> None:
        changed = False
        for line in self.host.poll_lines():
            if line.startswith("ERROR"):
                print("[NATIVE HOST] " + line, flush=True)
            elif line.startswith("FREEZE"):
                print("[NATIVE HOST] " + line + " (CPU/Z80/audio continue)", flush=True)
            elif line.startswith("KEY "):
                key = line.split(None, 1)[1].strip().upper()
                changed = self._handle_debug_key(key) or changed
        # Always poll the Win32 fallback.  On a current host this is harmless
        # because held-key edge state suppresses repeats; on the stale P1.0.9
        # host it restores F1..F10 completely.
        self._poll_debug_keys_win32()

    def _step_machine_frame(self, lateness_ms: float = 0.0) -> None:
        t0 = time.perf_counter()
        self.machine.set_pad(self.host.pad_bits)
        self.machine.step_frame()
        self.debugger.after_frame()
        self._audio_actions()
        if self.rt_client is None:
            self._poll_audio_prepare()
        self._feed_realtime_audio()
        self.host.send_frame(self.machine.vdp, self.machine.frame_counter)
        sim_ms = (time.perf_counter() - t0) * 1000.0
        self.profiler.note_sim(sim_ms, lateness_ms, False)

    # ---------------- clock ----------------
    def run(self) -> None:
        period = 1.0 / self.machine.FPS
        next_frame = time.perf_counter() + period
        next_hud = time.perf_counter() + 0.20
        next_profile = time.perf_counter() + 0.50
        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.collect(); gc.disable()
        try:
            try:
                kernel32 = ctypes.windll.kernel32
                kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00008000)
            except Exception:
                pass
            print("[HOST] DMS-1 DEBUGGER V0.1 - hardware budgets observed, not increased", flush=True)
            while not self.closed and not self.host.close_requested:
                # Host commands are read before advancing the next DMS frame, so
                # PAUSE/CAPTURE are deterministic from the developer's viewpoint.
                self._poll_host()
                now = time.perf_counter()

                if self.debugger.paused:
                    next_frame = now + period
                    if self.debugger.consume_step():
                        self._step_machine_frame(0.0)
                        next_frame = time.perf_counter() + period
                else:
                    frames_this_turn = 0
                    while now >= next_frame and frames_this_turn < 2:
                        lateness_ms = max(0.0, (now - next_frame) * 1000.0)
                        self._step_machine_frame(lateness_ms)
                        next_frame += period
                        frames_this_turn += 1
                        now = time.perf_counter()

                    if now - next_frame > period * 2:
                        stall_ms = max(0.0, (now - next_frame) * 1000.0)
                        next_frame = now + period
                        self._scheduler_resyncs += 1
                        self.profiler.note_sim(0.0, stall_ms, True)

                self._poll_realtime_status()
                if self.rt_client is None:
                    self._poll_audio_prepare()

                now = time.perf_counter()
                if now >= next_hud:
                    self._send_hud()
                    next_hud = now + 0.20
                if now >= next_profile:
                    self.profiler.sample(
                        machine=self.machine, audio_status=self.audio_status,
                        audio_stats=self.rt_client.diagnostic_stats() if self.rt_client else None,
                        host_stats=self.host.diagnostic_stats(),
                    )
                    next_profile = now + 0.50

                remain = next_frame - time.perf_counter()
                if self.debugger.paused:
                    time.sleep(0.001)
                elif remain > 0.0012:
                    time.sleep(max(0.0005, remain - 0.00045))
                elif remain > 0:
                    while time.perf_counter() < next_frame:
                        pass
        except Exception as exc:
            try:
                self.debugger.set_runtime_context(
                    host_stats=self.host.diagnostic_stats(),
                    audio_stats=self.rt_client.diagnostic_stats() if self.rt_client else {},
                    audio_status=self.audio_status,
                )
                report = self.debugger.crash_report(exc)
                print(f"[CRASH] report: {report}", flush=True)
            except Exception as cap_exc:
                print(f"[CRASH] capture failed: {cap_exc}", flush=True)
            raise
        finally:
            self.close()
            if gc_was_enabled and not gc.isenabled():
                gc.enable()

    def close(self) -> None:
        if self.closed: return
        self.closed = True
        host_stats = self.host.diagnostic_stats()
        if self.rt_client:
            try: self.rt_client.close()
            except Exception: pass
        try: self.audio.close()
        except Exception: pass
        try:
            self.profiler.close(host_stats=host_stats)
            print(f"[DIAG] report: {self.profiler.last_txt}", flush=True)
            print(f"[DIAG] csv   : {self.profiler.last_csv}", flush=True)
        except Exception as exc:
            print(f"[DIAG] unable to finalize report: {exc}", flush=True)
        try: self.host.close()
        except Exception: pass
        try: self.temp.cleanup()
        except Exception: pass
        if self._timer_period:
            try: ctypes.windll.winmm.timeEndPeriod(1)
            except Exception: pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", nargs="?", type=Path, default=DEFAULT_ROM)
    args = parser.parse_args()
    NativeConsolePlayer(args.rom).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
