#!/usr/bin/env python3
"""DMS Metadata Editor V0.1 - safe META editor for DMR 0.1 files.

The editor never relocates CODE, SDIR or SAMP. If META cannot be resized in
place, a new META chunk is appended and only its directory entry is repointed.
This keeps audio/sample page addresses stable.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import struct
import sys
import tempfile
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable

APP_NAME = "DMS Metadata Editor"
APP_VERSION = "0.1.0"
SYSTEM_CLOCK = 24_000_000
MAX_DMR_SIZE = 0x01000000
KNOWN_FIELDS = (
    ("title", "TITLE"),
    ("author", "AUTHOR"),
    ("composer", "COMPOSER"),
    ("project", "PROJECT / ALBUM"),
    ("year", "YEAR"),
    ("genre", "GENRE"),
    ("copyright", "COPYRIGHT"),
    ("comment", "COMMENT"),
)
KNOWN_KEYS = {key for key, _label in KNOWN_FIELDS}


class DmrError(RuntimeError):
    pass


@dataclass(frozen=True)
class Chunk:
    kind: bytes
    offset: int
    size: int
    flags: int
    directory_index: int


@dataclass(frozen=True)
class DmrLayout:
    total_size: int
    directory_offset: int
    chunk_count: int
    entry_size: int
    chunks: tuple[Chunk, ...]

    @property
    def meta(self) -> Chunk | None:
        for chunk in self.chunks:
            if chunk.kind == b"META":
                return chunk
        return None


def align(value: int, boundary: int = 4) -> int:
    return (value + boundary - 1) & -boundary


def parse_dmr(data: bytes | bytearray) -> DmrLayout:
    if len(data) < 64 or data[:4] != b"DMR0":
        raise DmrError("ce fichier n'est pas une ROM DMR")
    major, minor, header_size = struct.unpack_from(">HHH", data, 4)
    if (major, minor) != (0, 1):
        raise DmrError(f"version DMR non prise en charge: {major}.{minor}")
    if header_size != 64:
        raise DmrError(f"en-tête DMR incompatible: {header_size} octets")
    total_size = struct.unpack_from(">I", data, 0x0C)[0]
    if total_size != len(data):
        raise DmrError(f"taille DMR incohérente: header={total_size}, fichier={len(data)}")
    if data[0x10:0x14] != b"DMS1":
        raise DmrError("hardware ID différent de DMS1")
    clock = struct.unpack_from(">I", data, 0x24)[0]
    if clock != SYSTEM_CLOCK:
        raise DmrError(f"timebase inattendue: {clock} Hz")
    directory = struct.unpack_from(">I", data, 0x18)[0]
    count, entry_size = struct.unpack_from(">HH", data, 0x1C)
    if entry_size != 16:
        raise DmrError("répertoire DMR incompatible")
    if directory < 64 or directory + count * entry_size > len(data):
        raise DmrError("répertoire DMR hors fichier")
    chunks: list[Chunk] = []
    seen: set[bytes] = set()
    for i in range(count):
        kind, offset, size, flags = struct.unpack_from(">4sIII", data, directory + i * 16)
        if kind in seen:
            raise DmrError(f"chunk dupliqué: {kind!r}")
        seen.add(kind)
        if offset > len(data) or size > len(data) - offset:
            raise DmrError(f"chunk {kind!r} hors fichier")
        chunks.append(Chunk(kind, offset, size, flags, i))
    if b"CODE" not in seen:
        raise DmrError("chunk CODE absent")
    return DmrLayout(total_size, directory, count, entry_size, tuple(chunks))


def decode_meta_blob(blob: bytes) -> tuple[dict[str, str], list[str]]:
    text = blob.decode("utf-8", errors="strict")
    values: dict[str, str] = {}
    extras: list[str] = []
    for raw in text.splitlines():
        if "=" not in raw:
            if raw.strip():
                extras.append(raw)
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            extras.append(raw)
            continue
        values[key] = value.strip()
    return values, extras


def load_metadata(path: Path) -> tuple[DmrLayout, dict[str, str], list[str]]:
    data = path.read_bytes()
    layout = parse_dmr(data)
    meta = layout.meta
    if meta is None:
        raise DmrError("chunk META absent : cette V0.1 édite uniquement les DMR contenant déjà META")
    try:
        values, extras = decode_meta_blob(data[meta.offset:meta.offset + meta.size])
    except UnicodeDecodeError as exc:
        raise DmrError(f"META n'est pas un UTF-8 valide: {exc}") from exc
    return layout, values, extras


def validate_line(key: str, value: str) -> None:
    if not key or key.strip() != key:
        raise DmrError(f"clé META invalide: {key!r}")
    if "=" in key or "\n" in key or "\r" in key:
        raise DmrError(f"clé META invalide: {key!r}")
    if "\n" in value or "\r" in value:
        raise DmrError(f"la valeur de {key!r} doit tenir sur une ligne")


def encode_metadata(known: dict[str, str], raw_other: str) -> bytes:
    lines: list[str] = []
    seen: set[str] = set()
    for key, _label in KNOWN_FIELDS:
        value = known.get(key, "").strip()
        if value:
            validate_line(key, value)
            lines.append(f"{key}={value}")
            seen.add(key)

    for line_no, raw in enumerate(raw_other.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            # Preserve comment/free-form metadata lines as-is.
            lines.append(raw.rstrip())
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        validate_line(key, value)
        if key in KNOWN_KEYS:
            raise DmrError(f"ligne {line_no}: {key}= se modifie dans le champ dédié")
        if key in seen:
            raise DmrError(f"ligne {line_no}: clé META dupliquée: {key}")
        lines.append(f"{key}={value}")
        seen.add(key)

    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def non_meta_snapshot(data: bytes | bytearray, layout: DmrLayout) -> dict[bytes, tuple[int, int, bytes]]:
    return {
        chunk.kind: (chunk.offset, chunk.size, bytes(data[chunk.offset:chunk.offset + chunk.size]))
        for chunk in layout.chunks
        if chunk.kind != b"META"
    }


def rewrite_metadata_bytes(original: bytes, new_meta: bytes) -> bytes:
    layout = parse_dmr(original)
    meta = layout.meta
    if meta is None:
        raise DmrError("chunk META absent")
    before = non_meta_snapshot(original, layout)

    # If META is already the physical tail (normal for sample-free DMR and for
    # a file previously saved by this editor), resize there. Otherwise append
    # a new META chunk so SDIR/SAMP page locations never move.
    if meta.offset + meta.size == len(original):
        new_offset = meta.offset
        out = bytearray(original[:new_offset])
        out.extend(new_meta)
    else:
        new_offset = align(len(original), 4)
        out = bytearray(original)
        if len(out) < new_offset:
            out.extend(b"\x00" * (new_offset - len(out)))
        out.extend(new_meta)

    if len(out) > MAX_DMR_SIZE:
        raise DmrError("DMR dépasserait la limite V0.1 de 16 MiB")

    entry = layout.directory_offset + meta.directory_index * layout.entry_size
    struct.pack_into(">4sIII", out, entry, b"META", new_offset, len(new_meta), meta.flags)
    struct.pack_into(">I", out, 0x0C, len(out))

    after_layout = parse_dmr(out)
    after = non_meta_snapshot(out, after_layout)
    if before.keys() != after.keys():
        raise DmrError("validation interne: liste des chunks audio modifiée")
    for kind in before:
        if before[kind] != after[kind]:
            raise DmrError(f"validation interne: chunk {kind.decode('ascii','replace')} modifié")
    return bytes(out)


def atomic_write(path: Path, payload: bytes) -> None:
    path = path.resolve()
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".dmsmeta.tmp", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Full validation before the original is replaced.
        parse_dmr(temp.read_bytes())
        os.replace(temp, path)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def save_metadata(source: Path, destination: Path, known: dict[str, str], raw_other: str) -> None:
    source = source.resolve()
    destination = destination.resolve()
    original = source.read_bytes()
    new_meta = encode_metadata(known, raw_other)
    payload = rewrite_metadata_bytes(original, new_meta)
    if source == destination:
        atomic_write(destination, payload)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(destination, payload)


class WindowsFileDrop:
    """Pure ctypes WM_DROPFILES support, matching the Music Player approach."""
    WM_DROPFILES = 0x0233
    GWLP_WNDPROC = -4

    def __init__(self, root: tk.Tk, callback: Callable[[Path], None]):
        if os.name != "nt":
            raise RuntimeError("WM_DROPFILES est disponible uniquement sous Windows")
        root.update_idletasks()
        hwnd = root.winfo_id()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        LONG_PTR = ctypes.c_ssize_t
        WPARAM = ctypes.c_size_t
        LPARAM = ctypes.c_ssize_t
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_void_p, ctypes.c_uint, WPARAM, LPARAM)
        SetWindowLongPtr = user32.SetWindowLongPtrW
        SetWindowLongPtr.argtypes = [ctypes.c_void_p, ctypes.c_int, LONG_PTR]
        SetWindowLongPtr.restype = LONG_PTR
        CallWindowProc = user32.CallWindowProcW
        CallWindowProc.argtypes = [LONG_PTR, ctypes.c_void_p, ctypes.c_uint, WPARAM, LPARAM]
        CallWindowProc.restype = LRESULT
        shell32.DragAcceptFiles.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        shell32.DragQueryFileW.restype = ctypes.c_uint
        shell32.DragFinish.argtypes = [ctypes.c_void_p]

        self.root = root
        self.hwnd = hwnd
        self.shell32 = shell32
        self.SetWindowLongPtr = SetWindowLongPtr
        self.LONG_PTR = LONG_PTR
        self.old_proc = 0

        @WNDPROC
        def proc(window, msg, wparam, lparam):
            if msg == self.WM_DROPFILES:
                hdrop = ctypes.c_void_p(wparam)
                try:
                    count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                    if count:
                        needed = shell32.DragQueryFileW(hdrop, 0, None, 0) + 1
                        buf = ctypes.create_unicode_buffer(needed)
                        shell32.DragQueryFileW(hdrop, 0, buf, needed)
                        dropped = Path(buf.value)
                        root.after(0, lambda p=dropped: callback(p))
                finally:
                    shell32.DragFinish(hdrop)
                return 0
            return CallWindowProc(self.old_proc, window, msg, wparam, lparam)

        self.proc = proc
        new_ptr = ctypes.cast(self.proc, ctypes.c_void_p).value
        ctypes.set_last_error(0)
        self.old_proc = SetWindowLongPtr(ctypes.c_void_p(hwnd), self.GWLP_WNDPROC, LONG_PTR(new_ptr))
        if not self.old_proc and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        shell32.DragAcceptFiles(ctypes.c_void_p(hwnd), True)

    def close(self) -> None:
        if self.old_proc:
            try:
                self.shell32.DragAcceptFiles(ctypes.c_void_p(self.hwnd), False)
                self.SetWindowLongPtr(ctypes.c_void_p(self.hwnd), self.GWLP_WNDPROC, self.LONG_PTR(self.old_proc))
            except Exception:
                pass
            self.old_proc = 0


class MetadataEditorApp:
    BG = "#090d0b"
    PANEL = "#101712"
    GREEN = "#65ff98"
    CYAN = "#62d9ff"
    WHITE = "#e9f3ec"
    DIM = "#7d9485"
    AMBER = "#ffc568"
    RED = "#ff6f6f"
    ENTRY_BG = "#050806"

    def __init__(self, initial: Path | None = None):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("820x720")
        self.root.minsize(700, 620)
        self.root.configure(bg=self.BG)
        self.path: Path | None = None
        self.layout: DmrLayout | None = None
        self.vars = {key: tk.StringVar() for key, _label in KNOWN_FIELDS}
        self.path_var = tk.StringVar(value="NO DMR LOADED")
        self.info_var = tk.StringVar(value="DROP A .DMR FILE OR CLICK OPEN")
        self.drop_target: WindowsFileDrop | None = None
        self._build_ui()
        self.root.bind("<Control-o>", lambda _e: self.open_dialog())
        self.root.bind("<Control-s>", lambda _e: self.save())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if os.name == "nt":
            try:
                self.drop_target = WindowsFileDrop(self.root, self.load_path)
            except Exception as exc:
                self.info_var.set(f"WM_DROPFILES unavailable: {exc}")
        if initial:
            self.root.after(60, lambda: self.load_path(initial))

    def _build_ui(self) -> None:
        mono = ("Consolas", 10)
        mono_b = ("Consolas", 10, "bold")
        title_f = ("Consolas", 14, "bold")
        outer = tk.Frame(self.root, bg=self.BG, padx=18, pady=16)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="DMS METADATA EDITOR // DMR 0.1", bg=self.BG, fg=self.GREEN, font=title_f).pack(anchor="w")
        tk.Label(outer, text="metadata only | CODE / FM / SSG / ADPCM untouched", bg=self.BG, fg=self.DIM, font=mono).pack(anchor="w", pady=(2, 12))
        tk.Label(outer, textvariable=self.path_var, bg=self.PANEL, fg=self.CYAN, font=mono, anchor="w", padx=10, pady=8).pack(fill="x")

        fields = tk.Frame(outer, bg=self.BG)
        fields.pack(fill="x", pady=(12, 4))
        fields.grid_columnconfigure(1, weight=1)
        fields.grid_columnconfigure(3, weight=1)
        for idx, (key, label) in enumerate(KNOWN_FIELDS):
            row, side = divmod(idx, 2)
            base = side * 2
            tk.Label(fields, text=label, bg=self.BG, fg=self.DIM, font=mono_b, anchor="w").grid(row=row, column=base, sticky="w", padx=(0 if side == 0 else 16, 8), pady=4)
            ent = tk.Entry(fields, textvariable=self.vars[key], bg=self.ENTRY_BG, fg=self.WHITE, insertbackground=self.GREEN,
                           relief="flat", highlightthickness=1, highlightbackground="#26352a", highlightcolor=self.GREEN, font=mono)
            ent.grid(row=row, column=base + 1, sticky="ew", pady=4)

        tk.Label(outer, text="OTHER METADATA  // one key=value per line", bg=self.BG, fg=self.DIM, font=mono_b).pack(anchor="w", pady=(10, 4))
        text_frame = tk.Frame(outer, bg=self.BG)
        text_frame.pack(fill="both", expand=True)
        self.other = tk.Text(text_frame, height=13, wrap="none", undo=True, bg=self.ENTRY_BG, fg=self.WHITE,
                             insertbackground=self.GREEN, selectbackground="#234d32", relief="flat", padx=8, pady=8,
                             highlightthickness=1, highlightbackground="#26352a", highlightcolor=self.GREEN, font=mono)
        sy = tk.Scrollbar(text_frame, orient="vertical", command=self.other.yview)
        sx = tk.Scrollbar(text_frame, orient="horizontal", command=self.other.xview)
        self.other.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.other.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)

        buttons = tk.Frame(outer, bg=self.BG)
        buttons.pack(fill="x", pady=(12, 8))
        for label, command in (("OPEN DMR", self.open_dialog), ("RELOAD", self.reload), ("SAVE", self.save), ("SAVE COPY", self.save_copy)):
            tk.Button(buttons, text=label, command=command, bg=self.PANEL, fg=self.GREEN, activebackground="#18231b",
                      activeforeground=self.WHITE, relief="flat", bd=0, padx=14, pady=8, font=mono_b).pack(side="left", padx=(0, 8))

        tk.Label(outer, textvariable=self.info_var, bg=self.BG, fg=self.AMBER, font=mono, anchor="w", justify="left").pack(fill="x")

    def open_dialog(self) -> None:
        name = filedialog.askopenfilename(title="Open DMS music", filetypes=[("DMS Music ROM", "*.dmr"), ("All files", "*.*")])
        if name:
            self.load_path(Path(name))

    def load_path(self, path: Path) -> None:
        try:
            path = Path(path).resolve()
            if path.suffix.lower() != ".dmr":
                raise DmrError(".DMR expected")
            layout, values, extras = load_metadata(path)
            self.path = path
            self.layout = layout
            for key, _label in KNOWN_FIELDS:
                self.vars[key].set(values.pop(key, ""))
            other_lines = [*extras, *(f"{k}={v}" for k, v in values.items())]
            self.other.delete("1.0", "end")
            if other_lines:
                self.other.insert("1.0", "\n".join(other_lines) + "\n")
            meta = layout.meta
            self.path_var.set(str(path))
            chunks = " ".join(chunk.kind.decode("ascii", "replace") for chunk in layout.chunks)
            self.info_var.set(f"READY | {layout.total_size:,} bytes | META {meta.size if meta else 0} bytes | chunks: {chunks}")
        except Exception as exc:
            self.info_var.set(f"ERROR | {exc}")
            messagebox.showerror(APP_NAME, str(exc))

    def reload(self) -> None:
        if self.path:
            self.load_path(self.path)

    def _known(self) -> dict[str, str]:
        return {key: var.get() for key, var in self.vars.items()}

    def save(self) -> None:
        if not self.path:
            messagebox.showinfo(APP_NAME, "Open a DMR first.")
            return
        try:
            save_metadata(self.path, self.path, self._known(), self.other.get("1.0", "end-1c"))
            self.load_path(self.path)
            self.info_var.set(self.info_var.get() + " | SAVED - AUDIO CHUNKS VERIFIED")
        except Exception as exc:
            self.info_var.set(f"SAVE ERROR | {exc}")
            messagebox.showerror(APP_NAME, str(exc))

    def save_copy(self) -> None:
        if not self.path:
            messagebox.showinfo(APP_NAME, "Open a DMR first.")
            return
        suggested = self.path.with_name(self.path.stem + "_META" + self.path.suffix)
        name = filedialog.asksaveasfilename(title="Save DMR copy", defaultextension=".dmr", initialfile=suggested.name,
                                            initialdir=str(self.path.parent), filetypes=[("DMS Music ROM", "*.dmr")])
        if not name:
            return
        destination = Path(name)
        try:
            save_metadata(self.path, destination, self._known(), self.other.get("1.0", "end-1c"))
            self.info_var.set(f"COPY SAVED | {destination}")
        except Exception as exc:
            self.info_var.set(f"SAVE COPY ERROR | {exc}")
            messagebox.showerror(APP_NAME, str(exc))

    def close(self) -> None:
        if self.drop_target:
            self.drop_target.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def inspect_jsonish(path: Path) -> str:
    layout, values, extras = load_metadata(path)
    lines = [f"file={path}", f"size={layout.total_size}"]
    lines.extend(f"{k}={v}" for k, v in values.items())
    lines.extend(f"raw={line}" for line in extras)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dmr", nargs="?", type=Path)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    if args.inspect:
        if not args.dmr:
            parser.error("--inspect requires a DMR path")
        print(inspect_jsonish(args.dmr))
        return 0
    MetadataEditorApp(args.dmr).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
