from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP_NAME = "DMS Game Flow Builder"
APP_VERSION = "0.1.0"
HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
CORE_PATH = ROOT / "GDK" / "tools" / "dmsflowc.py"
spec = importlib.util.spec_from_file_location("dmsflowc", CORE_PATH)
core = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["dmsflowc"] = core
spec.loader.exec_module(core)

TYPE_COLORS = {
    "SCREEN": "#31546d",
    "MENU": "#5d4777",
    "GAME": "#35664d",
    "CUTSCENE": "#775642",
    "SUBFLOW": "#636363",
}


def clean_id(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", text.upper()).strip("_") or "STATE"
    if s[0].isdigit(): s = "S_" + s
    return s


def default_node(nid: str, typ: str, flow_id: str, x: float, y: float) -> dict:
    return {
        "id": nid, "name": nid.replace("_", " ").title(), "type": typ, "flow_id": flow_id,
        "x": int(x), "y": int(y), "scene": "", "video_mode": -1,
        "map": "", "collision": "", "actor": "", "actor_x": 152, "actor_y": 96,
        "music": "", "image": "", "sprite": "", "audio": "",
        "enter_fx": "NONE", "enter_fx_duration": 16,
        "exit_fx": "NONE", "exit_fx_duration": 16, "stop_music_on_exit": False,
        "enter_callback": "", "update_callback": "", "exit_callback": "",
        "subflow_id": "", "notes": "",
    }


def default_transition(tid: str, src: str, dst: str, event: str = "START") -> dict:
    return {
        "id": tid, "name": event, "source": src, "destination": dst,
        "event": clean_id(event), "condition": "", "delay_frames": 0,
        "visual_fx": "NONE", "fx_duration": 16, "priority": 100,
    }


class FlowApp(tk.Tk):
    def __init__(self, open_path: Path | None = None):
        super().__init__()
        self.title(f"{APP_NAME} - {APP_VERSION}")
        self.geometry("1680x940")
        self.minsize(1200, 720)
        self.flow = core.new_flow("GAME")
        self.path: Path | None = None
        self.current_flow = "MAIN"
        self.flow_stack: list[str] = []
        self.selected_node: str | None = None
        self.selected_transition: str | None = None
        self.drag_node: str | None = None
        self.drag_dx = 0.0; self.drag_dy = 0.0; self.drag_snapshot_taken = False
        self.connect_source: str | None = None
        self.scale = 1.0
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.autosave_job = None
        self.dirty = False
        self.vars: dict[str, tk.Variable] = {}
        self.tvars: dict[str, tk.Variable] = {}
        self._style(); self._build_ui(); self._bind_keys(); self.protocol("WM_DELETE_WINDOW", self.close_app)
        if open_path and open_path.exists(): self.load_path(open_path)
        else:
            self.add_node("SCREEN", nid="BOOT", record=False)
            self._set_entry("BOOT")
            self.refresh_all()

    def _style(self):
        s = ttk.Style(self)
        try: s.theme_use("clam")
        except Exception: pass
        bg = "#15181c"; fg = "#e7e7e7"
        self.configure(bg=bg)
        s.configure(".", font=("Segoe UI", 9))
        s.configure("TFrame", background=bg)
        s.configure("TLabel", background=bg, foreground=fg)
        s.configure("TLabelframe", background=bg, foreground=fg)
        s.configure("TLabelframe.Label", background=bg, foreground=fg)
        s.configure("Title.TLabel", background=bg, foreground="#ffffff", font=("Segoe UI", 16, "bold"))
        s.configure("Muted.TLabel", background=bg, foreground="#9aa3ad")
        s.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))

    def _build_ui(self):
        top = ttk.Frame(self, padding=(10, 8)); top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, style="Title.TLabel").pack(side="left")
        self.breadcrumb = ttk.Label(top, text="GAME / MAIN", style="Muted.TLabel"); self.breadcrumb.pack(side="left", padx=14)
        for text, cmd in [("Nouveau", self.new), ("Ouvrir", self.open), ("Sauver", self.save), ("Valider", self.validate_ui), ("EXPORT GDK", self.export_gdk)]:
            ttk.Button(top, text=text, command=cmd, style="Accent.TButton" if text == "EXPORT GDK" else "TButton").pack(side="right", padx=3)

        tools = ttk.Frame(self, padding=(10, 2)); tools.pack(fill="x")
        for typ in ("SCREEN", "MENU", "GAME", "CUTSCENE"):
            ttk.Button(tools, text=f"+ {typ}", command=lambda t=typ: self.add_node(t)).pack(side="left", padx=2)
        ttk.Button(tools, text="+ SUBFLOW", command=self.add_subflow).pack(side="left", padx=(2, 10))
        ttk.Button(tools, text="Relier", command=self.start_connect).pack(side="left", padx=2)
        ttk.Button(tools, text="↶", width=3, command=self.undo).pack(side="left", padx=(10,2))
        ttk.Button(tools, text="↷", width=3, command=self.redo).pack(side="left")
        ttk.Button(tools, text="Retour sous-flow", command=self.back_flow).pack(side="left", padx=(12,2))
        ttk.Button(tools, text="Centrer", command=self.center_graph).pack(side="left", padx=2)
        ttk.Button(tools, text="−", width=3, command=lambda: self.zoom(0.9)).pack(side="left", padx=(12,2))
        ttk.Button(tools, text="+", width=3, command=lambda: self.zoom(1.1)).pack(side="left")
        self.mode_label = ttk.Label(tools, text="Sélection", style="Muted.TLabel"); self.mode_label.pack(side="right")

        paned = ttk.Panedwindow(self, orient="horizontal"); paned.pack(fill="both", expand=True, padx=10, pady=8)
        left = ttk.Frame(paned, width=280); center = ttk.Frame(paned); right = ttk.Frame(paned, width=390)
        paned.add(left, weight=1); paned.add(center, weight=4); paned.add(right, weight=2)

        lf = ttk.LabelFrame(left, text="Arborescence du jeu", padding=6); lf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(lf, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.tree_select)
        self.tree.bind("<Double-1>", self.tree_double)

        cf = ttk.LabelFrame(center, text="Graphe", padding=4); cf.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(cf, bg="#0d1014", highlightthickness=0, scrollregion=(-3000,-3000,3000,3000))
        self.hbar = ttk.Scrollbar(cf, orient="horizontal", command=self.canvas.xview)
        self.vbar = ttk.Scrollbar(cf, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.hbar.set, yscrollcommand=self.vbar.set)
        self.canvas.grid(row=0,column=0,sticky="nsew"); self.vbar.grid(row=0,column=1,sticky="ns"); self.hbar.grid(row=1,column=0,sticky="ew")
        cf.rowconfigure(0,weight=1); cf.columnconfigure(0,weight=1)
        self.canvas.bind("<Button-1>", self.canvas_click)
        self.canvas.bind("<B1-Motion>", self.canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.canvas_release)
        self.canvas.bind("<Double-1>", self.canvas_double)
        self.canvas.bind("<Button-3>", self.canvas_context)
        self.canvas.bind("<ButtonPress-2>", lambda e: self.canvas.scan_mark(e.x,e.y))
        self.canvas.bind("<B2-Motion>", lambda e: self.canvas.scan_dragto(e.x,e.y,gain=1))
        self.canvas.bind("<MouseWheel>", self.canvas_wheel)

        self.props = ttk.LabelFrame(right, text="Propriétés", padding=8); self.props.pack(fill="both", expand=True)
        self.prop_container = ttk.Frame(self.props); self.prop_container.pack(fill="both", expand=True)
        self._build_node_properties(); self._build_transition_properties(); self._show_none_props()

        bottom = ttk.Frame(self, padding=(10, 0, 10, 8)); bottom.pack(fill="x")
        self.status = ttk.Label(bottom, text="Prêt."); self.status.pack(side="left")
        ttk.Label(bottom, text="DFLOW V1 • source éditable • C/H/BIN générés", style="Muted.TLabel").pack(side="right")

    def _build_node_properties(self):
        self.node_frame = ttk.Frame(self.prop_container)
        fields = [
            ("id","ID C","entry",None), ("name","Nom","entry",None), ("type","Type","combo",core.NODE_TYPES),
            ("video_mode","Video mode (-1 auto)","combo",("-1","0","1","2","3","4")),
            ("scene","SCENE / .dscene","entry",None), ("map","MAP / .dmap","entry",None),
            ("collision","COLLISION / .dcoll","entry",None), ("actor","ACTOR / .dactor","entry",None), ("actor_x","Actor X","entry",None), ("actor_y","Actor Y","entry",None),
            ("music","MUSIC / .dmr","entry",None), ("image","IMAGE / .dimg","entry",None),
            ("sprite","SPRITE / .dres","entry",None), ("audio","AUDIO export","entry",None),
            ("enter_fx","ENTER FX","combo",core.FX_NAMES), ("enter_fx_duration","ENTER durée","entry",None),
            ("exit_fx","EXIT FX","combo",core.FX_NAMES), ("exit_fx_duration","EXIT durée","entry",None),
            ("enter_callback","ENTER callback C","entry",None), ("update_callback","UPDATE callback C","entry",None),
            ("exit_callback","EXIT callback C","entry",None), ("subflow_id","Sous-flow","entry",None),
        ]
        for r,(key,label,kind,values) in enumerate(fields):
            ttk.Label(self.node_frame,text=label).grid(row=r,column=0,sticky="w",pady=2)
            v=tk.StringVar(); self.vars[key]=v
            if kind=="combo": w=ttk.Combobox(self.node_frame,textvariable=v,values=values,state="readonly",width=22)
            else: w=ttk.Entry(self.node_frame,textvariable=v,width=25)
            w.grid(row=r,column=1,sticky="ew",padx=5,pady=2)
        self.vars["stop_music_on_exit"] = tk.BooleanVar()
        ttk.Checkbutton(self.node_frame,text="Stop musique en EXIT",variable=self.vars["stop_music_on_exit"]).grid(row=len(fields),column=0,columnspan=2,sticky="w",pady=4)
        bar=ttk.Frame(self.node_frame);bar.grid(row=len(fields)+1,column=0,columnspan=2,sticky="ew",pady=8)
        ttk.Button(bar,text="Appliquer",command=self.apply_node,style="Accent.TButton").pack(side="left")
        ttk.Button(bar,text="Définir entrée",command=self.set_selected_entry).pack(side="left",padx=4)
        ttk.Button(bar,text="Dupliquer",command=self.duplicate_selected).pack(side="left",padx=4)
        ttk.Button(bar,text="Supprimer",command=self.delete_selected).pack(side="right")
        self.node_frame.columnconfigure(1,weight=1)

    def _build_transition_properties(self):
        self.trans_frame = ttk.Frame(self.prop_container)
        fields=[("id","ID","entry",None),("name","Nom","entry",None),("source","Source","combo",()),("destination","Destination","combo",()),
                ("event","Événement","entry",None),("condition","Condition callback C","entry",None),("delay_frames","Délai frames","entry",None),
                ("visual_fx","FX visuel","combo",core.FX_NAMES),("fx_duration","Durée FX","entry",None),("priority","Priorité (0 = forte)","entry",None)]
        self.trans_widgets={}
        for r,(key,label,kind,values) in enumerate(fields):
            ttk.Label(self.trans_frame,text=label).grid(row=r,column=0,sticky="w",pady=3)
            v=tk.StringVar();self.tvars[key]=v
            if kind=="combo": w=ttk.Combobox(self.trans_frame,textvariable=v,values=values,state="readonly",width=22)
            else:w=ttk.Entry(self.trans_frame,textvariable=v,width=25)
            self.trans_widgets[key]=w;w.grid(row=r,column=1,sticky="ew",padx=5,pady=3)
        bar=ttk.Frame(self.trans_frame);bar.grid(row=len(fields),column=0,columnspan=2,sticky="ew",pady=8)
        ttk.Button(bar,text="Appliquer",command=self.apply_transition,style="Accent.TButton").pack(side="left")
        ttk.Button(bar,text="Supprimer transition",command=self.delete_selected).pack(side="right")
        self.trans_frame.columnconfigure(1,weight=1)

    def _show_none_props(self):
        self.node_frame.pack_forget(); self.trans_frame.pack_forget()
        for w in self.prop_container.winfo_children():
            if getattr(w,"_is_placeholder",False): w.destroy()
        x=ttk.Label(self.prop_container,text="Sélectionnez un nœud ou une transition.",style="Muted.TLabel");x._is_placeholder=True;x.pack(anchor="w",pady=8)

    def _show_node_props(self, n: dict):
        for w in self.prop_container.winfo_children():
            if getattr(w,"_is_placeholder",False): w.destroy()
        self.trans_frame.pack_forget(); self.node_frame.pack(fill="both",expand=True)
        for k,v in self.vars.items():
            if k=="stop_music_on_exit": v.set(bool(n.get(k,False)))
            else: v.set(str(n.get(k,"")))

    def _show_transition_props(self,t:dict):
        for w in self.prop_container.winfo_children():
            if getattr(w,"_is_placeholder",False): w.destroy()
        self.node_frame.pack_forget(); self.trans_frame.pack(fill="both",expand=True)
        names=[n["id"] for n in self.flow["nodes"]]
        self.trans_widgets["source"].configure(values=names);self.trans_widgets["destination"].configure(values=names)
        for k,v in self.tvars.items():v.set(str(t.get(k,"")))

    def _bind_keys(self):
        self.bind("<Control-s>",lambda e:self.save())
        self.bind("<Control-o>",lambda e:self.open())
        self.bind("<Control-z>",lambda e:self.undo())
        self.bind("<Control-y>",lambda e:self.redo())
        self.bind("<Delete>",lambda e:self.delete_selected())
        self.bind("<Escape>",lambda e:self.cancel_connect())

    def node_by_id(self,nid): return next((n for n in self.flow["nodes"] if n.get("id")==nid),None)
    def trans_by_id(self,tid): return next((t for t in self.flow["transitions"] if t.get("id")==tid),None)
    def flow_by_id(self,fid): return next((f for f in self.flow["flows"] if f.get("id")==fid),None)

    def snapshot(self):
        self.undo_stack.append(copy.deepcopy(self.flow));self.undo_stack=self.undo_stack[-60:];self.redo_stack.clear()

    def changed(self,redraw=True):
        self.dirty=True
        self.schedule_autosave()
        if redraw:self.refresh_all()

    def schedule_autosave(self):
        if self.autosave_job:self.after_cancel(self.autosave_job)
        if self.path and self.flow.get("settings",{}).get("autosave",True):
            self.autosave_job=self.after(1500,self.autosave)

    def autosave(self):
        self.autosave_job=None
        if self.path:
            try: core.save_flow(self.path,self.flow);self.dirty=False;self.status.configure(text="Autosave DFLOW effectué.")
            except Exception as exc:self.status.configure(text=f"Autosave impossible : {exc}")

    def undo(self):
        if not self.undo_stack:return
        self.redo_stack.append(copy.deepcopy(self.flow));self.flow=self.undo_stack.pop();self._repair_current_flow();self.changed()
    def redo(self):
        if not self.redo_stack:return
        self.undo_stack.append(copy.deepcopy(self.flow));self.flow=self.redo_stack.pop();self._repair_current_flow();self.changed()
    def _repair_current_flow(self):
        if not self.flow_by_id(self.current_flow):self.current_flow=self.flow.get("main_flow","MAIN");self.flow_stack=[]
        self.selected_node=None;self.selected_transition=None

    def unique_node_id(self,base):
        used={n["id"] for n in self.flow["nodes"]};base=clean_id(base);i=1;s=base
        while s in used:i+=1;s=f"{base}_{i}"
        return s
    def unique_trans_id(self):
        used={t["id"] for t in self.flow["transitions"]};i=1
        while f"T{i}" in used:i+=1
        return f"T{i}"

    def add_node(self,typ,nid=None,record=True):
        if record:self.snapshot()
        nid=self.unique_node_id(nid or typ)
        x=self.canvas.canvasx(max(100,self.canvas.winfo_width()/2))/self.scale;y=self.canvas.canvasy(max(80,self.canvas.winfo_height()/2))/self.scale
        n=default_node(nid,typ,self.current_flow,x,y);self.flow["nodes"].append(n);self.selected_node=nid;self.selected_transition=None
        f=self.flow_by_id(self.current_flow)
        if f and not f.get("entry_state"):f["entry_state"]=nid
        if record:self.changed()
        return n

    def add_subflow(self):
        self.snapshot();fid_base=self.unique_node_id("FLOW");fid=clean_id(fid_base)
        while self.flow_by_id(fid):fid += "_X"
        self.flow["flows"].append({"id":fid,"name":fid.replace("_"," ").title(),"entry_state":""})
        n=self.add_node("SUBFLOW",nid=fid,record=False);n["subflow_id"]=fid;n["name"]=self.flow_by_id(fid)["name"]
        self.changed()

    def _set_entry(self,nid):
        f=self.flow_by_id(self.current_flow)
        if f:f["entry_state"]=nid
    def set_selected_entry(self):
        if not self.selected_node:return
        n=self.node_by_id(self.selected_node)
        if not n or n.get("flow_id")!=self.current_flow:return
        self.snapshot();self._set_entry(n["id"]);self.changed()
    def duplicate_selected(self):
        if not self.selected_node:return
        n=self.node_by_id(self.selected_node)
        if not n:return
        self.snapshot();q=copy.deepcopy(n);q["id"]=self.unique_node_id(n["id"]+"_COPY");q["name"]=n.get("name",n["id"])+" Copy";q["x"]=int(n.get("x",0))+30;q["y"]=int(n.get("y",0))+30
        if q.get("type")=="SUBFLOW":q["type"]="SCREEN";q["subflow_id"]=""
        self.flow["nodes"].append(q);self.selected_node=q["id"];self.changed()

    def start_connect(self):
        self.connect_source=None;self.mode_label.configure(text="Connexion : cliquez SOURCE puis DESTINATION")
    def cancel_connect(self):self.connect_source=None;self.mode_label.configure(text="Sélection")
    def connect_nodes(self,src,dst):
        ev=simpledialog.askstring("Transition","Événement déclencheur :",initialvalue="START",parent=self) or "START"
        self.snapshot();t=default_transition(self.unique_trans_id(),src,dst,ev);self.flow["transitions"].append(t);self.selected_transition=t["id"];self.selected_node=None;self.cancel_connect();self.changed()

    def enter_subflow(self,nid):
        n=self.node_by_id(nid)
        if not n or n.get("type")!="SUBFLOW":return
        fid=n.get("subflow_id")
        if not self.flow_by_id(fid):return
        self.flow_stack.append(self.current_flow);self.current_flow=fid;self.selected_node=None;self.selected_transition=None;self.refresh_all();self.center_graph()
    def back_flow(self):
        if self.flow_stack:self.current_flow=self.flow_stack.pop();self.selected_node=None;self.selected_transition=None;self.refresh_all();self.center_graph()

    def refresh_all(self):
        self.refresh_tree();self.redraw();self.refresh_props();f=self.flow_by_id(self.current_flow)
        self.breadcrumb.configure(text=f"{self.flow.get('name','GAME')} / {(f or {}).get('name',self.current_flow)}")

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        root=self.tree.insert("","end",iid="game",text=self.flow.get("name","GAME"),open=True)
        node_by_flow={f["id"]:[] for f in self.flow.get("flows",[])}
        for n in self.flow["nodes"]:node_by_flow.setdefault(n.get("flow_id","MAIN"),[]).append(n)
        subflow_owner={n.get("subflow_id"):n for n in self.flow["nodes"] if n.get("type")=="SUBFLOW"}
        def add_flow(fid,parent,visited):
            if fid in visited:return
            visited=visited|{fid};f=self.flow_by_id(fid);label=(f or {}).get("name",fid);entry=(f or {}).get("entry_state","")
            item=self.tree.insert(parent,"end",iid="flow|"+fid,text=f"{label}  [entrée: {entry or '-'}]",open=True)
            for n in node_by_flow.get(fid,[]):
                ni=self.tree.insert(item,"end",iid="node|"+n["id"],text=f"{n['name']}  ({n['type']})")
                if n.get("type")=="SUBFLOW" and n.get("subflow_id"):add_flow(n["subflow_id"],ni,visited)
        add_flow(self.flow.get("main_flow","MAIN"),root,set())
        # orphan flows are still visible
        for f in self.flow.get("flows",[]):
            if f["id"]!=self.flow.get("main_flow","MAIN") and f["id"] not in subflow_owner:add_flow(f["id"],root,set())

    def redraw(self):
        self.canvas.delete("all");self._draw_grid()
        current=[n for n in self.flow["nodes"] if n.get("flow_id","MAIN")==self.current_flow]
        ids={n["id"] for n in current};pos={n["id"]:(float(n.get("x",0)),float(n.get("y",0))) for n in current}
        for t in self.flow["transitions"]:
            if t.get("source") not in ids:continue
            if t.get("destination") not in ids:continue
            x1,y1=pos[t["source"]];x2,y2=pos[t["destination"]]
            line=self.canvas.create_line(x1+90,y1+30,x2-90,y2+30,fill="#8c9aa7",width=2,arrow="last",smooth=True,tags=("edge","edge|"+t["id"]))
            mx=(x1+x2)/2;my=(y1+y2)/2-10
            self.canvas.create_text(mx,my,text=f"{t.get('event','AUTO')}  →",fill="#b9c4ce",font=("Segoe UI",8),tags=("edge","edge|"+t["id"]))
        for n in current:self.draw_node(n)
        if self.scale != 1.0:
            self.canvas.scale("all", 0, 0, self.scale, self.scale)

    def _draw_grid(self):
        step=80
        for x in range(-3000,3001,step):self.canvas.create_line(x,-3000,x,3000,fill="#141a20",tags="grid")
        for y in range(-3000,3001,step):self.canvas.create_line(-3000,y,3000,y,fill="#141a20",tags="grid")

    def draw_node(self,n):
        x=float(n.get("x",0));y=float(n.get("y",0));typ=n.get("type","SCREEN");sel=n["id"]==self.selected_node
        fill=TYPE_COLORS.get(typ,"#444");outline="#f2d06b" if sel else "#111";w=3 if sel else 1
        tags=("node","node|"+n["id"])
        self.canvas.create_rectangle(x-90,y-8,x+90,y+68,fill=fill,outline=outline,width=w,tags=tags)
        self.canvas.create_text(x,y+13,text=n.get("name",n["id"]),fill="white",font=("Segoe UI",10,"bold"),tags=tags)
        self.canvas.create_text(x,y+36,text=typ,fill="#d8e0e6",font=("Segoe UI",8),tags=tags)
        f=self.flow_by_id(self.current_flow)
        if f and f.get("entry_state")==n["id"]:self.canvas.create_text(x-78,y+54,text="ENTRY",anchor="w",fill="#ffe18b",font=("Segoe UI",7,"bold"),tags=tags)
        if typ=="SUBFLOW":self.canvas.create_text(x+78,y+54,text="↳",anchor="e",fill="white",font=("Segoe UI",12,"bold"),tags=tags)

    def canvas_item_id(self,event,prefix):
        items=self.canvas.find_overlapping(self.canvas.canvasx(event.x)-2,self.canvas.canvasy(event.y)-2,self.canvas.canvasx(event.x)+2,self.canvas.canvasy(event.y)+2)
        for item in reversed(items):
            for tag in self.canvas.gettags(item):
                if tag.startswith(prefix+"|"):return tag.split("|",1)[1]
        return None

    def canvas_click(self,e):
        nid=self.canvas_item_id(e,"node")
        if nid:
            if self.mode_label.cget("text").startswith("Connexion"):
                if self.connect_source is None:self.connect_source=nid;self.mode_label.configure(text=f"Connexion : {nid} → cliquez destination")
                else:self.connect_nodes(self.connect_source,nid)
                return
            self.selected_node=nid;self.selected_transition=None;n=self.node_by_id(nid)
            self.drag_node=nid;self.drag_snapshot_taken=False;self.drag_dx=self.canvas.canvasx(e.x)/self.scale-float(n.get("x",0));self.drag_dy=self.canvas.canvasy(e.y)/self.scale-float(n.get("y",0));self.refresh_all();return
        tid=self.canvas_item_id(e,"edge")
        if tid:self.selected_transition=tid;self.selected_node=None;self.refresh_all();return
        self.selected_node=None;self.selected_transition=None;self.refresh_props()

    def canvas_drag(self,e):
        if not self.drag_node:return
        n=self.node_by_id(self.drag_node)
        if not n:return
        nx=int(self.canvas.canvasx(e.x)/self.scale-self.drag_dx);ny=int(self.canvas.canvasy(e.y)/self.scale-self.drag_dy)
        if nx==int(n.get("x",0)) and ny==int(n.get("y",0)):return
        if not self.drag_snapshot_taken:self.snapshot();self.drag_snapshot_taken=True
        n["x"]=nx;n["y"]=ny;self.redraw()
    def canvas_release(self,e):
        if self.drag_node and self.drag_snapshot_taken:self.changed(redraw=False)
        self.drag_node=None;self.drag_snapshot_taken=False
    def canvas_double(self,e):
        nid=self.canvas_item_id(e,"node")
        if nid:self.enter_subflow(nid)
    def canvas_context(self,e):
        m=tk.Menu(self,tearoff=False)
        for typ in ("SCREEN","MENU","GAME","CUTSCENE"):m.add_command(label="Ajouter "+typ,command=lambda t=typ:self.add_node(t))
        m.add_command(label="Ajouter SUBFLOW",command=self.add_subflow);m.add_separator();m.add_command(label="Supprimer sélection",command=self.delete_selected)
        m.tk_popup(e.x_root,e.y_root)
    def canvas_wheel(self,e):
        if e.state & 0x0004:self.zoom(1.1 if e.delta>0 else 0.9)
        else:self.canvas.yview_scroll(-1 if e.delta>0 else 1,"units")
    def zoom(self,factor):
        xv=self.canvas.xview();yv=self.canvas.yview();self.scale=max(0.55,min(1.8,self.scale*factor))
        self.redraw();self.center_graph(reposition=False)
        if xv:self.canvas.xview_moveto(xv[0])
        if yv:self.canvas.yview_moveto(yv[0])
        self.status.configure(text=f"Zoom {int(self.scale*100)} %")
    def center_graph(self,reposition=True):
        nodes=[n for n in self.flow["nodes"] if n.get("flow_id","MAIN")==self.current_flow]
        if not nodes:return
        minx=min(n.get("x",0) for n in nodes)-180;maxx=max(n.get("x",0) for n in nodes)+180;miny=min(n.get("y",0) for n in nodes)-120;maxy=max(n.get("y",0) for n in nodes)+120
        self.canvas.configure(scrollregion=(minx*self.scale,miny*self.scale,maxx*self.scale,maxy*self.scale))
        if reposition:self.canvas.xview_moveto(0.0);self.canvas.yview_moveto(0.0)

    def tree_select(self,e=None):
        s=self.tree.selection()
        if not s:return
        item=s[0]
        if item.startswith("node|"):
            nid=item.split("|",1)[1];n=self.node_by_id(nid)
            if n and n.get("flow_id")!=self.current_flow:
                self.current_flow=n.get("flow_id","MAIN");self.flow_stack=[]
            self.selected_node=nid;self.selected_transition=None;self.refresh_all()
        elif item.startswith("flow|"):
            self.current_flow=item.split("|",1)[1];self.selected_node=None;self.selected_transition=None;self.refresh_all();self.center_graph()
    def tree_double(self,e=None):
        s=self.tree.selection()
        if s and s[0].startswith("node|"):self.enter_subflow(s[0].split("|",1)[1])

    def refresh_props(self):
        if self.selected_node:
            n=self.node_by_id(self.selected_node)
            if n:self._show_node_props(n);return
        if self.selected_transition:
            t=self.trans_by_id(self.selected_transition)
            if t:self._show_transition_props(t);return
        self._show_none_props()

    def apply_node(self):
        n=self.node_by_id(self.selected_node) if self.selected_node else None
        if not n:return
        self.snapshot();old=n["id"];new=clean_id(str(self.vars["id"].get()))
        if new!=old and self.node_by_id(new):messagebox.showerror(APP_NAME,"Cet ID existe déjà.");self.undo_stack.pop();return
        for k,v in self.vars.items():
            if k=="stop_music_on_exit":n[k]=bool(v.get())
            elif k in ("video_mode","enter_fx_duration","exit_fx_duration","actor_x","actor_y"):
                try:n[k]=int(v.get())
                except:n[k]=-1 if k=="video_mode" else (152 if k=="actor_x" else (96 if k=="actor_y" else 16))
            else:n[k]=str(v.get()).strip()
        n["id"]=new;n["type"]=str(n.get("type","SCREEN")).upper()
        if new!=old:
            for f in self.flow["flows"]:
                if f.get("entry_state")==old:f["entry_state"]=new
            for t in self.flow["transitions"]:
                if t.get("source")==old:t["source"]=new
                if t.get("destination")==old:t["destination"]=new
            self.selected_node=new
        self.changed()

    def apply_transition(self):
        t=self.trans_by_id(self.selected_transition) if self.selected_transition else None
        if not t:return
        self.snapshot();old=t["id"];new=clean_id(str(self.tvars["id"].get()))
        if new!=old and self.trans_by_id(new):messagebox.showerror(APP_NAME,"Cet ID de transition existe déjà.");self.undo_stack.pop();return
        for k,v in self.tvars.items():
            if k in ("delay_frames","fx_duration","priority"):
                try:t[k]=max(0,int(v.get()))
                except:t[k]=0 if k!="priority" else 100
            else:t[k]=str(v.get()).strip()
        t["id"]=new;t["event"]=clean_id(t.get("event","AUTO"));t["visual_fx"]=str(t.get("visual_fx","NONE")).upper();self.selected_transition=new;self.changed()

    def delete_selected(self):
        if self.selected_node:
            n=self.node_by_id(self.selected_node)
            if not n:return
            if not messagebox.askyesno(APP_NAME,f"Supprimer {n['name']} et ses transitions ?"):return
            self.snapshot();nid=n["id"]
            self.flow["nodes"]=[x for x in self.flow["nodes"] if x.get("id")!=nid]
            self.flow["transitions"]=[t for t in self.flow["transitions"] if t.get("source")!=nid and t.get("destination")!=nid]
            for f in self.flow["flows"]:
                if f.get("entry_state")==nid:f["entry_state"]=""
            if n.get("type")=="SUBFLOW" and n.get("subflow_id"):
                sf=n["subflow_id"]
                if messagebox.askyesno(APP_NAME,"Supprimer aussi le contenu du sous-flow ?"):
                    child_ids={x["id"] for x in self.flow["nodes"] if x.get("flow_id")==sf};self.flow["nodes"]=[x for x in self.flow["nodes"] if x.get("flow_id")!=sf]
                    self.flow["transitions"]=[t for t in self.flow["transitions"] if t.get("source") not in child_ids and t.get("destination") not in child_ids]
                    self.flow["flows"]=[f for f in self.flow["flows"] if f.get("id")!=sf]
            self.selected_node=None;self.changed();return
        if self.selected_transition:
            if not messagebox.askyesno(APP_NAME,"Supprimer cette transition ?"):return
            self.snapshot();tid=self.selected_transition;self.flow["transitions"]=[t for t in self.flow["transitions"] if t.get("id")!=tid];self.selected_transition=None;self.changed()

    def confirm_discard(self):
        if not self.dirty:return True
        ans=messagebox.askyesnocancel(APP_NAME,"Le flow contient des modifications non sauvées.\n\nSauver avant de continuer ?")
        if ans is None:return False
        if ans:self.save();return not self.dirty
        return True
    def close_app(self):
        if self.confirm_discard():self.destroy()

    def new(self):
        if not self.confirm_discard():return
        if not messagebox.askyesno(APP_NAME,"Créer un nouveau flow ?"):return
        self.flow=core.new_flow("GAME");self.path=None;self.current_flow="MAIN";self.flow_stack=[];self.undo_stack=[];self.redo_stack=[];self.dirty=False;self.add_node("SCREEN",nid="BOOT",record=False);self._set_entry("BOOT");self.refresh_all()
    def open(self):
        if not self.confirm_discard():return
        p=filedialog.askopenfilename(title="Ouvrir DFLOW",filetypes=[("DMS Game Flow","*.dflow"),("Tous","*.*")])
        if p:self.load_path(Path(p))
    def load_path(self,p:Path):
        try:self.flow=core.load_flow(p);self.path=p;self.current_flow=self.flow.get("main_flow","MAIN");self.flow_stack=[];self.undo_stack=[];self.redo_stack=[];self.selected_node=None;self.selected_transition=None;self.dirty=False;self.refresh_all();self.center_graph();self.status.configure(text=f"Ouvert : {p}")
        except Exception as exc:messagebox.showerror(APP_NAME,str(exc))
    def save(self):
        target=self.path
        if not target:
            p=filedialog.asksaveasfilename(title="Sauver DFLOW",defaultextension=".dflow",filetypes=[("DMS Game Flow","*.dflow")])
            if not p:return
            target=Path(p)
        try:core.save_flow(target,self.flow);self.path=target;self.dirty=False;self.status.configure(text=f"Sauvé : {self.path}")
        except Exception as exc:messagebox.showerror(APP_NAME,str(exc))

    def validate_ui(self):
        source=self.path
        issues=core.validate_flow(self.flow,source)
        text=core.format_diagnostics(issues)
        if any(x.severity=="ERROR" for x in issues):messagebox.showerror("Validation DFLOW",text)
        elif issues:messagebox.showwarning("Validation DFLOW",text)
        else:messagebox.showinfo("Validation DFLOW",text)
        self.status.configure(text=f"Validation : {sum(x.severity=='ERROR' for x in issues)} erreur(s), {sum(x.severity=='WARN' for x in issues)} avertissement(s)")

    def export_gdk(self):
        if not self.path:self.save()
        if not self.path:return
        issues=core.validate_flow(self.flow,self.path)
        if any(x.severity=="ERROR" for x in issues):messagebox.showerror("Export impossible",core.format_diagnostics(issues));return
        project=self.path.parent.parent if self.path.parent.name.lower()=="res" else self.path.parent
        default=project/"src"
        out=filedialog.askdirectory(title="Dossier source GDK pour game_flow.c/.h",initialdir=str(default if default.exists() else project))
        if not out:return
        try:
            core.save_flow(self.path,self.flow);paths=core.compile_flow(self.flow,Path(out),self.path,"game_flow")
            # Mirror BIN/manifest to build/generated when exporting into project/src.
            gen=project/"build"/"generated";gen.mkdir(parents=True,exist_ok=True)
            (gen/"game_flow_data.bin").write_bytes(paths["bin"].read_bytes())
            (gen/"game_flow_manifest.json").write_bytes(paths["manifest"].read_bytes())
            self.status.configure(text="Export GDK PASS")
            messagebox.showinfo(APP_NAME,"Export PASS\n\n"+"\n".join(str(p) for p in paths.values()))
        except Exception as exc:messagebox.showerror(APP_NAME,str(exc))


def _write_startup_error(exc: BaseException) -> Path:
    import traceback
    log = HERE.parent / "DMS_GAME_FLOW_BUILDER_STARTUP_ERROR.log"
    try:
        log.write_text(
            "DMS Game Flow Builder - erreur de démarrage\n\n" +
            "Python: " + sys.version + "\n" +
            "Script: " + str(HERE) + "\n" +
            "Core: " + str(CORE_PATH) + "\n\n" +
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
    except Exception:
        pass
    return log

def main() -> int:
    ap=argparse.ArgumentParser(add_help=False);ap.add_argument("path",nargs="?");args,_=ap.parse_known_args()
    p=Path(args.path).resolve() if args.path else None
    try:
        FlowApp(p).mainloop()
        return 0
    except Exception as exc:
        log = _write_startup_error(exc)
        print(f"DMS Game Flow Builder : erreur de démarrage : {exc}", file=sys.stderr)
        print(f"Journal : {log}", file=sys.stderr)
        try:
            root=tk.Tk();root.withdraw()
            messagebox.showerror(APP_NAME, f"Le Builder n'a pas pu démarrer.\n\n{exc}\n\nJournal :\n{log}")
            root.destroy()
        except Exception:
            pass
        return 1

if __name__=="__main__":raise SystemExit(main())
