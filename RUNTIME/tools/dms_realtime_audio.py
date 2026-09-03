#!/usr/bin/env python3
"""P1.0.3 host bridge: async live Z80 MMIO -> DMS-1 RealtimeCore -> waveOut.

The Tk/emulator thread never writes directly to the subprocess pipe. All traffic
is queued to a dedicated writer thread so Windows pipe backpressure cannot freeze
video/input presentation.
"""
from __future__ import annotations
import os
import queue
import subprocess
import threading
from pathlib import Path


class RealtimeAudioClient:
    def __init__(self, executable: Path, sample_rom: Path, cwd: Path) -> None:
        if os.name != "nt":
            raise RuntimeError("real-time waveOut bridge is Windows-only")
        flags = subprocess.CREATE_NO_WINDOW
        child_env = os.environ.copy()
        child_env["PATH"] = str(executable.parent) + os.pathsep + child_env.get("PATH", "")
        self.proc = subprocess.Popen(
            [str(executable), str(sample_rom)], cwd=cwd, env=child_env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, creationflags=flags,
        )
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("unable to open real-time audio pipes")
        self.status: queue.Queue[str] = queue.Queue()
        self.errors: queue.Queue[str] = queue.Queue()
        self._tx: queue.Queue[str | None] = queue.Queue(maxsize=512)
        self._closed = False
        self._writer_error: str | None = None
        threading.Thread(target=self._read_stdout, daemon=True, name="dms1-rt-audio-out").start()
        threading.Thread(target=self._read_stderr, daemon=True, name="dms1-rt-audio-err").start()
        threading.Thread(target=self._writer, daemon=True, name="dms1-rt-audio-writer").start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            text = line.strip()
            if text:
                self.status.put(text)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            text = line.strip()
            if text:
                self.errors.put(text)

    def _writer(self) -> None:
        assert self.proc.stdin is not None
        while True:
            payload = self._tx.get()
            if payload is None:
                return
            try:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"real-time audio bridge stopped ({self.proc.returncode})")
                self.proc.stdin.write(payload)
                self.proc.stdin.flush()
            except Exception as exc:
                self._writer_error = str(exc)
                self.errors.put("writer: " + self._writer_error)
                return

    def _send(self, payload: str) -> None:
        if self._closed or self.proc.poll() is not None:
            raise RuntimeError("real-time audio bridge stopped")
        if self._writer_error:
            raise RuntimeError(self._writer_error)
        try:
            # Non-blocking by design: never stall Tk/video because Windows pipe is busy.
            self._tx.put_nowait(payload)
        except queue.Full as exc:
            raise RuntimeError("real-time audio TX queue saturated") from exc

    def play(self) -> None:
        self._send("P\n")

    def stop(self) -> None:
        self._send("S\n")

    def reset_stream(self) -> None:
        self._send("R\n")

    def feed(self, events: list[tuple[int, int, int]], fed_until: int) -> None:
        if not events:
            self._send(f"F {int(fed_until)}\n")
            return
        lines = [f"E {int(cycle)} {int(address):04x} {int(data):02x}\n" for cycle, address, data in events]
        lines.append(f"F {int(fed_until)}\n")
        self._send("".join(lines))

    def poll_status(self) -> list[str]:
        out: list[str] = []
        while True:
            try:
                out.append(self.status.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                out.append("ERROR " + self.errors.get_nowait())
            except queue.Empty:
                break
        if self.proc.poll() not in (None, 0):
            out.append(f"ERROR bridge exited {self.proc.returncode}")
        if self._writer_error:
            out.append("ERROR writer " + self._writer_error)
            self._writer_error = None
        return out


    def diagnostic_stats(self) -> dict[str, int | bool | str | None]:
        """Cheap host-side telemetry; never blocks the audio writer."""
        return {
            "tx_queue_depth": self._tx.qsize(),
            "tx_queue_capacity": self._tx.maxsize,
            "closed": self._closed,
            "process_alive": self.proc.poll() is None,
            "writer_error": self._writer_error,
        }

    def close(self) -> None:
        if self._closed:
            return
        # Queue Q behind all already-enqueued writes so the runtime sees a clean exit.
        try:
            self._tx.put_nowait("Q\n")
            self._tx.put_nowait(None)
        except queue.Full:
            pass
        self._closed = True
        try:
            self.proc.wait(timeout=1.0)
        except Exception:
            self.proc.kill()
