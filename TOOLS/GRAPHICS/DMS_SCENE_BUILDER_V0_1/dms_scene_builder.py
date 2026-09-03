#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, copy, json, os, sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import dms_scene_core as core

TITLE="DAC MASTER - DMS Scene Builder - V1.1.1"
EVENTS=list(core.OPS)

def relpath_for(scene_path:Path|None,p:Path)->str:
    if not scene_path: return str(p.resolve())
    try: return str(p.resolve().relative_to(scene_path.parent.resolve())).replace('\\','/')
    except Exception:
        try:
            import os; return os.path.relpath(p.resolve(),scene_path.parent.resolve()).replace('\\','/')
        except Exception: return str(p.resolve())

def rgb333(v:int)->str:
    r=(v>>6)&7; g=(v>>3)&7; b=v&7
    return '#%02x%02x%02x'%((r*255)//7,(g*255)//7,(b*255)//7)

class EventDialog(tk.Toplevel):
    def __init__(self,parent,objects,event=None):
        super().__init__(parent); self.title('Événement timeline'); self.resizable(False,False); self.result=None
        e=event or {"frame":0,"op":"SHOW"}; self.vars={}
        fields=[('frame','Frame'),('op','Opération'),('target','Objet'),('fx','FX'),('x','X / vitesse X'),('y','Y / vitesse Y'),('a_x','Scroll A X'),('a_y','Scroll A Y'),('b_x','Scroll B X'),('b_y','Scroll B Y'),('count','Nb formation'),('spacing_x','Espacement X'),('spacing_y','Espacement Y'),('mode','Mode vidéo'),('trigger','Trigger/Event'),('speed','Vitesse'),('offset','Offset'),('duration','Durée'),('intensity','Intensité'),('secondary','Secondaire'),('palette_mask','Palette mask'),('ref','ID ressource'),('wait','Wait')]
        for r,(k,label) in enumerate(fields):
            ttk.Label(self,text=label).grid(row=r,column=0,sticky='w',padx=8,pady=3)
            v=tk.StringVar(value=str(e.get(k,''))); self.vars[k]=v
            if k=='op': w=ttk.Combobox(self,textvariable=v,values=EVENTS,state='readonly',width=24)
            elif k=='target': w=ttk.Combobox(self,textvariable=v,values=objects,width=24)
            elif k=='fx': w=ttk.Combobox(self,textvariable=v,values=core.FX_ORDER[1:],width=24)
            elif k=='wait': w=ttk.Combobox(self,textvariable=v,values=list(core.WAIT_BITS),width=24)
            else: w=ttk.Entry(self,textvariable=v,width=27)
            w.grid(row=r,column=1,padx=8,pady=3)
        b=ttk.Frame(self);b.grid(row=len(fields),column=0,columnspan=2,pady=10)
        ttk.Button(b,text='OK',command=self.ok).pack(side='left',padx=4);ttk.Button(b,text='Annuler',command=self.destroy).pack(side='left',padx=4)
        self.transient(parent); self.grab_set(); self.wait_window(self)
    def ok(self):
        try:
            out={"frame":int(self.vars['frame'].get() or 0),"op":self.vars['op'].get() or 'SHOW'}
            for k in ('target','fx','wait','trigger'):
                if self.vars[k].get(): out[k]=self.vars[k].get()
            for k in ('x','y','a_x','a_y','b_x','b_y','count','spacing_x','spacing_y','mode','speed','offset','duration','intensity','secondary','palette_mask','ref'):
                if self.vars[k].get()!='': out[k]=int(self.vars[k].get(),0)
            self.result=out; self.destroy()
        except Exception as ex: messagebox.showerror('DMS Scene Builder',str(ex),parent=self)

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title(TITLE); self.geometry('1500x940'); self.minsize(1180,760)
        self.scene=core.default_scene(); self.path:Path|None=None; self.dirty=False; self.selected_id=None
        self.preview_frame=0; self.playing=False; self.bg_photo=None; self.font_cache=None;self.layer_photos=[];self.object_photos=[]
        self.status=tk.StringVar(value='Prêt - hardware DMS-1 strict'); self.frame_var=tk.StringVar(value='Frame 0')
        self._build(); self.protocol("WM_DELETE_WINDOW",self.close_app); self.refresh_all()
    def _build(self):
        top=ttk.Frame(self,padding=6); top.pack(fill='x')
        for text,cmd in [('Nouveau',self.new),('Ouvrir',self.open),('Sauver',self.save),('Sauver sous',self.save_as),('Valider HW',self.validate_scene),('EXPORT GDK',self.export),('▶ Preview',self.play),('■ Stop',self.stop)]: ttk.Button(top,text=text,command=cmd).pack(side='left',padx=3)
        ttk.Label(top,textvariable=self.frame_var).pack(side='right',padx=10)
        main=ttk.PanedWindow(self,orient='horizontal'); main.pack(fill='both',expand=True,padx=6,pady=(0,5))
        # Left objects/layers
        left=ttk.Frame(main,width=260); main.add(left,weight=0)
        ttk.Label(left,text='OBJETS / LAYERS',font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(2,6))
        self.tree=ttk.Treeview(left,show='tree',selectmode='browse'); self.tree.pack(fill='both',expand=True); self.tree.bind('<<TreeviewSelect>>',self.on_select)
        lb=ttk.Frame(left);lb.pack(fill='x',pady=5)
        for text,cmd in [('+ Texte / UI',self.add_text),('+ Menu',self.add_menu),('+ Sprite DRES',self.add_sprite),('+ Acteur DACTOR',self.add_actor),('+ Atmosphère',self.add_atmosphere),('+ Avant joueur',self.add_foreground),('+ BG DIMG V1',self.add_bg),('Suppr.',self.delete_obj)]: ttk.Button(lb,text=text,command=cmd).pack(fill='x',pady=1)
        ttk.Separator(left).pack(fill='x',pady=4)
        ttk.Button(left,text='Importer FONT DRES',command=self.import_font).pack(fill='x',pady=2)
        # Center preview
        center=ttk.Frame(main); main.add(center,weight=1)
        hdr=ttk.Frame(center);hdr.pack(fill='x');ttk.Label(hdr,text='APERÇU HARDWARE',font=('Segoe UI',11,'bold')).pack(side='left');self.mode_lbl=ttk.Label(hdr,text='');self.mode_lbl.pack(side='right')
        self.canvas=tk.Canvas(center,width=640,height=448,bg='black',highlightthickness=1,highlightbackground='#555');self.canvas.pack(anchor='center',pady=8)
        ttk.Label(center,text='Preview pixel ×2 - aucune capacité PC ajoutée').pack()
        # Right properties
        right=ttk.Frame(main,width=300);main.add(right,weight=0)
        ttk.Label(right,text='PROPRIÉTÉS',font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(2,6))
        scene_box=ttk.LabelFrame(right,text='Scene',padding=6);scene_box.pack(fill='x')
        self.scene_vars={}
        for k,label,vals in [('name','Nom',None),('type','Type',['SCREEN','MENU','GAMEPLAY','CUTSCENE','BOSS','OVERLAY']),('video_mode','Mode',[0,1,2,3,4]),('map','DMAP / symbole',None),('menu_move_sfx','SFX move',None),('menu_validate_sfx','SFX valid',None)]:
            row=ttk.Frame(scene_box);row.pack(fill='x',pady=2);ttk.Label(row,text=label,width=12).pack(side='left');v=tk.StringVar();self.scene_vars[k]=v
            w=ttk.Combobox(row,textvariable=v,values=vals,width=19,state='readonly' if vals else 'normal') if vals else ttk.Entry(row,textvariable=v,width=22)
            w.pack(side='left',fill='x',expand=True);w.bind('<FocusOut>',lambda _e:self.apply_scene());w.bind('<<ComboboxSelected>>',lambda _e:self.apply_scene())
        ttk.Button(scene_box,text='Choisir DMAP relatif…',command=self.choose_map).pack(fill='x',pady=(4,0))
        scroll=ttk.LabelFrame(right,text='Scroll / frame',padding=6);scroll.pack(fill='x',pady=5)
        self.scroll_vars={}
        for k in ('a_x','a_y','b_x','b_y'):
            r=ttk.Frame(scroll);r.pack(fill='x');ttk.Label(r,text=k.upper(),width=8).pack(side='left');v=tk.StringVar();self.scroll_vars[k]=v;ent=ttk.Entry(r,textvariable=v,width=8);ent.pack(side='left');ent.bind('<FocusOut>',lambda _e:self.apply_scene())
        self.parallax_vars={}
        for k in ('a_x','a_y','b_x','b_y'):
            r=ttk.Frame(scroll);r.pack(fill='x');ttk.Label(r,text='PAR '+k.upper(),width=8).pack(side='left');v=tk.StringVar();self.parallax_vars[k]=v;ent=ttk.Entry(r,textvariable=v,width=8);ent.pack(side='left');ent.bind('<FocusOut>',lambda _e:self.apply_scene())
        self.camera_vars={}
        for k in ('x','y','speed_x','speed_y'):
            r=ttk.Frame(scroll);r.pack(fill='x');ttk.Label(r,text='CAM '+k.upper(),width=8).pack(side='left');v=tk.StringVar();self.camera_vars[k]=v;ent=ttk.Entry(r,textvariable=v,width=8);ent.pack(side='left');ent.bind('<FocusOut>',lambda _e:self.apply_scene())
        self.prop_box=ttk.LabelFrame(right,text='Objet sélectionné',padding=6);self.prop_box.pack(fill='both',expand=True)
        self.prop_vars={}; self.prop_widgets=[]
        ttk.Label(right,textvariable=self.status,wraplength=290).pack(fill='x',pady=5)
        # Bottom timeline
        bottom=ttk.Frame(self,padding=(6,0,6,6));bottom.pack(fill='x')
        head=ttk.Frame(bottom);head.pack(fill='x');ttk.Label(head,text='TIMELINE',font=('Segoe UI',11,'bold')).pack(side='left')
        for text,cmd in [('+ Event',self.add_event),('Éditer',self.edit_event),('Dupliquer',self.dup_event),('Suppr.',self.del_event),('◀ frame',lambda:self.step(-1)),('frame ▶',lambda:self.step(1))]:ttk.Button(head,text=text,command=cmd).pack(side='left',padx=3)
        self.timeline=tk.Canvas(bottom,height=90,bg='#17191c',highlightthickness=1,highlightbackground='#555');self.timeline.pack(fill='x',pady=4)
        cols=('frame','op','target','params');self.events=ttk.Treeview(bottom,columns=cols,show='headings',height=5)
        for c,w in zip(cols,(70,150,150,700)):self.events.heading(c,text=c.upper());self.events.column(c,width=w,stretch=(c=='params'))
        self.events.pack(fill='x'); self.events.bind('<Double-1>',lambda _e:self.edit_event())
    def mark(self): self.dirty=True; self.title(TITLE+' *')
    def confirm_discard(self):
        if not self.dirty:return True
        ans=messagebox.askyesnocancel('DMS Scene Builder','La scène contient des modifications non sauvées.\n\nSauver avant de continuer ?',parent=self)
        if ans is None:return False
        if ans:
            self.save()
            return not self.dirty
        return True
    def close_app(self):
        if self.confirm_discard():self.destroy()
    def _rebase_paths(self,old_path,new_path):
        def resolve_old(value):
            if not value:return value
            pv=Path(str(value))
            if pv.is_absolute():return pv
            if old_path:return (old_path.parent/pv).resolve()
            return pv.resolve()
        def looks_path(value):
            text=str(value or '')
            return '/' in text or '\\' in text or Path(text).suffix.lower() in {'.dmap','.dimg','.dres','.dactor','.dcoll','.dscene'}
        if looks_path(self.scene.get('map','')):
            self.scene['map']=relpath_for(new_path,resolve_old(self.scene.get('map','')))
        font=self.scene.get('font') or {}
        if looks_path(font.get('source','')):
            font['source']=relpath_for(new_path,resolve_old(font.get('source','')))
        for obj in self.scene.get('objects',[]):
            if looks_path(obj.get('resource','')):
                obj['resource']=relpath_for(new_path,resolve_old(obj.get('resource','')))
    def refresh_all(self):
        for k,v in self.scene_vars.items(): v.set(str(self.scene.get(k,'')))
        sc=self.scene.get('scroll') or {}; [v.set(str(sc.get(k,0))) for k,v in self.scroll_vars.items()]
        par=self.scene.get('parallax') or {};[v.set(str(par.get(k,1.0 if k.startswith('a_') else 0.25))) for k,v in self.parallax_vars.items()]
        cam=self.scene.get('camera') or {};[v.set(str(cam.get(k,0))) for k,v in self.camera_vars.items()]
        info=core.MODE_INFO[int(self.scene.get('video_mode',0))];self.mode_lbl.config(text=f"M{self.scene.get('video_mode')} {info['name']} - {info['width']}×224 - {info['palettes']} palettes")
        self.refresh_tree();self.refresh_events();self.draw_preview();self.draw_timeline()
    def refresh_tree(self):
        sel=self.selected_id; self.tree.delete(*self.tree.get_children()); root=self.tree.insert('', 'end', text=f"SCENE {self.scene.get('name','')}",open=True)
        groups={name:self.tree.insert(root,'end',text=name,open=True) for name in ('BG B','BG A DERRIÈRE','ACTEURS','BG A DEVANT','ATMOSPHÈRE','UI / TEXTE','TRANSITIONS')}
        for o in self.scene.get('objects',[]):
            layer=str(o.get('layer','UI')).upper();key={'BG_B':'BG B','BG_A_BEHIND':'BG A DERRIÈRE','ACTORS':'ACTEURS','BG_A_FRONT':'BG A DEVANT','ATMOSPHERE':'ATMOSPHÈRE','UI':'UI / TEXTE','TRANSITION':'TRANSITIONS'}.get(layer,'UI / TEXTE');par=groups[key];iid=self.tree.insert(par,'end',text=f"{o.get('id')}  [{o.get('kind')}]");self.tree.item(iid,tags=(o.get('id'),))
            if o.get('id')==sel:self.tree.selection_set(iid);self.tree.see(iid)
    def refresh_events(self):
        self.events.delete(*self.events.get_children())
        for i,e in enumerate(sorted(enumerate(self.scene.get('events',[])),key=lambda q:int(q[1].get('frame',0)))):
            original_idx,ev=e; target=ev.get('target','');params=' '.join(f"{k}={v}" for k,v in ev.items() if k not in ('frame','op','target'))
            self.events.insert('', 'end', iid=str(original_idx), values=(ev.get('frame',0),ev.get('op',''),target,params))
    def on_select(self,_e=None):
        s=self.tree.selection()
        if not s:return
        tags=self.tree.item(s[0],'tags'); self.selected_id=tags[0] if tags else None; self.build_props()
    def selected_obj(self): return next((o for o in self.scene.get('objects',[]) if o.get('id')==self.selected_id),None)
    def build_props(self):
        for w in self.prop_widgets:w.destroy()
        self.prop_widgets=[];self.prop_vars={};o=self.selected_obj()
        if not o:return
        keys=['id','kind','layer','resource','resource_id','text','x','y','priority','palette','palette_animation','palette_cadence','palette_span','visible','screen_space','direction','loop','velocity_x','velocity_y','parallax_x','parallax_y','spawn_x','spawn_y','despawn_left','despawn_right','despawn_top','despawn_bottom','animation','cadence','start_frame','end_frame','start_trigger','end_trigger','selected_palette','action','destination','option_type','option_min','option_max','option_step','option_value','plane','sprite_cells']
        for k in keys:
            if k not in o and k not in ('destination',):continue
            r=ttk.Frame(self.prop_box);r.pack(fill='x',pady=1);self.prop_widgets.append(r);ttk.Label(r,text=k,width=17).pack(side='left');v=tk.StringVar(value=str(o.get(k,'')).lower() if isinstance(o.get(k),bool) else str(o.get(k,'')));self.prop_vars[k]=v
            vals={'kind':['TEXT','UI','SPRITE','ACTOR','BOSS','ATMOSPHERE','TRANSITION','BACKGROUND'],'layer':['BG_B','BG_A_BEHIND','ACTORS','BG_A_FRONT','ATMOSPHERE','UI','TRANSITION'],'plane':['A','B'],'direction':['RIGHT','LEFT'],'palette_animation':['NONE','CYCLE'],'priority':['true','false'],'visible':['true','false'],'screen_space':['true','false'],'loop':['true','false'],'option_type':['NONE','LIVES','MUSIC_TEST','SFX_TEST']}.get(k)
            w=ttk.Combobox(r,textvariable=v,values=vals,state='readonly',width=18) if vals else ttk.Entry(r,textvariable=v,width=21)
            w.pack(side='left',fill='x',expand=True);w.bind('<FocusOut>',lambda _e:self.apply_props());w.bind('<<ComboboxSelected>>',lambda _e:self.apply_props())
    def apply_props(self):
        o=self.selected_obj()
        if not o:return
        old=o.get('id'); staged=dict(o)
        int_keys={'x','y','palette','palette_cadence','palette_span','selected_palette','action','option_min','option_max','option_step','option_value','resource_id','spawn_x','spawn_y','despawn_left','despawn_right','despawn_top','despawn_bottom','animation','cadence','start_frame','end_frame','sprite_cells'};float_keys={'velocity_x','velocity_y','parallax_x','parallax_y'};bool_keys={'priority','visible','screen_space','loop'}
        try:
            for k,v in self.prop_vars.items():
                val=v.get();staged[k]=(val.lower()=='true') if k in bool_keys else (float(val.replace(',','.')) if k in float_keys and val!='' else (int(val,0) if k in int_keys and val!='' else val))
        except Exception as exc:
            self.status.set(f'Valeur invalide : {k}'); messagebox.showerror('DMS Scene Builder',f'Valeur invalide pour {k} : {val}\n\n{exc}',parent=self); return
        o.clear();o.update(staged);self.selected_id=o.get('id',old);self.mark();self.refresh_tree();self.draw_preview()
    def apply_scene(self):
        try:
            scene_values={}
            for k,v in self.scene_vars.items():
                val=v.get(); scene_values[k]=int(val,0) if k in ('video_mode','menu_move_sfx','menu_validate_sfx') and val!='' else val
            sc={k:int(v.get(),0) for k,v in self.scroll_vars.items()}
            par={k:float(v.get().replace(',','.')) for k,v in self.parallax_vars.items()}
            cam={k:(float(v.get().replace(',','.')) if k.startswith('speed_') else int(v.get(),0)) for k,v in self.camera_vars.items()}
        except Exception as exc:
            self.status.set('Valeur numérique invalide - modification non appliquée'); return
        self.scene.update(scene_values);self.scene['scroll']=sc;self.scene['parallax']=par;self.scene['camera']=cam;self.mark();self.refresh_all()
    def unique(self,base):
        ids={o.get('id') for o in self.scene.get('objects',[])};i=1;n=base
        while n in ids:i+=1;n=f'{base}_{i}'
        return n
    def add_text(self):
        oid=self.unique('TEXT');self.scene['objects'].append({'id':oid,'kind':'TEXT','layer':'UI','text':'NOUVEAU TEXTE','x':80,'y':80,'palette':3,'selected_palette':2,'priority':True,'visible':True,'screen_space':True,'start_frame':0,'end_frame':0,'start_trigger':'','end_trigger':''});self.selected_id=oid;self.mark();self.refresh_all();self.build_props()
    def add_menu(self):
        oid=self.unique('MENU_ITEM'); action=max([int(o.get('action',0)) for o in self.scene['objects']]+[0])+1;self.scene['objects'].append({'id':oid,'kind':'UI','layer':'UI','text':'OPTION','x':120,'y':120,'palette':3,'selected_palette':2,'priority':True,'visible':True,'screen_space':True,'action':action,'destination':f'FLOW_{oid}','start_frame':0,'end_frame':0});self.selected_id=oid;self.mark();self.refresh_all();self.build_props()
    def ensure_saved(self):
        if self.path:return True
        self.save_as();return self.path is not None
    def choose_map(self):
        if not self.ensure_saved():return
        p=filedialog.askopenfilename(title='Choisir DMAP V2',filetypes=[('DMS Map','*.dmap')]);
        if p:self.scene['map']=relpath_for(self.path,Path(p));self.mark();self.refresh_all()
    def add_runtime_object(self,kind,title,ext,layer):
        if not self.ensure_saved():return
        p=filedialog.askopenfilename(title=title,filetypes=[('Ressource DMS',ext)]);
        if not p:return
        oid=self.unique(kind);base={'id':oid,'kind':kind,'layer':layer,'resource':relpath_for(self.path,Path(p)),'x':160,'y':112,'velocity_x':0.0,'velocity_y':0.0,'parallax_x':1.0,'parallax_y':1.0,'spawn_x':352,'spawn_y':112,'despawn_left':-64,'despawn_right':384,'despawn_top':-64,'despawn_bottom':288,'direction':'RIGHT','loop':False,'animation':0,'cadence':0,'palette':0,'palette_animation':'NONE','palette_cadence':8,'palette_span':1,'priority':layer in ('BG_A_FRONT','UI','TRANSITION'),'visible':True,'screen_space':layer in ('UI','TRANSITION'),'start_frame':0,'end_frame':0,'start_trigger':'','end_trigger':'','sprite_cells':4}
        self.scene['objects'].append(base);self.selected_id=oid;self.mark();self.refresh_all();self.build_props()
    def add_sprite(self):self.add_runtime_object('SPRITE','Charger sprite DRES','*.dres','ACTORS')
    def add_actor(self):self.add_runtime_object('ACTOR','Charger acteur DACTOR','*.dactor','ACTORS')
    def add_atmosphere(self):
        self.add_runtime_object('ATMOSPHERE','Charger élément atmosphérique DRES','*.dres','ATMOSPHERE');o=self.selected_obj()
        if o:o.update({'velocity_x':-0.5,'parallax_x':0.25,'loop':True,'spawn_x':352,'despawn_left':-64,'direction':'LEFT'});self.mark();self.refresh_all();self.build_props()
    def add_foreground(self):
        self.add_runtime_object('SPRITE','Charger élément de premier plan DRES','*.dres','BG_A_FRONT');o=self.selected_obj()
        if o:o.update({'priority':True,'parallax_x':1.0});self.mark();self.refresh_all();self.build_props()
    def add_bg(self):
        if not self.ensure_saved():return
        p=filedialog.askopenfilename(title='Charger background DIMG',filetypes=[('DMS Image','*.dimg')]);
        if not p:return
        oid=self.unique('BG');self.scene['objects'].append({'id':oid,'kind':'BACKGROUND','layer':'BG_B','resource':relpath_for(self.path,Path(p)),'plane':'B'});self.selected_id=oid;self.mark();self.refresh_all();self.build_props()
    def delete_obj(self):
        if not self.selected_id:return
        self.scene['objects']=[o for o in self.scene['objects'] if o.get('id')!=self.selected_id];self.scene['events']=[e for e in self.scene['events'] if e.get('target')!=self.selected_id];self.selected_id=None;self.mark();self.refresh_all();self.build_props()
    def import_font(self):
        if not self.ensure_saved():return
        p=filedialog.askopenfilename(title='Importer font depuis DRES V3',filetypes=[('DMS Resource','*.dres')]);
        if not p:return
        order=simpledialog.askstring('Glyph order','Ordre des frames/glyphes DRES :',initialvalue='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/:.!?+',parent=self)
        if order is None:return
        self.scene['font']={'source':relpath_for(self.path,Path(p)),'glyph_order':order,'palette_ids':[3,2]};self.font_cache=None;self.mark();self.draw_preview()
    def add_event(self):
        d=EventDialog(self,[o.get('id') for o in self.scene['objects'] if o.get('kind')!='BACKGROUND'])
        if d.result:self.scene['events'].append(d.result);self.mark();self.refresh_events();self.draw_timeline();self.draw_preview()
    def selected_event_idx(self):
        s=self.events.selection();return int(s[0]) if s else None
    def edit_event(self):
        idx=self.selected_event_idx();
        if idx is None:return
        d=EventDialog(self,[o.get('id') for o in self.scene['objects'] if o.get('kind')!='BACKGROUND'],copy.deepcopy(self.scene['events'][idx]))
        if d.result:self.scene['events'][idx]=d.result;self.mark();self.refresh_events();self.draw_timeline();self.draw_preview()
    def dup_event(self):
        idx=self.selected_event_idx();
        if idx is None:return
        e=copy.deepcopy(self.scene['events'][idx]);e['frame']=int(e.get('frame',0))+10;self.scene['events'].append(e);self.mark();self.refresh_events();self.draw_timeline()
    def del_event(self):
        idx=self.selected_event_idx();
        if idx is None:return
        self.scene['events'].pop(idx);self.mark();self.refresh_events();self.draw_timeline();self.draw_preview()
    def new(self):
        if not self.confirm_discard():return
        self.scene=core.default_scene();self.path=None;self.preview_frame=0;self.dirty=False;self.title(TITLE);self.refresh_all()
    def open(self):
        if not self.confirm_discard():return
        p=filedialog.askopenfilename(filetypes=[('DMS Scene','*.dscene')]);
        if not p:return
        try:
            self.scene=core.load_scene(Path(p));self.path=Path(p);self.preview_frame=0;self.font_cache=None;self.dirty=False;self.title(TITLE+' - '+self.path.name);self.refresh_all()
            if self.scene.get('_warnings'):messagebox.showinfo('Migration DSCENE','\n'.join(self.scene['_warnings'])+'\n\nUne copie V1 sera créée lors de la première sauvegarde.',parent=self)
        except Exception as e:messagebox.showerror('DMS Scene Builder',str(e))
    def save(self):
        if not self.path:return self.save_as()
        try:core.save_scene(self.path,self.scene);self.scene['_path']=str(self.path.resolve());self.dirty=False;self.title(TITLE+' - '+self.path.name);self.status.set('Sauvé : '+str(self.path))
        except Exception as e:messagebox.showerror('DMS Scene Builder',str(e))
    def save_as(self):
        p=filedialog.asksaveasfilename(defaultextension='.dscene',filetypes=[('DMS Scene','*.dscene')]);
        if not p:return
        new_path=Path(p); old_path=self.path
        staged=copy.deepcopy(self.scene)
        try:
            self._rebase_paths(old_path,new_path); core.save_scene(new_path,self.scene)
            self.path=new_path;self.scene['_path']=str(new_path.resolve());self.dirty=False;self.title(TITLE+' - '+new_path.name);self.status.set('Sauvé : '+str(new_path))
        except Exception as e:
            self.scene=staged; self.path=old_path; messagebox.showerror('DMS Scene Builder',str(e))
    def validate_scene(self):
        if self.path:self.scene['_path']=str(self.path.resolve())
        root=HERE.parents[2]
        d=core.validate(self.scene,root);txt='\n'.join(f'{a}: {b}' for a,b in d);self.status.set(txt.splitlines()[0]);messagebox.showinfo('Validation hardware DMS-1',txt)
    def export(self):
        if not self.path:
            self.save_as()
            if not self.path:return
        self.save(); out=filedialog.askdirectory(title='Dossier export C/H/BIN')
        if not out:return
        try:
            paths=core.export_scene(self.scene,Path(out),HERE.parents[2]);messagebox.showinfo('EXPORT GDK','Export PASS\n'+'\n'.join(str(p) for p in paths.values()));self.status.set('EXPORT GDK PASS')
        except Exception as e:messagebox.showerror('EXPORT GDK',str(e))
    def _bg_image(self):
        bgobj=next((o for o in self.scene.get('objects',[]) if o.get('kind')=='BACKGROUND'),None)
        if not bgobj:return None
        try:bg=core.load_dimg(core.resolve(self.scene,str(bgobj.get('resource',''))))
        except:return None
        pals={pid:bg['palettes'][i*16:(i+1)*16] for i,pid in enumerate(bg['palette_ids'])};tiles=bg['tiles'];w=bg['map_w']*8;h=bg['map_h']*8
        # PhotoImage.put is slower than raw PPM on some Tk builds, but it is
        # portable across the Windows Tk 9 and Linux Tk variants used to
        # validate the GDK.  It also avoids depending on an image codec.
        img=tk.PhotoImage(width=w,height=h)
        for py in range(h):
            ty=py//8;iy=py&7;row=[]
            for px in range(w):
                tx=px//8;ix=px&7;word=bg['map'][ty*bg['map_w']+tx];tid=word&0x3ff;pid=(word>>10)&7;fx=bool(word&0x4000);fy=bool(word&0x8000);x=7-ix if fx else ix;y=7-iy if fy else iy;off=tid*32+y*4+(x>>1);b=tiles[off] if off<len(tiles) else 0;ci=(b&15) if x&1 else (b>>4);v=(pals.get(pid,[0]*16)+[0]*16)[ci];row.append(rgb333(v))
            img.put('{'+ ' '.join(row) +'}',to=(0,py))
        return img.zoom(2,2)
    def _font(self):
        if self.font_cache:return self.font_cache
        try:self.font_cache=core.load_font(self.scene)
        except:self.font_cache=core.builtin_font()
        return self.font_cache
    def object_state(self,o,frame):
        visible=bool(o.get('visible',False));count=len(str(o.get('text','')));x=int(o.get('x',0));menu=False
        for e in sorted(self.scene.get('events',[]),key=lambda q:int(q.get('frame',0))):
            f=int(e.get('frame',0));
            if f>frame:break
            if e.get('op')=='MENU_ENABLE':menu=True
            if e.get('target')!=o.get('id'):continue
            op=e.get('op')
            if op=='SHOW':visible=True;count=len(str(o.get('text','')));x=int(o.get('x',0))
            elif op=='HIDE':visible=False
            elif op=='TYPEWRITER':visible=True;speed=max(1,int(e.get('speed',2)));count=min(len(str(o.get('text',''))),max(0,(frame-f)//speed))
            elif op=='SLIDE_IN':visible=True;dur=max(1,int(e.get('duration',24)));age=min(dur,max(0,frame-f));off=int(e.get('offset',20));x=int(o.get('x',0))+int(round(off*(1-age/dur)))
        return visible,count,x,menu
    def draw_text_bitmap(self,text,x,y,pal):
        font=self._font();tiles=font['tiles'];pals=font['palettes'];fpids=(self.scene.get('font') or {}).get('palette_ids',[3,2]);bank=0 if pal==int(fpids[0]) else 1;p=pals[bank*16:(bank+1)*16] if len(pals)>=32 else pals[:16]
        for ci,ch in enumerate(text):
            code=ord(ch.upper());
            if not 32<=code<128:continue
            raw=tiles[(code-32)*32:(code-31)*32]
            for yy in range(8):
                for xx in range(8):
                    b=raw[yy*4+(xx>>1)];v=(b&15) if xx&1 else (b>>4)
                    if v:
                        col=rgb333((p+[0]*16)[v]);sx=(x+ci)*16+xx*2;sy=y*16+yy*2;self.canvas.create_rectangle(sx,sy,sx+2,sy+2,fill=col,outline='')
    def _pixel_image(self,pixels,w,h):
        img=tk.PhotoImage(width=w,height=h)
        for y in range(h):
            for x in range(w):
                value=pixels[y*w+x]
                if value is not None:img.put(rgb333(value),(x,y))
        return img.zoom(2,2)
    def _draw_runtime_object(self,o,xoff):
        start=int(o.get('start_frame',0) or 0);end=int(o.get('end_frame',0) or 0)
        if self.preview_frame<start or (end and self.preview_frame>=end) or not bool(o.get('visible',True)):return
        vx=float(o.get('velocity_x',0) or 0);vy=float(o.get('velocity_y',0) or 0);age=max(0,self.preview_frame-start);x=float(o.get('x',0))+vx*age;y=float(o.get('y',0))+vy*age
        if o.get('loop'):
            left=float(o.get('despawn_left',-64));right=float(o.get('despawn_right',384));top=float(o.get('despawn_top',-64));bottom=float(o.get('despawn_bottom',288))
            if x<left or x>right:x=float(o.get('spawn_x',o.get('x',0)))
            if y<top or y>bottom:y=float(o.get('spawn_y',o.get('y',0)))
        text=str(o.get('text',''))
        if text:
            vis,count,tx,_=self.object_state(o,self.preview_frame)
            if vis:
                pal=int(o.get('palette',3));span=max(1,int(o.get('palette_span',1) or 1))
                if str(o.get('palette_animation','NONE')).upper() in ('CYCLE','CYCLE_PALETTES') and span>1:pal+=((self.preview_frame//max(1,int(o.get('palette_cadence',8) or 8)))%span)
                self.draw_text_bitmap(text[:count],int(tx)//8+(xoff//16),int(y)//8,pal)
            return
        frame=core.load_object_frame(self.scene,o,self.preview_frame)
        if frame:
            pixels=frame['pixels'];w=frame['width'];h=frame['height']
            if str(o.get('direction','RIGHT')).upper() in ('LEFT','GAUCHE','-1'):
                pixels=[pixels[yy*w+(w-1-xx)] for yy in range(h) for xx in range(w)]
            img=self._pixel_image(pixels,w,h);self.object_photos.append(img);pivot=frame.get('pivot') or [0,0];self.canvas.create_image(xoff+int(x-pivot[0])*2,int(y-pivot[1])*2,image=img,anchor='nw')
        else:
            self.canvas.create_rectangle(xoff+int(x)*2-12,int(y)*2-12,xoff+int(x)*2+12,int(y)*2+12,outline='#ffcc44');self.canvas.create_text(xoff+int(x)*2,int(y)*2,text=str(o.get('kind','?'))[:3],fill='#ffcc44',font=('TkFixedFont',7))
    def draw_preview(self):
        self.canvas.delete('all'); info=core.MODE_INFO[int(self.scene.get('video_mode',0))];active_w=info['width']*2
        self.canvas.create_rectangle(0,0,640,448,fill='black',outline='')
        self.layer_photos=[];self.object_photos=[]
        self.bg_photo=self._bg_image()
        if self.bg_photo:self.canvas.create_image((640-active_w)//2,0,image=self.bg_photo,anchor='nw')
        xoff=(640-active_w)//2
        dmap=core.load_dmap_preview(self.scene)
        if dmap:
            behind=self._pixel_image(dmap['behind'],dmap['width'],dmap['height']);self.layer_photos.append(behind);self.canvas.create_image(xoff,0,image=behind,anchor='nw')
        objs=[o for o in self.scene.get('objects',[]) if o.get('kind')!='BACKGROUND'];order={'BG_B':0,'BG_A_BEHIND':1,'ATMOSPHERE':2,'ACTORS':3,'BG_A_FRONT':5,'UI':6,'TRANSITION':7}
        before=[o for o in objs if order.get(str(o.get('layer','UI')).upper(),6)<5 and not (str(o.get('layer','')).upper()=='ATMOSPHERE' and o.get('priority'))]
        after=[o for o in objs if o not in before]
        for o in sorted(before,key=lambda q:order.get(str(q.get('layer','UI')).upper(),6)):self._draw_runtime_object(o,xoff)
        if dmap:
            front=self._pixel_image(dmap['front'],dmap['width'],dmap['height']);self.layer_photos.append(front);self.canvas.create_image(xoff,0,image=front,anchor='nw')
        for o in sorted(after,key=lambda q:order.get(str(q.get('layer','UI')).upper(),6)):self._draw_runtime_object(o,xoff)
        # FX status overlay only; no PC-only transformation is simulated.
        activefx=[]
        for e in self.scene.get('events',[]):
            if e.get('op')=='FX_START':
                f=int(e.get('frame',0));dur=int(e.get('duration',60) or 60)
                if f<=self.preview_frame<f+dur:activefx.append(e.get('fx'))
        if activefx:self.canvas.create_text(8,438,anchor='sw',text='FX: '+', '.join(activefx),fill='white',font=('TkFixedFont',9))
        self.frame_var.set(f'Frame {self.preview_frame}')
    def draw_timeline(self):
        c=self.timeline;c.delete('all');w=max(800,c.winfo_width());maxf=max([int(e.get('frame',0)) for e in self.scene.get('events',[])]+[180]);scale=(w-40)/maxf
        for f in range(0,maxf+1,30):x=30+f*scale;c.create_line(x,0,x,90,fill='#333');c.create_text(x+2,8,text=str(f),fill='#aaa',anchor='nw')
        rows={};r=0
        for e in sorted(self.scene.get('events',[]),key=lambda q:int(q.get('frame',0))):
            key=e.get('target') or 'GLOBAL'
            if key not in rows:rows[key]=r;r+=1
            y=28+(rows[key]%4)*14;x=30+int(e.get('frame',0))*scale;c.create_oval(x-4,y-4,x+4,y+4,fill='#ddd',outline='');c.create_text(x+7,y,text=e.get('op',''),fill='#ddd',anchor='w',font=('TkFixedFont',8))
        x=30+self.preview_frame*scale;c.create_line(x,0,x,90,fill='white',width=2)
    def step(self,d):self.stop();self.preview_frame=max(0,self.preview_frame+d);self.draw_preview();self.draw_timeline()
    def play(self):
        if self.playing:return
        self.playing=True
        def tick():
            if not self.playing:return
            self.preview_frame+=1
            maxf=max([int(e.get('frame',0))+int(e.get('duration',0) or 0) for e in self.scene.get('events',[])]+[int(o.get('end_frame',0) or 0) for o in self.scene.get('objects',[])]+[240])
            if self.preview_frame>maxf:self.preview_frame=0
            self.draw_preview();self.draw_timeline();self.after(17,tick)
        tick()
    def stop(self):self.playing=False

def cli()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--export',nargs=2,metavar=('SCENE','OUT'));ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:
        s=core.default_scene();d=core.validate(s,HERE.parents[2]);assert not [x for x in d if x[0]=='ERROR'];print('DMS Scene Builder core self-test PASS');return 0
    if a.export:
        p=Path(a.export[0]).resolve();s=core.load_scene(p);r=core.export_scene(s,Path(a.export[1]).resolve(),HERE.parents[2]);print('\n'.join(str(v) for v in r.values()));return 0
    App().mainloop();return 0
if __name__=='__main__':raise SystemExit(cli())
