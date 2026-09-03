#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "GDK" / "tools"))
from dms_game_settings import (  # noqa: E402
    FORMAT,
    SETTINGS,
    SettingsError,
    _get,
    _set,
    compile_document,
    default_document,
    load_document,
    save_document,
)


class SettingsApp(tk.Tk):
    def __init__(self, project: Path):
        super().__init__()
        self.project = project.resolve()
        self.source = self.project / "dms_game_settings.json"
        self.title(f"RÉGLAGES DU JEU - {self.project.name}")
        self.geometry("940x720")
        self.minsize(780, 560)
        self.variables: dict[str, tk.Variable] = {}
        self.document = default_document()
        self.status = tk.StringVar(value="Le JSON est la source; BUILD + RUN recompile automatiquement.")
        self._build_ui()
        self._load_or_defaults()

    def _build_ui(self) -> None:
        ttk.Label(self, text="RÉGLAGES DU JEU", font=("Segoe UI", 20, "bold")).pack(anchor="w", padx=18, pady=(16, 2))
        ttk.Label(self, text=str(self.source)).pack(anchor="w", padx=18, pady=(0, 12))
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=4)
        sections: dict[str, ttk.Frame] = {}
        for setting in SETTINGS:
            if setting.section not in sections:
                frame = ttk.Frame(notebook, padding=12)
                notebook.add(frame, text=setting.section)
                frame.columnconfigure(1, weight=1)
                sections[setting.section] = frame
            frame = sections[setting.section]
            row = len([s for s in SETTINGS[: SETTINGS.index(setting)] if s.section == setting.section])
            ttk.Label(frame, text=setting.label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            if setting.kind == "bool":
                variable: tk.Variable = tk.BooleanVar()
                ttk.Checkbutton(frame, variable=variable).grid(row=row, column=1, sticky="w", pady=5)
            else:
                variable = tk.StringVar()
                ttk.Entry(frame, textvariable=variable, width=18).grid(row=row, column=1, sticky="ew", pady=5)
            ttk.Label(frame, text=setting.help, foreground="#555555").grid(row=row, column=2, sticky="w", padx=(10, 0), pady=5)
            self.variables[setting.path] = variable
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=18, pady=10)
        ttk.Button(buttons, text="ENREGISTRER + COMPILER", command=self.save_and_compile).pack(side="left")
        ttk.Button(buttons, text="Recharger", command=self._load_or_defaults).pack(side="left", padx=6)
        ttk.Button(buttons, text="Valeurs par défaut", command=self.load_defaults).pack(side="left")
        ttk.Label(buttons, textvariable=self.status).pack(side="right")

    def _load_or_defaults(self) -> None:
        try:
            if self.source.exists():
                self.document, warnings = load_document(self.source)
                self.status.set("Chargé" + (f" - {len(warnings)} valeur(s) migrée(s)" if warnings else ""))
            else:
                self.document = default_document()
                self.status.set("Nouveau fichier - enregistrez pour l’activer")
            self._document_to_form()
        except Exception as exc:
            messagebox.showerror("Réglages du jeu", str(exc), parent=self)

    def _document_to_form(self) -> None:
        for setting in SETTINGS:
            value = _get(self.document, setting.path, setting.default)
            self.variables[setting.path].set(value)

    def load_defaults(self) -> None:
        if not messagebox.askyesno("Réglages du jeu", "Remettre le formulaire aux valeurs par défaut ?\nLe fichier n’est pas encore modifié.", parent=self):
            return
        self.document = default_document()
        self._document_to_form()
        self.status.set("Valeurs par défaut dans le formulaire")

    def _form_to_document(self) -> dict:
        data = json.loads(json.dumps(self.document))
        data["format"] = FORMAT
        for setting in SETTINGS:
            raw = self.variables[setting.path].get()
            if setting.kind == "bool":
                value = bool(raw)
            elif setting.kind == "int":
                value = int(str(raw).strip())
            else:
                value = float(str(raw).strip().replace(",", "."))
            _set(data, setting.path, value)
        return data

    def save_and_compile(self) -> None:
        try:
            data = self._form_to_document()
            if self.source.exists():
                try:
                    old = json.loads(self.source.read_text(encoding="utf-8-sig"))
                except Exception:
                    old = {}
                if old.get("format") not in (None, FORMAT):
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup = self.source.with_name(f"dms_game_settings.before_migration_{stamp}.json")
                    shutil.copy2(self.source, backup)
            save_document(self.source, data)
            compile_document(self.source, self.project / "build" / "autogen")
            self.document, _ = load_document(self.source)
            self.status.set("PASS - enregistré et compilé")
            messagebox.showinfo("Réglages du jeu", "Réglages enregistrés. Ils seront repris au prochain BUILD + RUN.", parent=self)
        except (ValueError, SettingsError) as exc:
            messagebox.showerror("Valeur invalide", str(exc), parent=self)
        except Exception as exc:
            messagebox.showerror("Réglages du jeu", str(exc), parent=self)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: dms_game_settings_ui.py DOSSIER_PROJET")
        return 2
    project = Path(sys.argv[1])
    if not (project / "src" / "main.c").is_file():
        print("ERREUR : projet invalide (src/main.c absent)")
        return 2
    SettingsApp(project).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
