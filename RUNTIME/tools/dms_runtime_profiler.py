#!/usr/bin/env python3
"""P1.0.5 lightweight runtime profiler for the DMS-1 host frontend.

The CSV is appended during execution so useful data survives even if the host
window later freezes and must be terminated. The TXT summary is refreshed
periodically and finalized on a clean exit.
"""
from __future__ import annotations

import csv
import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered)-1, int(math.ceil(p * len(ordered))) - 1))
    return ordered[idx]


def _fmt_stats(values: list[float], unit: str = "") -> str:
    if not values:
        return "n/a"
    return (f"avg={statistics.fmean(values):.3f}{unit}  "
            f"p95={_pct(values, .95):.3f}{unit}  "
            f"max={max(values):.3f}{unit}")


@dataclass
class ModeMetrics:
    wall_s: float = 0.0
    samples: int = 0
    rendered_frames: int = 0
    skipped_frames: int = 0
    catchup_frames: int = 0
    buffering_count: int = 0
    render_ms: list[float] = field(default_factory=list)
    sim_ms: list[float] = field(default_factory=list)
    sim_due: list[float] = field(default_factory=list)
    video_gap_ms: list[float] = field(default_factory=list)
    fps: list[float] = field(default_factory=list)
    tx_depth: list[float] = field(default_factory=list)


class RuntimeProfiler:
    FIELDS = [
        "elapsed_s","mode","machine_frame","audio_running","audio_status",
        "display_fps","render_ms","video_gap_ms","frames_skipped_total",
        "sim_ms","sim_due","sim_accum_frames","catchup_total","master_cycle",
        "m68k_cycles","z80_cycles","native_audio_writes","audio_events_delta",
        "audio_tx_depth","audio_tx_capacity","audio_process_alive",
        "buffering_total","host_process_cpu_pct"
    ]

    def __init__(self, root: Path) -> None:
        self.dir = root / "DOCS_REPORTS" / "runtime"
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_path = self.dir / f"DMS1_RUNTIME_{stamp}.csv"
        self.txt_path = self.dir / f"DMS1_RUNTIME_{stamp}.txt"
        self.last_csv = self.dir / "DMS1_LAST_RUNTIME.csv"
        self.last_txt = self.dir / "DMS1_LAST_RUNTIME_REPORT.txt"
        self.start_wall = time.perf_counter()
        self.last_sample_wall = self.start_wall
        self.last_cpu_wall = self.start_wall
        self.last_cpu_time = time.process_time()
        self.last_audio_writes = 0
        self.mode = defaultdict(ModeMetrics)
        self.buffering_total = 0
        self.catchup_total = 0
        self.frames_skipped_total = 0
        self.rendered_total = 0
        self.mode_switches = 0
        self.last_mode = None
        self.last_present_wall = None
        self.last_video_gap_ms = 0.0
        self.last_sim_ms = 0.0
        self.last_sim_due = 0
        self.last_audio_status = ""
        self.status_counts = defaultdict(int)
        self.rows = 0
        self.last_snapshot_sync = self.start_wall
        self._f = self.csv_path.open("w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._f, fieldnames=self.FIELDS)
        self._writer.writeheader(); self._f.flush()

    def note_audio_status(self, status: str) -> None:
        self.last_audio_status = status
        self.status_counts[status] += 1
        if "BUFFERING" in status:
            self.buffering_total += 1
            if self.last_mode is not None:
                self.mode[self.last_mode].buffering_count += 1

    def note_sim(self, mode: int, due: int, duration_ms: float) -> None:
        self.last_sim_ms = duration_ms
        self.last_sim_due = due
        mm = self.mode[mode]
        mm.sim_ms.append(duration_ms)
        mm.sim_due.append(float(due))
        if due > 1:
            catch = due - 1
            self.catchup_total += catch
            mm.catchup_frames += catch

    def note_present(self, mode: int, frame_delta: int, render_ms: float) -> None:
        now = time.perf_counter()
        gap = 0.0 if self.last_present_wall is None else (now-self.last_present_wall)*1000.0
        self.last_present_wall = now
        self.last_video_gap_ms = gap
        skipped = max(0, frame_delta - 1)
        self.frames_skipped_total += skipped
        self.rendered_total += 1
        mm = self.mode[mode]
        mm.rendered_frames += 1
        mm.skipped_frames += skipped
        mm.render_ms.append(render_ms)
        if gap:
            mm.video_gap_ms.append(gap)

    def sample(self, *, machine, display_fps: float, sim_accum: float,
               audio_status: str, audio_stats: dict | None) -> None:
        now=time.perf_counter(); elapsed=now-self.start_wall
        mode=int(machine.vdp.mode)
        if self.last_mode is None:
            self.last_mode=mode
        elif mode != self.last_mode:
            self.mode_switches += 1
            self.last_mode=mode
        wall_delta=max(1e-9, now-self.last_sample_wall)
        self.last_sample_wall=now
        mm=self.mode[mode]; mm.wall_s += wall_delta; mm.samples += 1; mm.fps.append(display_fps)
        cpu_now=time.process_time(); cpu_wall=max(1e-9,now-self.last_cpu_wall)
        cpu_pct=(cpu_now-self.last_cpu_time)/cpu_wall*100.0
        self.last_cpu_time=cpu_now; self.last_cpu_wall=now
        state=machine.debug_state(); writes=int(state["native_audio_writes"])
        delta=writes-self.last_audio_writes if writes>=self.last_audio_writes else writes
        self.last_audio_writes=writes
        tx_depth=int((audio_stats or {}).get("tx_queue_depth",0)); mm.tx_depth.append(float(tx_depth))
        row={
            "elapsed_s":f"{elapsed:.3f}","mode":mode,"machine_frame":state["frame"],
            "audio_running":int(bool(state["audio_running"])),"audio_status":audio_status,
            "display_fps":f"{display_fps:.3f}","render_ms":f"{(mm.render_ms[-1] if mm.render_ms else 0):.3f}",
            "video_gap_ms":f"{self.last_video_gap_ms:.3f}","frames_skipped_total":self.frames_skipped_total,
            "sim_ms":f"{self.last_sim_ms:.3f}","sim_due":self.last_sim_due,
            "sim_accum_frames":f"{sim_accum:.4f}","catchup_total":self.catchup_total,
            "master_cycle":state["master_cycle"],"m68k_cycles":state["m68k_cycles"],"z80_cycles":state["z80_cycles"],
            "native_audio_writes":writes,"audio_events_delta":delta,
            "audio_tx_depth":tx_depth,"audio_tx_capacity":int((audio_stats or {}).get("tx_queue_capacity",0)),
            "audio_process_alive":int(bool((audio_stats or {}).get("process_alive",False))),
            "buffering_total":self.buffering_total,"host_process_cpu_pct":f"{cpu_pct:.2f}",
        }
        self._writer.writerow(row); self._f.flush(); self.rows += 1
        # Refresh fixed-name diagnostic files once per second. Keeping this out
        # of the 10 Hz hot path avoids the profiler becoming its own bottleneck.
        if now - self.last_snapshot_sync >= 1.0:
            self.last_snapshot_sync = now
            try:
                self.last_csv.write_bytes(self.csv_path.read_bytes())
            except Exception:
                pass
            self.write_summary(clean_exit=False)

    def write_summary(self, clean_exit: bool) -> None:
        runtime=time.perf_counter()-self.start_wall
        lines=[
            "DAC MASTER DMS-1 P1.0.5 - RUNTIME DIAGNOSTIC REPORT",
            "="*66,
            f"Status              : {'CLEAN EXIT' if clean_exit else 'RUNNING / PERIODIC SNAPSHOT'}",
            f"Runtime measured    : {runtime:.2f} s",
            f"CSV samples         : {self.rows}",
            f"Mode switches       : {self.mode_switches}",
            f"Frames presented    : {self.rendered_total}",
            f"Emulated frames skipped by presenter : {self.frames_skipped_total}",
            f"Scheduler catch-up frames             : {self.catchup_total}",
            f"Audio BUFFERING statuses              : {self.buffering_total}",
            "",
            "INTERPRETATION THRESHOLDS",
            "- target presentation: ~60 FPS; sustained <55 FPS is suspicious",
            "- 60 Hz frame budget: 16.667 ms",
            "- render p95 >16.667 ms means the host cannot present every frame",
            "- repeated frame skips/catch-up indicate scheduler/presentation starvation",
            "- audio TX queue approaching 512 indicates pipe/runtime backpressure",
            "",
        ]
        for mode in sorted(self.mode):
            m=self.mode[mode]
            lines += [
                f"MODE {mode}",
                f"  sampled wall time : {m.wall_s:.2f} s",
                f"  FPS               : {_fmt_stats(m.fps, '')}",
                f"  render            : {_fmt_stats(m.render_ms, ' ms')}",
                f"  video frame gap   : {_fmt_stats(m.video_gap_ms, ' ms')}",
                f"  scheduler tick    : {_fmt_stats(m.sim_ms, ' ms')}",
                f"  due frames/tick   : {_fmt_stats(m.sim_due, '')}",
                f"  presented frames  : {m.rendered_frames}",
                f"  skipped frames    : {m.skipped_frames}",
                f"  catch-up frames   : {m.catchup_frames}",
                f"  audio buffering   : {m.buffering_count}",
                f"  audio TX depth    : {_fmt_stats(m.tx_depth, '')}",
                "",
            ]
        if self.status_counts:
            lines.append("AUDIO STATUS COUNTS")
            for key,val in sorted(self.status_counts.items(), key=lambda kv:(-kv[1],kv[0])):
                lines.append(f"  {val:5d}  {key}")
            lines.append("")
        lines += [
            "Files:",
            f"  CSV : {self.csv_path.name}",
            f"  TXT : {self.txt_path.name}",
            "",
            "For diagnosis, upload DMS1_LAST_RUNTIME_REPORT.txt and DMS1_LAST_RUNTIME.csv.",
        ]
        text="\n".join(lines)+"\n"
        self.txt_path.write_text(text,encoding="utf-8")
        self.last_txt.write_text(text,encoding="utf-8")

    def close(self) -> None:
        self.write_summary(clean_exit=True)
        try: self._f.flush(); self._f.close()
        except Exception: pass
        try: self.last_csv.write_bytes(self.csv_path.read_bytes())
        except Exception: pass
