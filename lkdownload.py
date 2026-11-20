import os
import io
import sys
import json
import time
import base64
import queue
import threading
import subprocess
import shutil
from types import SimpleNamespace
from pathlib import Path
from tkinter import messagebox, filedialog, colorchooser

# Cu pythonw (fără consolă) stdout/stderr sunt None -> yt_dlp ar putea crăpa la print
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import tkinter as tk
import customtkinter as ctk
import yt_dlp
from PIL import Image, ImageDraw

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = shutil.which("ffmpeg")

DOWNLOADS = Path.home() / "Downloads"
DOWNLOADS.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = Path.home() / ".lkdownload_config.json"
ASSETS = Path.home() / ".lkdownload_assets"

CALITATI = {
    "Originală": None,
    "Full HD - 1080p": 1080,
    "HD - 720p": 720,
    "SD - 480p": 480,
    "Low - 360p": 360,
    "Audio - MP3": "audio",
}

GB = 1_000_000_000

# ---------- Teme ----------
PRESETE = {
    "Neon Verde": ("#39FF14", "#030503"),
    "Cyber Albastru": ("#00D4FF", "#02060A"),
    "Violet": ("#B26BFF", "#07040C"),
    "Roșu": ("#FF3B3B", "#0C0303"),
    "Portocaliu": ("#FF9500", "#0C0702"),
    "Roz": ("#FF4FD8", "#0C030A"),
    "Galben": ("#FFE600", "#0A0900"),
    "Alb Minimal": ("#FFFFFF", "#0A0A0A"),
}

DEFAULT_CFG = {
    "preset": "Neon Verde",
    "accent": PRESETE["Neon Verde"][0],
    "bg": PRESETE["Neon Verde"][1],
    "banner": "",
    "logo": "",
    "alpha": 1.0,
}

T = SimpleNamespace()


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(x)))) for x in c)


def mix(a, b, t):
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


def luminance(h):
    r, g, b = hex_to_rgb(h)
    return 0.299 * r + 0.587 * g + 0.114 * b


def text_on(color):
    return "#000000" if luminance(color) > 140 else "#ffffff"


def aplica_tema(accent, bg):
    T.ACCENT = accent
    T.BG = bg
    T.SIDEBAR = mix(bg, "#ffffff", 0.03)
    T.CARD = mix(bg, "#ffffff", 0.06)
    T.CARD_LIGHT = mix(bg, "#ffffff", 0.11)
    T.BORDER = mix(bg, accent, 0.25)
    T.ACCENT_HOVER = mix(accent, "#000000", 0.2)
    T.TEXT = mix("#ffffff", accent, 0.1)
    T.MUTED = mix("#8c8c8c", accent, 0.3)
    T.ON_ACCENT = text_on(accent)
    T.ON_ACCENT_DIS = mix(T.ON_ACCENT, accent, 0.65)
    T.RED = "#FF5A5A"


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------- Imagini ----------
def cover(img, w, h):
    """Redimensionează + taie din centru ca să acopere exact w x h."""
    scale = max(w / img.width, h / img.height)
    nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def creeaza_icon(size=256):
    """Pătrat cu culoarea accent și săgeată de download."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.24), fill=T.ACCENT)
    dark = hex_to_rgb(T.BG) + (255,)
    d.rounded_rectangle((s * 0.43, s * 0.16, s * 0.57, s * 0.50),
                        radius=int(s * 0.03), fill=dark)
    d.polygon([(s * 0.27, s * 0.44), (s * 0.73, s * 0.44), (s * 0.50, s * 0.69)],
              fill=dark)
    d.rounded_rectangle((s * 0.22, s * 0.77, s * 0.78, s * 0.85),
                        radius=int(s * 0.04), fill=dark)
    return img


def logo_din_fisier(path, size=256):
    img = Image.open(path).convert("RGBA")
    img = cover(img, size, size)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * 0.24), fill=255
    )
    img.putalpha(mask)
    return img


# ---------- Info RAM ----------
def info_ram():
    """Returnează (total_bytes, liber_bytes)."""
    try:
        import psutil
        m = psutil.virtual_memory()
        return m.total, m.available
    except Exception:
        pass

    if sys.platform.startswith("win"):
        try:
            import ctypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = MEMSTAT()
            st.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return st.ullTotalPhys, st.ullAvailPhys
        except Exception:
            pass
    else:
        try:
            vals = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":")
                    vals[k] = int(v.split()[0]) * 1024
            return vals["MemTotal"], vals.get("MemAvailable", vals["MemTotal"] // 2)
        except Exception:
            pass
    return 8 * GB, 4 * GB


class RamTester:
    """Ocupă RAM și îl stresează continuu (scriere + citire + verificare)."""

    CHUNK = 8 * 1024 * 1024

    def __init__(self, q):
        self.q = q
        self.stop_evt = threading.Event()
        self.thread = None
        self._pend = {}
        self._last = 0.0

    def activ(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, target_bytes):
        if self.activ():
            return
        self.stop_evt.clear()
        self._pend = {}
        self._last = 0.0
        self.thread = threading.Thread(
            target=self._run, args=(int(target_bytes),), daemon=True
        )
        self.thread.start()

    def stop(self):
        self.stop_evt.set()

    def emit(self, force=False, **kw):
        # mesajele sunt grupate: max ~4/s, ca interfața să nu fie inundată
        self._pend.update(kw)
        now = time.perf_counter()
        if force or now - self._last >= 0.25:
            self.q.put(("ram", self._pend))
            self._pend = {}
            self._last = now

    def _run(self, target):
        C = self.CHUNK
        chunks = []
        errors = 0
        cycles = 0
        try:
            total = max(1, target // C)
            pats = [b"\xAA" * C, b"\x55" * C]
            self.emit(force=True, running=True, status="Se alocă memoria...",
                      alloc_frac=0.0, alloc_gb=0.0, read=0.0, write=0.0,
                      cycles=0, errors=0)

            for i in range(total):
                if self.stop_evt.is_set():
                    break
                chunks.append(bytearray(pats[0]))  # copiere = RAM atinsă efectiv
                if i % 8 == 0 or i == total - 1:
                    self.emit(alloc_frac=(i + 1) / total,
                              alloc_gb=(i + 1) * C / GB)

            if chunks and not self.stop_evt.is_set():
                scratch = bytearray(C)
                self.emit(force=True, status="Test activ - RAM-ul este stresat", alloc_frac=1.0)
                phase = 0
                while not self.stop_evt.is_set():
                    pat = pats[phase]
                    phase ^= 1

                    # --- scriere ---
                    acc_b, acc_t = 0, 0.0
                    for b in chunks:
                        if self.stop_evt.is_set():
                            break
                        t0 = time.perf_counter()
                        b[:] = pat
                        acc_t += time.perf_counter() - t0
                        acc_b += C
                        if acc_t >= 0.4:
                            self.emit(write=acc_b / acc_t / 1e9)
                            acc_b, acc_t = 0, 0.0
                    if acc_t > 0.05:
                        self.emit(write=acc_b / acc_t / 1e9)

                    # --- citire + verificare ---
                    acc_b, acc_t = 0, 0.0
                    for b in chunks:
                        if self.stop_evt.is_set():
                            break
                        t0 = time.perf_counter()
                        scratch[:] = b
                        acc_t += time.perf_counter() - t0
                        acc_b += C
                        if scratch != pat:
                            errors += 1
                            self.emit(errors=errors)
                        if acc_t >= 0.4:
                            self.emit(read=acc_b / acc_t / 1e9)
                            acc_b, acc_t = 0, 0.0
                    if acc_t > 0.05:
                        self.emit(read=acc_b / acc_t / 1e9)

                    cycles += 1
                    self.emit(cycles=cycles, errors=errors)

        except MemoryError:
            self.emit(force=True, status="Memorie insuficientă - test oprit")
        except Exception as e:
            self.emit(force=True, status=f"Eroare: {e}")
        finally:
            chunks.clear()  # eliberează RAM-ul (fără gc.collect: nu din alt thread, blochează tkinter)
            self.emit(force=True, running=False, alloc_gb=0.0, alloc_frac=0.0,
                      status="Oprit - RAM eliberat")


class App(ctk.CTk):

    def __init__(self):
        super().__init__()
        sys.setswitchinterval(0.001)  # UI mai fluid cât rulează testul RAM

        self.cfg = load_cfg()
        aplica_tema(self.cfg["accent"], self.cfg["bg"])

        self.title("LkDownload")
        self.geometry("1050x720")
        self.minsize(900, 620)
        try:
            self.attributes("-alpha", float(self.cfg["alpha"]))
        except Exception:
            pass

        self.coada = queue.Queue()
        self.descarcare_activa = False

        self.pagina = "downloader"
        self.url_text = ""
        self.progress_val = 0.0
        self.status_txt = "Aștept link-ul..."
        self.status_col = None  # None = muted

        self.calitate_var = ctk.StringVar(value="Originală")
        self.calitate_var.trace_add("write", self.actualizeaza_format)

        self.ram = RamTester(self.coada)
        self.ram_state = dict(
            running=False, status="Oprit", alloc_gb=0.0, alloc_frac=0.0,
            read=0.0, write=0.0, read_max=0.0, write_max=0.0,
            cycles=0, errors=0,
        )
        self.ram_gb_alege = 4.0

        self.sidebar = None
        self.main = None
        self.banner_label = None
        self.banner_pil = None
        self.banner_w = 700
        self._resize_job = None
        self.ram_ui = {}

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.inchide)
        self.verifica_coada()

    # ------------------------------------------------------------ UI general
    def build_ui(self):
        try:
            if self.pagina == "downloader" and self.url_entry.winfo_exists():
                self.url_text = self.url_entry.get()
        except Exception:
            pass

        self.configure(fg_color=T.BG)
        for w in (self.sidebar, self.main):
            if w is not None:
                w.destroy()
        self.ram_ui = {}
        self.banner_label = None

        logo = self.cfg.get("logo")
        try:
            if logo and Path(logo).exists():
                self.icon_img = logo_din_fisier(logo)
            else:
                self.icon_img = creeaza_icon()
        except Exception:
            self.icon_img = creeaza_icon()
        self.aplica_icon_fereastra()

        self.creeaza_sidebar()
        self.creeaza_principal()

    def aplica_icon_fereastra(self):
        buf = io.BytesIO()
        self.icon_img.resize((64, 64)).save(buf, format="PNG")
        self.icon_tk = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
        self.after(300, lambda: self.iconphoto(False, self.icon_tk))

    def schimba_pagina(self, nume):
        if nume == self.pagina:
            return
        self.pagina = nume
        self.build_ui()

    def aplica_schimbare_tema(self):
        aplica_tema(self.cfg["accent"], self.cfg["bg"])
        save_cfg(self.cfg)
        self.build_ui()

    def inchide(self):
        save_cfg(self.cfg)
        self.ram.stop()
        self.destroy()

    # ------------------------------------------------------------ sidebar
    def creeaza_sidebar(self):
        self.sidebar = ctk.CTkFrame(
            self, width=245, corner_radius=0, fg_color=T.SIDEBAR
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        logo = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        logo.pack(fill="x", padx=25, pady=(30, 35))

        logo_img = ctk.CTkImage(
            light_image=self.icon_img, dark_image=self.icon_img, size=(48, 48)
        )
        ctk.CTkLabel(logo, text="", image=logo_img).pack(side="left")

        logo_text = ctk.CTkFrame(logo, fg_color="transparent")
        logo_text.pack(side="left", padx=12)

        ctk.CTkLabel(
            logo_text, text="LkDownload", text_color=T.TEXT,
            font=ctk.CTkFont(size=19, weight="bold")
        ).pack(anchor="w")

        ctk.CTkLabel(
            logo_text, text="MEDIA DOWNLOADER", text_color=T.ACCENT,
            font=ctk.CTkFont(size=9, weight="bold")
        ).pack(anchor="w")

        ctk.CTkLabel(
            self.sidebar, text="MENIU", text_color=T.MUTED,
            font=ctk.CTkFont(size=11, weight="bold")
        ).pack(anchor="w", padx=28, pady=(5, 12))

        self.nav_button("⬇    Downloader", self.pagina == "downloader",
                        command=lambda: self.schimba_pagina("downloader"))
        self.nav_button("▣    Descărcări", False, command=self.deschide_folder)
        self.nav_button("⚙    Setări", self.pagina == "setari",
                        command=lambda: self.schimba_pagina("setari"))

        # credit (cel mai jos)
        ctk.CTkLabel(
            self.sidebar, text="By Andrei - IDk RO", text_color=T.MUTED,
            font=ctk.CTkFont(size=9)
        ).pack(side="bottom", pady=(0, 10))

        self.sidebar_bottom = ctk.CTkFrame(
            self.sidebar, fg_color=T.CARD, corner_radius=16,
            border_width=1, border_color=T.BORDER
        )
        self.sidebar_bottom.pack(side="bottom", fill="x", padx=17, pady=(20, 8))

        ctk.CTkLabel(
            self.sidebar_bottom, text="STATUS APLICAȚIE", text_color=T.MUTED,
            font=ctk.CTkFont(size=10, weight="bold")
        ).pack(anchor="w", padx=15, pady=(14, 5))

        ctk.CTkLabel(
            self.sidebar_bottom, text="●  Sistem pregătit", text_color=T.ACCENT,
            font=ctk.CTkFont(size=12, weight="bold")
        ).pack(anchor="w", padx=15, pady=(0, 14))

    def nav_button(self, text, active=False, command=None):
        button = ctk.CTkButton(
            self.sidebar,
            text=text,
            anchor="w",
            height=45,
            corner_radius=10,
            fg_color=T.CARD_LIGHT if active else "transparent",
            hover_color=T.CARD_LIGHT,
            text_color=T.ACCENT if active else T.MUTED,
            border_width=1 if active else 0,
            border_color=T.BORDER,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=command or (lambda: None)
        )
        button.pack(fill="x", padx=17, pady=4)

    # ------------------------------------------------------------ zona principală
    def creeaza_principal(self):
        self.main = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew", padx=35, pady=30)
        self.main.grid_columnconfigure(0, weight=1)

        if self.pagina == "setari":
            self.main.grid_rowconfigure(1, weight=1)
            self.pagina_setari()
        else:
            self.pagina_downloader()

    # ---------- pagina Downloader ----------
    def pagina_downloader(self):
        header = ctk.CTkFrame(self.main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")

        banner = self.cfg.get("banner")
        if banner and Path(banner).exists():
            try:
                im = Image.open(banner).convert("RGB")
                im.thumbnail((2400, 2400))
                self.banner_pil = im
                self.banner_label = ctk.CTkLabel(
                    header, text="Downloader", text_color="#ffffff",
                    font=ctk.CTkFont(size=34, weight="bold"),
                    compound="center", image=self.crea_banner(self.banner_w)
                )
                self.banner_label.pack(fill="x")
            except Exception:
                self.banner_label = None

        if self.banner_label is None:
            ctk.CTkLabel(
                header, text="Downloader", text_color=T.TEXT,
                font=ctk.CTkFont(size=29, weight="bold")
            ).pack(anchor="w")

        ctk.CTkLabel(
            header, text="Descarcă videoclipuri și audio într-un singur loc.",
            text_color=T.MUTED, font=ctk.CTkFont(size=13)
        ).pack(anchor="w", pady=(8 if self.banner_label else 5, 0))

        self.main.bind("<Configure>", self.on_resize_main, add="+")

        self.card = ctk.CTkFrame(
            self.main, fg_color=T.CARD, corner_radius=20,
            border_width=1, border_color=T.BORDER
        )
        self.card.grid(row=1, column=0, sticky="ew", pady=(30, 20))
        self.card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.card, text="LINK VIDEO", text_color=T.MUTED,
            font=ctk.CTkFont(size=11, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=25, pady=(25, 10))

        self.url_entry = ctk.CTkEntry(
            self.card, height=52, corner_radius=12,
            fg_color=T.CARD_LIGHT, border_color=T.BORDER, text_color=T.TEXT,
            placeholder_text="Introdu link-ul aici...",
            font=ctk.CTkFont(size=13)
        )
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=25)
        if self.url_text:
            self.url_entry.insert(0, self.url_text)

        ctk.CTkLabel(
            self.card, text="CALITATE", text_color=T.MUTED,
            font=ctk.CTkFont(size=11, weight="bold")
        ).grid(row=2, column=0, sticky="w", padx=25, pady=(25, 10))

        self.calitate_menu = ctk.CTkOptionMenu(
            self.card,
            variable=self.calitate_var,
            values=list(CALITATI.keys()),
            height=45,
            corner_radius=10,
            fg_color=T.CARD_LIGHT,
            button_color=T.ACCENT,
            button_hover_color=T.ACCENT_HOVER,
            dropdown_fg_color=T.CARD_LIGHT,
            dropdown_hover_color=T.BORDER,
            dropdown_text_color=T.TEXT,
            text_color=T.TEXT,
            font=ctk.CTkFont(size=13),
            dropdown_font=ctk.CTkFont(size=13)
        )
        self.calitate_menu.grid(row=3, column=0, sticky="ew", padx=25)

        self.format_label = ctk.CTkLabel(
            self.card, text="OUTPUT FORMAT: MP4", text_color=T.ACCENT,
            font=ctk.CTkFont(size=11, weight="bold")
        )
        self.format_label.grid(row=4, column=0, sticky="w", padx=25, pady=(15, 0))
        self.actualizeaza_format()

        self.download_button = ctk.CTkButton(
            self.card,
            text="SE DESCARCĂ..." if self.descarcare_activa else "⬇   DESCARCĂ ACUM",
            state="disabled" if self.descarcare_activa else "normal",
            height=52,
            corner_radius=12,
            fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER,
            text_color=T.ON_ACCENT,
            text_color_disabled=T.ON_ACCENT_DIS,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.start
        )
        self.download_button.grid(
            row=5, column=0, sticky="ew", padx=25, pady=(25, 25)
        )

        self.progress_card = ctk.CTkFrame(
            self.main, fg_color=T.CARD, corner_radius=18,
            border_width=1, border_color=T.BORDER
        )
        self.progress_card.grid(row=2, column=0, sticky="ew")
        self.progress_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.progress_card, text="PROGRES DESCĂRCARE", text_color=T.MUTED,
            font=ctk.CTkFont(size=11, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=22, pady=(20, 10))

        self.progress = ctk.CTkProgressBar(
            self.progress_card, height=9, corner_radius=10,
            progress_color=T.ACCENT, fg_color=T.CARD_LIGHT
        )
        self.progress.grid(row=1, column=0, sticky="ew", padx=22)
        self.progress.set(self.progress_val)

        self.progress_text = ctk.CTkLabel(
            self.progress_card, text=f"{int(self.progress_val * 100)}%",
            text_color=T.ACCENT, font=ctk.CTkFont(size=13, weight="bold")
        )
        self.progress_text.grid(row=2, column=0, sticky="w", padx=22, pady=(10, 0))

        self.status_label = ctk.CTkLabel(
            self.progress_card, text=self.status_txt,
            text_color=self.status_col or T.MUTED, font=ctk.CTkFont(size=12)
        )
        self.status_label.grid(row=3, column=0, sticky="w", padx=22, pady=(5, 20))

        footer = ctk.CTkFrame(self.main, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", pady=(20, 0))

        ctk.CTkLabel(
            footer, text="LkDownload v2.1.0", text_color=T.MUTED,
            font=ctk.CTkFont(size=11)
        ).pack(side="left")

        ctk.CTkButton(
            footer,
            text="📁  Deschide folderul",
            width=175, height=36, corner_radius=9,
            fg_color=T.CARD_LIGHT, hover_color=T.BORDER,
            text_color=T.ACCENT,
            border_width=1, border_color=T.BORDER,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.deschide_folder
        ).pack(side="right")

    def crea_banner(self, w):
        w = max(300, int(w))
        img = cover(self.banner_pil, w, 130)
        img = Image.blend(img, Image.new("RGB", img.size, T.BG), 0.5)
        self._banner_ctk = ctk.CTkImage(
            light_image=img, dark_image=img, size=(w, 130)
        )
        return self._banner_ctk

    def on_resize_main(self, e):
        if self.banner_label is None or abs(e.width - self.banner_w) < 10:
            return
        self.banner_w = e.width
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self.refresh_banner)

    def refresh_banner(self):
        self._resize_job = None
        try:
            if self.banner_label is not None and self.banner_label.winfo_exists():
                self.banner_label.configure(image=self.crea_banner(self.banner_w))
        except Exception:
            pass

    # ---------- pagina Setări ----------
    def pagina_setari(self):
        header = ctk.CTkFrame(self.main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")

        ctk.CTkLabel(
            header, text="Setări", text_color=T.TEXT,
            font=ctk.CTkFont(size=29, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            header, text="Personalizează aspectul și testează memoria RAM.",
            text_color=T.MUTED, font=ctk.CTkFont(size=13)
        ).pack(anchor="w", pady=(5, 0))

        sc = ctk.CTkScrollableFrame(
            self.main, fg_color=T.BG, corner_radius=0,
            scrollbar_button_color=T.BORDER,
            scrollbar_button_hover_color=T.ACCENT
        )
        sc.grid(row=1, column=0, sticky="nsew", pady=(20, 0))

        self.card_tema(sc)
        self.card_imagini(sc)
        self.card_fereastra(sc)
        self.card_ram(sc)

    def make_card(self, parent, titlu):
        card = ctk.CTkFrame(
            parent, fg_color=T.CARD, corner_radius=18,
            border_width=1, border_color=T.BORDER
        )
        card.pack(fill="x", pady=(0, 16), padx=(0, 6))
        ctk.CTkLabel(
            card, text=titlu, text_color=T.MUTED,
            font=ctk.CTkFont(size=11, weight="bold")
        ).pack(anchor="w", padx=22, pady=(20, 10))
        return card

    def mini_btn(self, parent, text, command, width=170, side="left"):
        b = ctk.CTkButton(
            parent, text=text, width=width, height=36, corner_radius=9,
            fg_color=T.CARD_LIGHT, hover_color=T.BORDER, text_color=T.ACCENT,
            border_width=1, border_color=T.BORDER,
            font=ctk.CTkFont(size=11, weight="bold"), command=command
        )
        b.pack(side=side, padx=(0, 8))
        return b

    # --- Temă ---
    def card_tema(self, parent):
        card = self.make_card(parent, "TEMĂ ȘI CULORI")

        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=18, pady=(0, 6))
        for c in range(4):
            grid.grid_columnconfigure(c, weight=1, uniform="sw")

        for i, (nume, (acc, bg)) in enumerate(PRESETE.items()):
            sel = self.cfg.get("preset") == nume
            ctk.CTkButton(
                grid, text=nume, height=38, corner_radius=10,
                fg_color=acc, hover_color=mix(acc, "#000000", 0.2),
                text_color=text_on(acc),
                border_width=3 if sel else 0, border_color=T.TEXT,
                font=ctk.CTkFont(size=11, weight="bold"),
                command=lambda n=nume: self.set_preset(n)
            ).grid(row=i // 4, column=i % 4, padx=4, pady=4, sticky="ew")

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(8, 20))

        ctk.CTkFrame(row, width=26, height=26, corner_radius=6,
                     fg_color=T.ACCENT, border_width=1,
                     border_color=T.BORDER).pack(side="left", padx=(0, 8))
        self.mini_btn(row, "🎨  Culoare accent", self.alege_accent)

        ctk.CTkFrame(row, width=26, height=26, corner_radius=6,
                     fg_color=T.BG, border_width=1,
                     border_color=T.BORDER).pack(side="left", padx=(8, 8))
        self.mini_btn(row, "🖌  Culoare fundal", self.alege_fundal)

        self.mini_btn(row, "↺  Resetează", self.reset_tema, width=120)

    def set_preset(self, nume):
        acc, bg = PRESETE[nume]
        self.cfg.update(preset=nume, accent=acc, bg=bg)
        self.aplica_schimbare_tema()

    def alege_accent(self):
        c = colorchooser.askcolor(color=T.ACCENT, title="Culoare accent",
                                  parent=self)[1]
        if c:
            self.cfg.update(preset="Personalizat", accent=c)
            self.aplica_schimbare_tema()

    def alege_fundal(self):
        c = colorchooser.askcolor(color=T.BG, title="Culoare fundal",
                                  parent=self)[1]
        if c:
            self.cfg.update(preset="Personalizat", bg=c)
            self.aplica_schimbare_tema()

    def reset_tema(self):
        self.cfg.update(
            preset=DEFAULT_CFG["preset"], accent=DEFAULT_CFG["accent"],
            bg=DEFAULT_CFG["bg"], banner="", logo=""
        )
        self.aplica_schimbare_tema()

    # --- Imagini ---
    def card_imagini(self, parent):
        card = self.make_card(parent, "IMAGINI LOCALE")
        self.rand_imagine(card, "Banner (imagine în antetul Downloader)", "banner")
        self.rand_imagine(card, "Logo personalizat (sidebar + iconiță fereastră)", "logo")
        ctk.CTkFrame(card, height=8, fg_color="transparent").pack()

    def rand_imagine(self, card, titlu, cheie):
        ctk.CTkLabel(
            card, text=titlu, text_color=T.TEXT, font=ctk.CTkFont(size=12)
        ).pack(anchor="w", padx=22, pady=(4, 6))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 10))

        self.mini_btn(row, "🖼  Alege imagine...",
                      lambda: self.alege_imagine(cheie))
        self.mini_btn(row, "✕  Elimină",
                      lambda: self.elimina_imagine(cheie), width=100)

        p = self.cfg.get(cheie)
        ctk.CTkLabel(
            row, text=Path(p).name if p else "(implicit)",
            text_color=T.MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=6)

    def alege_imagine(self, cheie):
        p = filedialog.askopenfilename(
            parent=self, title="Alege o imagine",
            filetypes=[("Imagini", "*.png *.jpg *.jpeg *.bmp *.webp *.gif"),
                       ("Toate fișierele", "*.*")]
        )
        if not p:
            return
        try:
            with Image.open(p) as im:
                im.verify()
            ASSETS.mkdir(parents=True, exist_ok=True)
            dest = ASSETS / f"{cheie}{Path(p).suffix.lower()}"
            if not (dest.exists() and Path(p).resolve() == dest.resolve()):
                shutil.copy2(p, dest)
        except Exception as e:
            messagebox.showerror("Imagine invalidă", str(e))
            return
        self.cfg[cheie] = str(dest)
        self.aplica_schimbare_tema()

    def elimina_imagine(self, cheie):
        self.cfg[cheie] = ""
        self.aplica_schimbare_tema()

    # --- Fereastră ---
    def card_fereastra(self, parent):
        card = self.make_card(parent, "FEREASTRĂ")

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 20))

        ctk.CTkLabel(row, text="Transparență", text_color=T.TEXT,
                     font=ctk.CTkFont(size=12)).pack(side="left")

        self.alpha_lbl = ctk.CTkLabel(
            row, text=f"{int(float(self.cfg['alpha']) * 100)}%",
            text_color=T.ACCENT, font=ctk.CTkFont(size=12, weight="bold"),
            width=50
        )
        self.alpha_lbl.pack(side="right")

        ctk.CTkSlider(
            row, from_=0.5, to=1.0, number_of_steps=50, width=300,
            progress_color=T.ACCENT, button_color=T.ACCENT,
            button_hover_color=T.ACCENT_HOVER, fg_color=T.CARD_LIGHT,
            command=self.seteaza_alpha
        ).pack(side="right", padx=10)
        # valoarea inițială a sliderului
        for w in row.winfo_children():
            if isinstance(w, ctk.CTkSlider):
                w.set(float(self.cfg["alpha"]))

    def seteaza_alpha(self, v):
        self.cfg["alpha"] = round(float(v), 2)
        try:
            self.attributes("-alpha", self.cfg["alpha"])
        except Exception:
            pass
        self.alpha_lbl.configure(text=f"{int(self.cfg['alpha'] * 100)}%")

    # --- RAM ---
    def card_ram(self, parent):
        card = self.make_card(parent, "TEST RAM  (ocupă memorie și o stresează)")

        total, liber = info_ram()
        max_gb = max(1.0, int(liber * 0.8 / GB * 2) / 2)  # max 80% din RAM liber
        self.ram_gb_alege = min(max(self.ram_gb_alege, 0.5), max_gb)

        ctk.CTkLabel(
            card,
            text=f"RAM total: {total / GB:.1f} GB   •   Liber acum: {liber / GB:.1f} GB",
            text_color=T.TEXT, font=ctk.CTkFont(size=12)
        ).pack(anchor="w", padx=22, pady=(0, 10))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 10))

        ctk.CTkLabel(row, text="Cantitate de ocupat", text_color=T.TEXT,
                     font=ctk.CTkFont(size=12)).pack(side="left")

        self.ram_gb_lbl = ctk.CTkLabel(
            row, text=f"{self.ram_gb_alege:.1f} GB", text_color=T.ACCENT,
            font=ctk.CTkFont(size=12, weight="bold"), width=70
        )
        self.ram_gb_lbl.pack(side="right")

        steps = max(1, int((max_gb - 0.5) / 0.5))
        sl = ctk.CTkSlider(
            row, from_=0.5, to=max_gb, number_of_steps=steps, width=300,
            progress_color=T.ACCENT, button_color=T.ACCENT,
            button_hover_color=T.ACCENT_HOVER, fg_color=T.CARD_LIGHT,
            command=self.seteaza_ram_gb
        )
        sl.set(self.ram_gb_alege)
        sl.pack(side="right", padx=10)

        self.ram_btn = ctk.CTkButton(
            card, height=46, corner_radius=12,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.toggle_ram
        )
        self.ram_btn.pack(fill="x", padx=22, pady=(6, 12))

        self.ram_bar = ctk.CTkProgressBar(
            card, height=8, corner_radius=8,
            progress_color=T.ACCENT, fg_color=T.CARD_LIGHT
        )
        self.ram_bar.pack(fill="x", padx=22, pady=(0, 6))

        self.ram_status = ctk.CTkLabel(
            card, text="", text_color=T.MUTED, font=ctk.CTkFont(size=12)
        )
        self.ram_status.pack(anchor="w", padx=22, pady=(0, 10))

        tiles = ctk.CTkFrame(card, fg_color="transparent")
        tiles.pack(fill="x", padx=18, pady=(0, 6))
        for c in range(3):
            tiles.grid_columnconfigure(c, weight=1, uniform="t")

        def tile(r, c, nume):
            f = ctk.CTkFrame(tiles, fg_color=T.CARD_LIGHT, corner_radius=12,
                             border_width=1, border_color=T.BORDER)
            f.grid(row=r, column=c, padx=4, pady=4, sticky="ew")
            ctk.CTkLabel(f, text=nume, text_color=T.MUTED,
                         font=ctk.CTkFont(size=10, weight="bold")
                         ).pack(anchor="w", padx=12, pady=(10, 0))
            v = ctk.CTkLabel(f, text="-", text_color=T.ACCENT,
                             font=ctk.CTkFont(size=15, weight="bold"))
            v.pack(anchor="w", padx=12)
            s = ctk.CTkLabel(f, text=" ", text_color=T.MUTED,
                             font=ctk.CTkFont(size=10))
            s.pack(anchor="w", padx=12, pady=(0, 10))
            return v, s

        self.ram_ui = {
            "alloc": tile(0, 0, "ALOCAT"),
            "read": tile(0, 1, "CITIRE"),
            "write": tile(0, 2, "SCRIERE"),
            "cycles": tile(1, 0, "CICLURI"),
            "errors": tile(1, 1, "ERORI DETECTATE"),
        }

        ctk.CTkLabel(
            card,
            text="Notă: testul rulează în Python pe un singur thread, deci GB/s măsurat e "
                 "orientativ (bun pentru comparat înainte/după). Pentru stabilitate la "
                 "overclock folosește și MemTest86 / TestMem5. Erorile > 0 = RAM instabil.",
            text_color=T.MUTED, font=ctk.CTkFont(size=10),
            wraplength=560, justify="left"
        ).pack(anchor="w", padx=22, pady=(6, 20))

        self.refresh_ram_ui()

    def seteaza_ram_gb(self, v):
        self.ram_gb_alege = float(v)
        self.ram_gb_lbl.configure(text=f"{self.ram_gb_alege:.1f} GB")

    def toggle_ram(self):
        if self.ram_state["running"] or self.ram.activ():
            self.ram.stop()
            self.ram_state["status"] = "Se oprește..."
            self.refresh_ram_ui()
        else:
            self.ram_state.update(read_max=0.0, write_max=0.0, read=0.0,
                                  write=0.0, cycles=0, errors=0, running=True,
                                  status="Se pornește...")
            self.ram.start(self.ram_gb_alege * GB)
            self.refresh_ram_ui()

    def ram_event(self, d):
        s = self.ram_state
        s.update(d)
        if "read" in d:
            s["read_max"] = max(s["read_max"], d["read"])
        if "write" in d:
            s["write_max"] = max(s["write_max"], d["write"])
        self.refresh_ram_ui()

    def refresh_ram_ui(self):
        if self.pagina != "setari" or not self.ram_ui:
            return
        s, u = self.ram_state, self.ram_ui
        try:
            running = s["running"]
            self.ram_btn.configure(
                text="■   OPREȘTE ȘI ELIBEREAZĂ RAM" if running
                else "▶   PORNEȘTE TESTUL RAM",
                fg_color=T.RED if running else T.ACCENT,
                hover_color="#D44848" if running else T.ACCENT_HOVER,
                text_color="#000000" if running else T.ON_ACCENT,
            )
            self.ram_bar.set(s["alloc_frac"])
            self.ram_status.configure(
                text=s["status"], text_color=T.ACCENT if running else T.MUTED
            )
            u["alloc"][0].configure(text=f"{s['alloc_gb']:.1f} GB")
            u["read"][0].configure(text=f"{s['read']:.2f} GB/s")
            u["read"][1].configure(text=f"max {s['read_max']:.2f} GB/s")
            u["write"][0].configure(text=f"{s['write']:.2f} GB/s")
            u["write"][1].configure(text=f"max {s['write_max']:.2f} GB/s")
            u["cycles"][0].configure(text=str(s["cycles"]))
            u["errors"][0].configure(
                text=str(s["errors"]),
                text_color=T.RED if s["errors"] else T.ACCENT
            )
        except tk.TclError:
            pass

    # ------------------------------------------------------------ download (logica neschimbată)
    def actualizeaza_format(self, *args):
        try:
            if CALITATI.get(self.calitate_var.get()) == "audio":
                self.format_label.configure(text="OUTPUT FORMAT: MP3")
            else:
                self.format_label.configure(text="OUTPUT FORMAT: MP4")
        except Exception:
            pass

    def start(self):
        if self.descarcare_activa:
            return

        url = self.url_entry.get().strip()
        limita = CALITATI.get(self.calitate_var.get())

        if not url:
            messagebox.showwarning(
                "LkDownload", "Introdu un link înainte de descărcare."
            )
            return

        if not url.startswith(("http://", "https://")):
            messagebox.showerror(
                "Link invalid",
                "Link-ul trebuie să înceapă cu http:// sau https://."
            )
            return

        if limita == "audio" and not FFMPEG:
            messagebox.showerror(
                "FFmpeg lipsește",
                "Pentru MP3 ai nevoie de FFmpeg.\n\n"
                "Rulează în terminal:\npip install imageio-ffmpeg"
            )
            return

        self.descarcare_activa = True
        self.download_button.configure(state="disabled", text="SE DESCARCĂ...")

        self.set_dl_ui(0, "Se pregătește descărcarea...", T.ACCENT)

        threading.Thread(
            target=self.descarca, args=(url, limita), daemon=True
        ).start()

    def descarca(self, url, limita):
        opt = {
            "outtmpl": str(DOWNLOADS / "%(title)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 5,
            "fragment_retries": 5,
            "progress_hooks": [self.progress_hook],
        }

        if FFMPEG:
            opt["ffmpeg_location"] = FFMPEG

        if limita == "audio":
            # MP3 - descarcă cel mai bun audio și îl convertește cu FFmpeg
            opt["format"] = "bestaudio/best"
            opt["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        else:
            filtru = f"[height<={limita}]" if limita else ""

            if FFMPEG:
                opt["format"] = (
                    f"bestvideo{filtru}[ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo{filtru}+bestaudio/"
                    f"best{filtru}/best"
                )
                opt["merge_output_format"] = "mp4"
            else:
                opt["format"] = f"best{filtru}[ext=mp4]/best{filtru}/best"

        try:
            with yt_dlp.YoutubeDL(opt) as ydl:
                ydl.download([url])

            self.coada.put(("success", "Descărcare finalizată!"))

        except Exception as e:
            self.coada.put(("error", str(e)))

    def progress_hook(self, data):
        if data["status"] == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes", 0)

            if total:
                self.coada.put(("progress", downloaded / total))

            viteza = (data.get("_speed_str") or "").strip()
            eta = (data.get("_eta_str") or "").strip()
            self.coada.put(("status", f"Se descarcă... {viteza} | ETA: {eta}"))

        elif data["status"] == "finished":
            self.coada.put(("status", "Se procesează / se convertește fișierul..."))

    def set_dl_ui(self, progress=None, status=None, color=None):
        """Ține starea descărcării și o afișează dacă pagina Downloader e deschisă."""
        if progress is not None:
            self.progress_val = progress
        if status is not None:
            self.status_txt = status
            self.status_col = color
        if self.pagina != "downloader":
            return
        try:
            self.progress.set(self.progress_val)
            self.progress_text.configure(text=f"{int(self.progress_val * 100)}%")
            self.status_label.configure(
                text=self.status_txt, text_color=self.status_col or T.MUTED
            )
        except tk.TclError:
            pass

    def verifica_coada(self):
        ram_acc = {}
        try:
            for _ in range(200):
                tip, valoare = self.coada.get_nowait()

                if tip == "progress":
                    self.set_dl_ui(progress=max(0, min(1, valoare)))

                elif tip == "status":
                    self.set_dl_ui(status=valoare, color=T.ACCENT)

                elif tip == "success":
                    self.set_dl_ui(1, valoare, T.ACCENT)
                    self.termina()
                    messagebox.showinfo(
                        "LkDownload", "Fișierul a fost descărcat cu succes!"
                    )

                elif tip == "error":
                    self.set_dl_ui(status="Descărcarea a eșuat.", color=T.RED)
                    self.termina()
                    messagebox.showerror("Download Error", valoare)

                elif tip == "ram":
                    ram_acc.update(valoare)

        except queue.Empty:
            pass

        if ram_acc:
            self.ram_event(ram_acc)

        self.after(100, self.verifica_coada)

    def termina(self):
        self.descarcare_activa = False
        if self.pagina == "downloader":
            try:
                self.download_button.configure(
                    state="normal", text="⬇   DESCARCĂ ACUM"
                )
            except tk.TclError:
                pass

    def deschide_folder(self):
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(DOWNLOADS))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(DOWNLOADS)])
            else:
                subprocess.Popen(["xdg-open", str(DOWNLOADS)])
        except Exception as e:
            messagebox.showerror("Eroare", str(e))


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    app = App()
    app.mainloop()