#!/usr/bin/env python3
"""P1.0.9 final-runtime-lock profiler.

Critical rule: no filesystem I/O occurs on the 60 Hz console thread.
Rows are queued to a background writer. DMS1_LAST_RUNTIME.csv is written live
in parallel with the timestamped CSV, so a forced shutdown still leaves useful
data without rereading/copying an ever-growing file once per second.
"""
from __future__ import annotations

import csv
import math
import queue
import statistics
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def pct(values: list[float], p: float) -> float:
    if not values: return 0.0
    values=sorted(values)
    return values[max(0,min(len(values)-1,math.ceil(p*len(values))-1))]

def stats(values: list[float], unit: str="") -> str:
    if not values: return "n/a"
    return f"avg={statistics.fmean(values):.3f}{unit}  p95={pct(values,.95):.3f}{unit}  max={max(values):.3f}{unit}"

@dataclass
class Mode:
    wall_s: float=0.0
    samples: int=0
    host_fps: list[float]=field(default_factory=list)
    render_ms: list[float]=field(default_factory=list)
    sim_ms: list[float]=field(default_factory=list)
    sim_late_ms: list[float]=field(default_factory=list)
    present_gap_ms: list[float]=field(default_factory=list)
    host_received_delta: int=0
    host_rendered_delta: int=0
    host_overwritten_delta: int=0
    py_overwritten_delta: int=0
    audio_buffering: int=0

class NativeRuntimeProfiler:
    FIELDS=[
        "elapsed_s","mode","machine_frame","audio_running","audio_status",
        "sim_ms","sim_late_ms","scheduler_resyncs","master_cycle","m68k_cycles","z80_cycles",
        "native_audio_writes","audio_events_delta","audio_tx_depth","audio_process_alive",
        "host_fps","host_render_fps","host_rx_fps","host_received","host_rendered","host_presented","host_overwritten",
        "host_drop_delta","host_paints","host_render_ms","host_frame","host_freeze","host_gap_ms","host_gap_max_ms",
        "python_frame_queue_overwrites","host_process_alive","host_writer_error",
        "transport_full_packets","transport_delta_packets","transport_bytes_sent","transport_last_packet_bytes",
        "host_process_cpu_pct","host_dwm_flushes","buffering_total",
        "m68k_load_pct","m68k_active_cycles","m68k_wait_cycles","m68k_budget_cycles",
        "z80_load_pct","z80_active_cycles","z80_budget_cycles",
        "sprites_active","sprites_limit","scanline_peak","scanline_limit","scanline_overflows"
    ]

    def __init__(self, root: Path) -> None:
        self.dir=root/"DOCS_REPORTS"/"runtime"; self.dir.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_path=self.dir/f"DMS1_NATIVE_RUNTIME_{stamp}.csv"
        self.txt_path=self.dir/f"DMS1_NATIVE_RUNTIME_{stamp}.txt"
        self.last_csv=self.dir/"DMS1_LAST_RUNTIME.csv"
        self.last_txt=self.dir/"DMS1_LAST_RUNTIME_REPORT.txt"
        self.start=time.perf_counter(); self.last_sample=self.start
        self.last_cpu_wall=self.start; self.last_cpu_time=time.process_time()
        self.last_audio_writes=0; self.last_host_received=0; self.last_host_rendered=0
        self.last_host_overwritten=0; self.last_py_overwritten=0
        self.last_mode:int|None=None; self.mode_switches=0; self.buffering_total=0
        self.status_counts=defaultdict(int); self.modes=defaultdict(Mode); self.rows=0
        self.scheduler_resyncs=0; self.last_sim_ms=0.0; self.last_late_ms=0.0
        self._rowq: queue.Queue[dict|None]=queue.Queue(maxsize=4096)
        self._io_dropped=0; self._writer_error:str|None=None
        self._writer=threading.Thread(target=self._writer_loop,daemon=True,name="dms1-p108-profiler-io")
        self._writer.start()

    def _writer_loop(self)->None:
        try:
            with self.csv_path.open("w",encoding="utf-8",newline="") as f, self.last_csv.open("w",encoding="utf-8",newline="") as lf:
                w=csv.DictWriter(f,fieldnames=self.FIELDS); lw=csv.DictWriter(lf,fieldnames=self.FIELDS)
                w.writeheader(); lw.writeheader(); f.flush(); lf.flush()
                since_flush=0
                while True:
                    row=self._rowq.get()
                    if row is None: break
                    w.writerow(row); lw.writerow(row); since_flush+=1
                    # Background-only flush: enough crash resilience without touching realtime.
                    if since_flush>=10:
                        f.flush(); lf.flush(); since_flush=0
                f.flush(); lf.flush()
        except Exception as exc:
            self._writer_error=str(exc)

    def note_audio_status(self,text:str)->None:
        self.status_counts[text]+=1
        if "BUFFERING" in text:
            self.buffering_total+=1
            if self.last_mode is not None: self.modes[self.last_mode].audio_buffering+=1

    def note_sim(self,duration_ms:float,late_ms:float,resynced:bool)->None:
        self.last_sim_ms=float(duration_ms); self.last_late_ms=float(late_ms)
        if resynced: self.scheduler_resyncs+=1

    def sample(self,*,machine,audio_status:str,audio_stats:dict|None,host_stats:dict|None)->None:
        now=time.perf_counter(); elapsed=now-self.start; wall=max(1e-9,now-self.last_sample); self.last_sample=now
        mode=int(machine.vdp.mode)
        if self.last_mode is None: self.last_mode=mode
        elif mode!=self.last_mode: self.mode_switches+=1; self.last_mode=mode
        m=self.modes[mode]; m.wall_s+=wall; m.samples+=1
        hs=host_stats or {}; fps=float(hs.get("fps",0.0) or 0.0); render_ms=float(hs.get("render_ms",0.0) or 0.0)
        gap=float(hs.get("gap_ms",0.0) or 0.0)
        if fps>0:m.host_fps.append(fps)
        if render_ms>=0:m.render_ms.append(render_ms)
        if gap>0:m.present_gap_ms.append(gap)
        m.sim_ms.append(self.last_sim_ms); m.sim_late_ms.append(max(0.0,self.last_late_ms))
        received=int(hs.get("received",0) or 0); rendered=int(hs.get("rendered",0) or 0)
        overwritten=int(hs.get("overwritten",0) or 0); py_over=int(hs.get("python_frame_queue_overwrites",0) or 0)
        if received>=self.last_host_received:m.host_received_delta+=received-self.last_host_received
        if rendered>=self.last_host_rendered:m.host_rendered_delta+=rendered-self.last_host_rendered
        if overwritten>=self.last_host_overwritten:m.host_overwritten_delta+=overwritten-self.last_host_overwritten
        if py_over>=self.last_py_overwritten:m.py_overwritten_delta+=py_over-self.last_py_overwritten
        self.last_host_received=received; self.last_host_rendered=rendered; self.last_host_overwritten=overwritten; self.last_py_overwritten=py_over
        cpu_now=time.process_time(); cpu_wall=max(1e-9,now-self.last_cpu_wall)
        cpu_pct=(cpu_now-self.last_cpu_time)/cpu_wall*100.0; self.last_cpu_time=cpu_now; self.last_cpu_wall=now
        state=machine.debug_state(); writes=int(state["native_audio_writes"])
        delta=writes-self.last_audio_writes if writes>=self.last_audio_writes else writes; self.last_audio_writes=writes
        a=audio_stats or {}
        row={
            "elapsed_s":f"{elapsed:.3f}","mode":mode,"machine_frame":state["frame"],"audio_running":int(bool(state["audio_running"])),"audio_status":audio_status,
            "sim_ms":f"{self.last_sim_ms:.3f}","sim_late_ms":f"{self.last_late_ms:.3f}","scheduler_resyncs":self.scheduler_resyncs,
            "master_cycle":state["master_cycle"],"m68k_cycles":state["m68k_cycles"],"z80_cycles":state["z80_cycles"],
            "native_audio_writes":writes,"audio_events_delta":delta,"audio_tx_depth":int(a.get("tx_queue_depth",0) or 0),"audio_process_alive":int(bool(a.get("process_alive",False))),
            "host_fps":f"{fps:.3f}","host_render_fps":f"{float(hs.get('render_fps',0) or 0):.3f}","host_rx_fps":f"{float(hs.get('rx_fps',0) or 0):.3f}",
            "host_received":received,"host_rendered":rendered,"host_presented":int(hs.get("presented",0) or 0),"host_overwritten":overwritten,
            "host_drop_delta":int(hs.get("drop_delta",0) or 0),"host_paints":int(hs.get("paints",0) or 0),"host_render_ms":f"{render_ms:.3f}",
            "host_frame":int(hs.get("frame",0) or 0),"host_freeze":int(hs.get("freeze",0) or 0),"host_gap_ms":f"{gap:.3f}","host_gap_max_ms":f"{float(hs.get('gap_max_ms',0) or 0):.3f}",
            "python_frame_queue_overwrites":py_over,"host_process_alive":int(bool(hs.get("process_alive",False))),"host_writer_error":str(hs.get("writer_error") or ""),
            "transport_full_packets":int(hs.get("transport_full_packets",0) or 0),"transport_delta_packets":int(hs.get("transport_delta_packets",0) or 0),
            "transport_bytes_sent":int(hs.get("transport_bytes_sent",0) or 0),"transport_last_packet_bytes":int(hs.get("transport_last_packet_bytes",0) or 0),
            "host_process_cpu_pct":f"{cpu_pct:.2f}","host_dwm_flushes":int(hs.get("dwm_flushes",0) or 0),"buffering_total":self.buffering_total,
        }
        spr = machine.vdp.debug_sprite_metrics() if hasattr(machine.vdp, "debug_sprite_metrics") else {"active":0,"total_limit":0,"peak_scanline":0,"scanline_limit":0,"overflow_count":0}
        row.update({
            "m68k_load_pct":f"{float(state.get('m68k_load_pct',0.0)):.2f}",
            "m68k_active_cycles":int(state.get("m68k_active",0)),
            "m68k_wait_cycles":int(state.get("m68k_wait",0)),
            "m68k_budget_cycles":int(state.get("m68k_budget",0)),
            "z80_load_pct":f"{float(state.get('z80_load_pct',0.0)):.2f}",
            "z80_active_cycles":int(state.get("z80_active",0)),
            "z80_budget_cycles":int(state.get("z80_budget",0)),
            "sprites_active":int(spr.get("active",0)),
            "sprites_limit":int(spr.get("total_limit",0)),
            "scanline_peak":int(spr.get("peak_scanline",0)),
            "scanline_limit":int(spr.get("scanline_limit",0)),
            "scanline_overflows":int(spr.get("overflow_count",0)),
        })
        self.rows+=1
        try:self._rowq.put_nowait(row)
        except queue.Full:self._io_dropped+=1

    def write_summary(self,clean_exit:bool,host_stats:dict|None=None)->None:
        runtime=time.perf_counter()-self.start; hs=host_stats or {}
        lines=[
            "DAC MASTER DMS-1 P1.0.9 - FINAL RUNTIME LOCK REPORT","="*70,
            f"Status              : {'CLEAN EXIT' if clean_exit else 'SNAPSHOT'}",f"Runtime measured    : {runtime:.2f} s",f"CSV samples         : {self.rows}",
            f"Mode switches       : {self.mode_switches}",f"Scheduler resyncs   : {self.scheduler_resyncs}",f"Audio BUFFERING     : {self.buffering_total}",
            f"Profiler rows dropped: {self._io_dropped}",f"Profiler writer error: {self._writer_error or 'none'}",
            f"Native host received: {int(hs.get('received',self.last_host_received) or 0)}",f"Native host rendered: {int(hs.get('rendered',self.last_host_rendered) or 0)}",
            f"Native host presented: {int(hs.get('presented',0) or 0)}",f"Native host overwrite/drop: {int(hs.get('overwritten',self.last_host_overwritten) or 0)}",
            f"Python pending-frame overwrite: {int(hs.get('python_frame_queue_overwrites',self.last_py_overwritten) or 0)}",
            f"Transport full packets : {int(hs.get('transport_full_packets',0) or 0)}",f"Transport delta packets: {int(hs.get('transport_delta_packets',0) or 0)}",
            f"Transport bytes sent   : {int(hs.get('transport_bytes_sent',0) or 0)}",f"Last frame packet bytes: {int(hs.get('transport_last_packet_bytes',0) or 0)}",
            "","P1.0.9 FINAL RUNTIME LOCK",
            "- no Tk/Tcl in realtime display/input path",
            "- profiler disk writes moved to a background I/O thread",
            "- no once-per-second CSV reread/copy on the console scheduler",
            "- Win32 STAT telemetry moved off the GUI/message thread",
            "- first video frame is full; later frames use merged VRAM/CRAM dirty deltas",
            "- Python cyclic GC disabled only while realtime loop is running",
            "- native raster scratch buffers and frame snapshot are allocation-free after warmup",
            "- video and HUD invalidation are separated",
            "- optional DwmFlush compositor pacing active when Windows DWM exposes it",
            "","TARGETS","- stable visual pacing without ~1 Hz hitch","- native host ~59-60 FPS","- host gap p95 close to 16.67 ms","- audio BUFFERING = 0","",
        ]
        for mode in sorted(self.modes):
            m=self.modes[mode]
            lines += [f"MODE {mode}",f"  sampled wall time      : {m.wall_s:.2f} s",f"  native host FPS        : {stats(m.host_fps)}",
                      f"  native render          : {stats(m.render_ms,' ms')}",f"  present gap            : {stats(m.present_gap_ms,' ms')}",
                      f"  Python step_frame      : {stats(m.sim_ms,' ms')}",f"  scheduler lateness     : {stats(m.sim_late_ms,' ms')}",
                      f"  host frames received   : {m.host_received_delta}",f"  host frames rendered   : {m.host_rendered_delta}",
                      f"  host overwrite/drop    : {m.host_overwritten_delta}",f"  Python queue overwrite : {m.py_overwritten_delta}",f"  audio buffering        : {m.audio_buffering}",""]
        if self.status_counts:
            lines.append("AUDIO STATUS COUNTS")
            for key,val in sorted(self.status_counts.items(),key=lambda kv:(-kv[1],kv[0])):lines.append(f"  {val:5d}  {key}")
            lines.append("")
        lines += ["Files:",f"  CSV : {self.csv_path.name}",f"  TXT : {self.txt_path.name}","","Upload DMS1_LAST_RUNTIME_REPORT.txt and DMS1_LAST_RUNTIME.csv for diagnosis."]
        text="\n".join(lines)+"\n"; self.txt_path.write_text(text,encoding="utf-8"); self.last_txt.write_text(text,encoding="utf-8")

    def close(self,host_stats:dict|None=None)->None:
        try:self._rowq.put(None,timeout=1.0)
        except Exception:pass
        self._writer.join(timeout=2.0)
        self.write_summary(True,host_stats=host_stats)
