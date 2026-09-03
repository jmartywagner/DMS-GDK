#!/usr/bin/env python3
from __future__ import annotations
import json,os,queue,subprocess,sys,threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox,simpledialog
HERE=Path(__file__).resolve(); ROOT=HERE.parents[2]

def discover_tools():
    out=[]; errors=[]
    for mf in (ROOT/'TOOLS').rglob('dms_tool.json'):
        try:
            d=json.loads(mf.read_text(encoding='utf-8')); launcher=mf.parent/str(d['launcher'])
            if d.get('schema')!='dms-tool-v1':
                errors.append(f'{mf}: schema invalide'); continue
            if d.get('active',True):
                if launcher.exists(): out.append((str(d.get('name') or mf.parent.name),launcher))
                else: errors.append(f'{mf}: launcher introuvable : {launcher.name}')
        except Exception as exc: errors.append(f'{mf}: {exc}')
    return sorted(out,key=lambda x:x[0].lower()),errors

def discover_projects():
    out=[]
    for base in (ROOT/'PROJECTS',ROOT/'SAMPLES'):
        if not base.is_dir(): continue
        out.extend(p for p in base.iterdir() if p.is_dir() and (p/'src/main.c').is_file())
    return sorted(out,key=lambda p:p.name.lower())

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title('DAC MASTER - DMS-GDK'); self.geometry('1040x760'); self.minsize(900,640)
        projects=discover_projects(); self.project=tk.StringVar(value=str(projects[0]) if projects else str(ROOT/'PROJECTS')); self.status=tk.StringVar(value='')
        self._events=queue.Queue(); self._busy=False; self._ui(projects); self.after(50,self._pump_events)
    def _ui(self,projects):
        ttk.Label(self,text='DMS-GDK',font=('Segoe UI',20,'bold')).pack(anchor='w',padx=18,pady=(16,10))
        pf=ttk.LabelFrame(self,text='Projet',padding=10); pf.pack(fill='x',padx=18,pady=6)
        self.combo=ttk.Combobox(pf,textvariable=self.project,values=[str(p) for p in projects]); self.combo.pack(side='left',fill='x',expand=True)
        ttk.Button(pf,text='Choisir dossier projet…',command=self.choose).pack(side='left',padx=6); ttk.Button(pf,text='Nouveau projet…',command=self.new_project).pack(side='left')
        actions=ttk.Frame(self); actions.pack(fill='x',padx=18,pady=8)
        for text,act in [('VALIDER','validate'),('BUILD ROM','build'),('BUILD + RUN','build-run'),('RUN ROM EXISTANTE','run')]: ttk.Button(actions,text=text,command=lambda a=act:self.do(a)).pack(side='left',padx=(0,8))
        ttk.Button(actions,text='Ouvrir dossier',command=self.open_project).pack(side='left')
        tf=ttk.LabelFrame(self,text='Outils DMS-1',padding=10); tf.pack(fill='x',padx=18,pady=6)
        tools,errors=discover_tools()
        for i,(name,path) in enumerate(tools): ttk.Button(tf,text=name,command=lambda p=path:self.launch(p)).grid(row=i//3,column=i%3,sticky='ew',padx=4,pady=4)
        for c in range(3): tf.columnconfigure(c,weight=1)
        lf=ttk.LabelFrame(self,text='Journal',padding=8); lf.pack(fill='both',expand=True,padx=18,pady=8); self.log=tk.Text(lf,height=15,wrap='word',state='disabled'); self.log.pack(fill='both',expand=True)
        b=ttk.Frame(self); b.pack(fill='x',padx=18,pady=(0,12)); ttk.Label(b,text='© Jonathan Marty-Wagner 2026').pack(side='left'); ttk.Label(b,textvariable=self.status).pack(side='left',padx=(18,0)); ttk.Button(b,text='DMS Doctor',command=lambda:self.launch(ROOT/'ADMIN/DMS_DOCTOR.bat')).pack(side='right')
        if errors:
            self.write('Découverte outils :')
            for err in errors:self.write('  AVERTISSEMENT : '+err)
    def _pump_events(self):
        try:
            while True:
                kind,payload=self._events.get_nowait()
                if kind=='write': self.write(payload)
                elif kind=='status': self.status.set(payload)
                elif kind=='project': self.project.set(payload)
                elif kind=='busy': self._busy=bool(payload)
                elif kind=='error': messagebox.showerror('DMS-GDK',payload)
        except queue.Empty: pass
        self.after(50,self._pump_events)
    def _post(self,kind,payload): self._events.put((kind,payload))
    def write(self,s): self.log.configure(state='normal'); self.log.insert('end',str(s).rstrip()+'\n'); self.log.see('end'); self.log.configure(state='disabled')
    def project_path(self):
        p=Path(self.project.get()).resolve()
        if not p.is_dir() or not (p/'src/main.c').is_file(): raise FileNotFoundError('Dossier projet GCC invalide (src/main.c absent)')
        return p
    def choose(self):
        d=filedialog.askdirectory(title='Choisir le dossier du projet DMS-1',initialdir=str(ROOT/'PROJECTS'))
        if d:
            p=Path(d)
            if not (p/'src/main.c').is_file(): messagebox.showerror('DMS-GDK','src/main.c absent dans ce dossier.'); return
            self.project.set(str(p)); self.status.set('Projet : '+p.name)
    @staticmethod
    def _hidden_flags():
        return subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    def launch(self,path):
        p=Path(path)
        if not p.exists(): messagebox.showerror('DMS-GDK',f'Introuvable :\n{p}'); return
        if p.is_dir(): os.startfile(str(p)); return
        try:
            suffix=p.suffix.lower()
            if os.name=='nt' and suffix in {'.bat','.cmd'}:
                helper=ROOT/'GDK/launcher/dms_hidden_run.vbs'
                if not helper.is_file():
                    raise FileNotFoundError(f'Lanceur silencieux absent : {helper}')
                subprocess.Popen(
                    ['wscript.exe','//nologo',str(helper),str(p)],
                    cwd=str(p.parent), creationflags=self._hidden_flags(),
                )
            elif os.name=='nt' and suffix in {'.py','.pyw'}:
                # Python GUI direct : pythonw évite toute console sans modifier le script.
                pyw=Path(sys.executable).with_name('pythonw.exe')
                exe=str(pyw if pyw.is_file() else sys.executable)
                subprocess.Popen([exe,str(p)],cwd=str(p.parent),creationflags=self._hidden_flags())
            else:
                subprocess.Popen([str(p)],cwd=str(p.parent))
        except Exception as exc:
            messagebox.showerror('DMS-GDK',f'Impossible de lancer {p.name} :\n{exc}')
    def _start_worker(self,fn):
        if self._busy: messagebox.showinfo('DMS-GDK','Une opération DMS est déjà en cours.'); return False
        self._busy=True
        def run():
            try: fn()
            finally: self._post('busy',False)
        threading.Thread(target=run,daemon=True).start(); return True
    def do(self,action):
        try:p=self.project_path()
        except Exception as e: messagebox.showerror('DMS-GDK',str(e)); return
        def worker():
            cmd=[sys.executable,str(ROOT/'GDK/tools/dms_project_runner.py'),str(p),action]; self._post('status','Travail en cours…'); self._post('write','> '+' '.join(cmd))
            try:
                cp=subprocess.run(cmd,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',creationflags=self._hidden_flags()); self._post('write',cp.stdout or '(aucune sortie)'); self._post('status','PASS' if cp.returncode==0 else f'ERREUR {cp.returncode}')
            except Exception as e:self._post('write','ERREUR : '+str(e)); self._post('status','ERREUR')
        self._start_worker(worker)
    def open_project(self):
        try: os.startfile(str(self.project_path()))
        except Exception as e: messagebox.showerror('DMS-GDK',str(e))
    def new_project(self):
        name=simpledialog.askstring('Nouveau projet','Nom du projet :',parent=self)
        if not name:return
        safe=''.join(c if c.isalnum() or c in '_-' else '_' for c in name).strip('_') or 'MY_GAME'; dest=ROOT/'PROJECTS'/safe
        if dest.exists(): messagebox.showerror('DMS-GDK','Le dossier existe deja.'); return
        def worker():
            self._post('status','Création du projet…')
            try:
                cp=subprocess.run([sys.executable,str(ROOT/'GDK/tools/dmsnew.py'),safe],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',creationflags=self._hidden_flags()); self._post('write',cp.stdout or '(aucune sortie)')
                if cp.returncode==0:self._post('project',str(dest));self._post('status','Projet cree')
                else:self._post('status',f'ERREUR {cp.returncode}')
            except Exception as exc:self._post('write','ERREUR : '+str(exc));self._post('status','ERREUR')
        self._start_worker(worker)
if __name__=='__main__': App().mainloop()
