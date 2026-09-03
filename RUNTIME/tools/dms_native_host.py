#!/usr/bin/env python3
"""P1.0.8 bridge to the dedicated native Win32 DMS-1 host.

Realtime-isolation changes versus P1.0.7:
- the first frame is a complete VRAM/CRAM snapshot;
- later frames carry only dirty VRAM/CRAM ranges + current VDP registers;
- pending deltas are merged so latest-frame replacement cannot lose a static write;
- transport diagnostics never perform disk I/O.

The emulator process never calls Tk/GDI. The dedicated C++ host owns the
window, input and presentation.
"""
from __future__ import annotations

from collections import deque
import os
import queue
import struct
import subprocess
import threading
from pathlib import Path

MAGIC = b"DMSH"
PKT_FRAME_FULL = 1
PKT_FRAME = PKT_FRAME_FULL  # compatibility name used by older tests
PKT_HUD = 2
PKT_QUIT = 3
PKT_FRAME_DELTA = 4
PKT_DISPLAY_PROFILE = 5  # optional; ignored safely by pre-V1.1 native hosts
HEADER = struct.Struct("<4sII")
# frame, mode, backdrop, scroll A x/y, scroll B x/y, arg0, arg1
# FULL: arg0=vram_size, arg1=cram_size
# DELTA: arg0=vram_range_count, arg1=cram_range_count
FRAME_META = struct.Struct("<Q6iII")
RANGE_META = struct.Struct("<II")
VRAM_SIZE = 0x20000
CRAM_SIZE = 0x100
FULL_THRESHOLD_BYTES = 32 * 1024
FULL_THRESHOLD_RANGES = 256


def _coalesce_offsets(offsets: set[int]) -> list[tuple[int, int]]:
    if not offsets:
        return []
    ordered = sorted(offsets)
    out: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for off in ordered[1:]:
        if off == prev + 1:
            prev = off
            continue
        out.append((start, prev + 1))
        start = prev = off
    out.append((start, prev + 1))
    return out


def _expand_ranges(ranges: list[tuple[int, int]], limit: int) -> set[int]:
    out: set[int] = set()
    for start, end in ranges:
        start = max(0, int(start)); end = min(limit, int(end))
        if end > start:
            out.update(range(start, end))
    return out


class NativeHostClient:
    def __init__(self, executable: Path, cwd: Path) -> None:
        if os.name != "nt":
            raise RuntimeError("DMS-1 native host is Windows-only")
        if not executable.exists():
            raise FileNotFoundError(executable)
        flags = subprocess.CREATE_NO_WINDOW
        child_env = os.environ.copy()
        # MinGW side DLLs, if any, must be resolved from the executable folder
        # rather than from a random system-wide MSYS2 installation.
        child_env["PATH"] = str(executable.parent) + os.pathsep + child_env.get("PATH", "")
        self.proc = subprocess.Popen(
            [str(executable)], cwd=cwd, env=child_env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, creationflags=flags,
        )
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("unable to open native host pipes")

        self.pad_bits = 0
        self.close_requested = False
        self.freeze = False
        self.ready = threading.Event()
        self.errors: queue.Queue[str] = queue.Queue()
        self.lines: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._event = threading.Event()
        # tuple(packet, full, included_vram_offsets, included_cram_offsets)
        self._latest_frame: tuple[bytes, bool, set[int], set[int]] | None = None
        self._latest_hud: bytes | None = None
        self._controls: deque[bytes | None] = deque()
        self._closed = False
        self._writer_error: str | None = None
        self._full_committed = False
        self._last_display_profile: int | None = None
        self._unsent_vram: set[int] = set()
        self._unsent_cram: set[int] = set()
        self.frame_packets_queued = 0
        self.frame_packets_overwritten = 0
        self.frame_packets_sent = 0
        self.full_packets_sent = 0
        self.delta_packets_sent = 0
        self.transport_bytes_sent = 0
        self.last_packet_bytes = 0
        self.max_packet_bytes = 0
        self.stats: dict[str, float | int] = {
            "fps": 0.0, "render_fps": 0.0, "rx_fps": 0.0, "received": 0, "rendered": 0,
            "presented": 0, "overwritten": 0, "drop_delta": 0, "paints": 0,
            "render_ms": 0.0, "frame": 0, "freeze": 0,
        }

        threading.Thread(target=self._reader, daemon=True, name="dms1-native-host-out").start()
        threading.Thread(target=self._stderr_reader, daemon=True, name="dms1-native-host-err").start()
        threading.Thread(target=self._writer, daemon=True, name="dms1-native-host-writer").start()
        if not self.ready.wait(timeout=5.0):
            detail = self._drain_errors()
            raise RuntimeError("native host did not become READY" + (": " + detail if detail else ""))

    @staticmethod
    def packet(packet_type: int, payload: bytes = b"") -> bytes:
        return HEADER.pack(MAGIC, int(packet_type), len(payload)) + payload

    @staticmethod
    def _meta(vdp, frame_counter: int, arg0: int, arg1: int) -> bytes:
        return FRAME_META.pack(
            int(frame_counter),
            int(vdp.mode), int(vdp.backdrop),
            int(vdp.scroll_a_x), int(vdp.scroll_a_y),
            int(vdp.scroll_b_x), int(vdp.scroll_b_y),
            int(arg0), int(arg1),
        )

    @classmethod
    def frame_payload(cls, vdp, frame_counter: int) -> bytes:
        """Compatibility/full-snapshot payload."""
        vram = bytes(vdp.vram)
        cram = bytes(vdp.cram)
        return cls._meta(vdp, frame_counter, len(vram), len(cram)) + vram + cram

    @classmethod
    def delta_payload(cls, vdp, frame_counter: int,
                      vram_offsets: set[int], cram_offsets: set[int]) -> bytes:
        vranges = _coalesce_offsets(vram_offsets)
        cranges = _coalesce_offsets(cram_offsets)
        parts = [cls._meta(vdp, frame_counter, len(vranges), len(cranges))]
        for start, end in vranges:
            data = bytes(vdp.vram[start:end])
            parts.append(RANGE_META.pack(start, len(data))); parts.append(data)
        for start, end in cranges:
            data = bytes(vdp.cram[start:end])
            parts.append(RANGE_META.pack(start, len(data))); parts.append(data)
        return b"".join(parts)

    def send_display_profile(self, profile: int) -> None:
        """Send host-only presentation preference without changing frame ABI.

        Older native hosts safely ignore packet type 5, so installing this add-on
        never makes the existing RAW runtime unusable if the host has not yet
        been rebuilt.
        """
        try:
            profile = int(profile)
        except Exception:
            profile = 0
        if profile < 0 or profile > 4:
            profile = 0
        if profile == self._last_display_profile:
            return
        payload = struct.pack("<I", profile)
        with self._lock:
            self._controls.append(self.packet(PKT_DISPLAY_PROFILE, payload))
            self._last_display_profile = profile
        self._event.set()

    def send_frame(self, vdp, frame_counter: int) -> None:
        self.send_display_profile(getattr(vdp, "presentation_profile", 0))
        # The VDP supplies exact write dirtiness. Merge it with writes belonging
        # to any latest-frame packet that has not yet been handed to the pipe.
        if hasattr(vdp, "consume_host_dirty"):
            vranges, cranges = vdp.consume_host_dirty()
            new_v = _expand_ranges(vranges, VRAM_SIZE)
            new_c = _expand_ranges(cranges, CRAM_SIZE)
        else:  # safety for an older VDP object
            new_v = set(range(VRAM_SIZE)); new_c = set(range(CRAM_SIZE))

        with self._lock:
            self._unsent_vram.update(new_v)
            self._unsent_cram.update(new_c)
            dirty_bytes = len(self._unsent_vram) + len(self._unsent_cram)
            v_range_count = len(_coalesce_offsets(self._unsent_vram)) if self._unsent_vram else 0
            c_range_count = len(_coalesce_offsets(self._unsent_cram)) if self._unsent_cram else 0
            need_full = (not self._full_committed or dirty_bytes >= FULL_THRESHOLD_BYTES or
                         (v_range_count + c_range_count) >= FULL_THRESHOLD_RANGES)
            included_v = set(self._unsent_vram)
            included_c = set(self._unsent_cram)
            if need_full:
                payload = self.frame_payload(vdp, frame_counter)
                packet = self.packet(PKT_FRAME_FULL, payload)
            else:
                payload = self.delta_payload(vdp, frame_counter, included_v, included_c)
                packet = self.packet(PKT_FRAME_DELTA, payload)
            if self._latest_frame is not None:
                self.frame_packets_overwritten += 1
            self._latest_frame = (packet, need_full, included_v, included_c)
            self.frame_packets_queued += 1
        self._event.set()

    def send_hud(self, text: str) -> None:
        payload = text.encode("utf-8", errors="replace")[:4096]
        with self._lock:
            self._latest_hud = self.packet(PKT_HUD, payload)
        self._event.set()

    def _writer(self) -> None:
        assert self.proc.stdin is not None
        try:
            while True:
                self._event.wait()
                while True:
                    full = False
                    is_frame = False
                    with self._lock:
                        if self._controls:
                            packet = self._controls.popleft()
                            included_v = included_c = set()
                        elif self._latest_frame is not None:
                            is_frame = True
                            packet, full, included_v, included_c = self._latest_frame
                            self._latest_frame = None
                            # From this point this packet is ordered before any newer
                            # packet. New writes to the same bytes are free to re-add.
                            self._unsent_vram.difference_update(included_v)
                            self._unsent_cram.difference_update(included_c)
                        elif self._latest_hud is not None:
                            packet = self._latest_hud
                            self._latest_hud = None
                            included_v = included_c = set()
                        else:
                            self._event.clear()
                            break
                    if packet is None:
                        return
                    if self.proc.poll() is not None:
                        raise RuntimeError(f"native host stopped ({self.proc.returncode})")
                    self.proc.stdin.write(packet)
                    n = len(packet)
                    self.transport_bytes_sent += n
                    self.last_packet_bytes = n
                    self.max_packet_bytes = max(self.max_packet_bytes, n)
                    if is_frame:
                        self.frame_packets_sent += 1
                        if full:
                            self.full_packets_sent += 1
                            self._full_committed = True
                        else:
                            self.delta_packets_sent += 1
        except Exception as exc:
            self._writer_error = str(exc)
            self.errors.put("writer: " + self._writer_error)
            self.close_requested = True

    def _reader(self) -> None:
        assert self.proc.stdout is not None
        while True:
            raw = self.proc.stdout.readline()
            if not raw:
                break
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            self.lines.put(text)
            if text == "READY":
                self.ready.set()
            elif text.startswith("PAD "):
                try: self.pad_bits = int(text.split()[1]) & 0xFF
                except Exception: pass
            elif text == "CLOSE":
                self.close_requested = True
            elif text.startswith("FREEZE "):
                self.freeze = text.endswith("1")
            elif text.startswith("ERROR"):
                self.errors.put(text)
            elif text.startswith("STAT "):
                self._parse_stat(text[5:])
        if not self._closed:
            self.close_requested = True

    def _stderr_reader(self) -> None:
        assert self.proc.stderr is not None
        while True:
            raw = self.proc.stderr.readline()
            if not raw: break
            text = raw.decode("utf-8", errors="replace").strip()
            if text: self.errors.put(text)

    def _parse_stat(self, text: str) -> None:
        values: dict[str, float | int] = {}
        for token in text.split():
            if "=" not in token: continue
            key, value = token.split("=", 1)
            try:
                if key in {"fps", "render_fps", "rx_fps", "render_ms", "gap_ms", "gap_max_ms"}:
                    values[key] = float(value)
                else:
                    values[key] = int(value)
            except ValueError:
                continue
        if values: self.stats.update(values)

    def poll_lines(self) -> list[str]:
        out: list[str] = []
        while True:
            try: out.append(self.lines.get_nowait())
            except queue.Empty: break
        while True:
            try: out.append("ERROR " + self.errors.get_nowait())
            except queue.Empty: break
        return out

    def _drain_errors(self) -> str:
        parts=[]
        while True:
            try: parts.append(self.errors.get_nowait())
            except queue.Empty: break
        return " | ".join(parts)

    def diagnostic_stats(self) -> dict[str, float | int | bool | str | None]:
        result = dict(self.stats)
        result.update({
            "process_alive": self.proc.poll() is None,
            "python_frame_queue_overwrites": self.frame_packets_overwritten,
            "python_frame_packets_queued": self.frame_packets_queued,
            "python_frame_packets_sent": self.frame_packets_sent,
            "transport_full_packets": self.full_packets_sent,
            "transport_delta_packets": self.delta_packets_sent,
            "transport_bytes_sent": self.transport_bytes_sent,
            "transport_last_packet_bytes": self.last_packet_bytes,
            "transport_max_packet_bytes": self.max_packet_bytes,
            "writer_error": self._writer_error,
            "freeze": int(self.freeze),
        })
        return result

    def close(self) -> None:
        if self._closed: return
        self._closed = True
        try:
            with self._lock:
                self._controls.appendleft(self.packet(PKT_QUIT)); self._controls.append(None)
            self._event.set(); self.proc.wait(timeout=1.5)
        except Exception:
            try: self.proc.terminate(); self.proc.wait(timeout=0.5)
            except Exception:
                try: self.proc.kill()
                except Exception: pass


def _contract_smoke() -> None:
    class V:
        mode=4; backdrop=3; scroll_a_x=1; scroll_a_y=2; scroll_b_x=3; scroll_b_y=4
        vram=bytearray(VRAM_SIZE); cram=bytearray(CRAM_SIZE)
    payload=NativeHostClient.frame_payload(V(),123)
    assert len(payload)==FRAME_META.size+VRAM_SIZE+CRAM_SIZE
    frame,mode,back,ax,ay,bx,by,vs,cs=FRAME_META.unpack_from(payload)
    assert (frame,mode,back,ax,ay,bx,by,vs,cs)==(123,4,3,1,2,3,4,VRAM_SIZE,CRAM_SIZE)
    dp=NativeHostClient.delta_payload(V(),124,{1,2,3,100},{4,5})
    meta=FRAME_META.unpack_from(dp)
    assert meta[0]==124 and meta[-2:]==(2,1)

if __name__ == "__main__":
    _contract_smoke(); print("DMS-1 P1.0.8 native host protocol: OK")
