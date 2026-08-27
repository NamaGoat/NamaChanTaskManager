import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
import json
import os
import queue
import io
import faulthandler
import traceback
from tkinter import ttk, messagebox

import customtkinter as ctk

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

import accounts
import core
import features
import updater

__version__ = "1.0.16"

BG = "#0d1118"
CARD = "#151b26"
CARD2 = "#1e2735"
FG = "#eaf2fb"
MUT = "#8296ad"
AMBER = "#e6b84c"

THEMES = {
    "Miyabi":   {"ACCENT": "#41bccc", "ACCENT_H": "#5cd9e9"},
    "Écarlate": {"ACCENT": "#e8465f", "ACCENT_H": "#ff5d76"},
    "Violet":   {"ACCENT": "#9d6bff", "ACCENT_H": "#b78fff"},
    "Ambre":    {"ACCENT": "#eda23b", "ACCENT_H": "#ffc05c"},
}
DEFAULT_THEME = "Miyabi"

GREEN = "#31c8a0"
GREEN_H = "#45dfb5"
RED = "#d93a52"
RED_H = "#ef5068"
RED_DK = "#7e2531"


def load_theme(name):
    t = THEMES.get(name, THEMES[DEFAULT_THEME])
    globals().update(t)
    return name if name in THEMES else DEFAULT_THEME


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c1, c2, t):
    a, b = _hex_rgb(c1), _hex_rgb(c2)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


FONT = ("Segoe UI", 12)
FONT_B = ("Segoe UI", 12, "bold")
FONT_T = ("Segoe UI", 20, "bold")
FONT_S = ("Segoe UI", 10)

GFX_TOOLTIPS = {
    "Auto": "Roblox gère la qualité tout seul (peut repasser en Auto en multi).\nN'applique AUCUN flag.",
    "Perf++": "FPS max absolu (moteur 1/21).\n"
             "• Textures : mini (override 1)\n"
             "• Anti-aliasing : off (MSAA -1)\n"
             "• Herbe : off (grass 0)\n"
             "• Ombres douces : off (voxelizer)\n"
             "• Ciel gris : on\n"
             "• LOD / render distance : max (200k)\n"
             "⚠ NVIDIA conseillé (sur AMD la vision au loin peut se réduire).",
    "Perf": "FPS max sans fog gris (moteur 21/21).\n"
            "• Textures : mini (override 1)\n"
            "• Anti-aliasing : off (MSAA -1)\n"
            "• Herbe : off (grass 0)\n"
            "• Ombres douces : off (voxelizer)\n"
            "• Ciel : normal (nuit visible)\n"
            "• LOD / render distance : max (200k)\n"
            "Vision complète et détaillée au loin.",
    "Perf Render Max": "Pour cartes AMD (moteur 21/21, render max).\n"
            "• Textures : mini (override 1)\n"
            "• Anti-aliasing : off (MSAA -1)\n"
            "• Herbe : off (grass 0)\n"
            "• Ombres douces : off (voxelizer)\n"
            "• Ciel : normal\n"
            "• LOD / render distance : max (200k)\n"
            "Vision maximale sans couper le streaming.",
    "Équilibré": "Niveau 8/21 — bon compromis visuel / performances.\nVision complète, pas de dégradation agressive.",
    "Pro": "Niveau 21/21 — rendu au maximum, vision complète.\nAucune optimisation de dégradation.",
}


class ToolTip:
    """Infobulle simple au survol (Toplevel sans bordures)."""

    def __init__(self, widget, get_text):
        self.widget = widget
        self.get_text = get_text
        self.tw = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _=None):
        if self.tw or not self.widget.winfo_exists():
            return
        try:
            txt = self.get_text()
            tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{self.widget.winfo_rootx() + 4}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 6}")
            lbl = tk.Label(tw, text=txt, justify="left", wraplength=280,
                           background="#0a0e15", foreground=FG, relief="solid",
                           borderwidth=1, font=("Segoe UI", 9), padx=8, pady=5)
            lbl.pack()
            self.tw = tw
        except Exception:
            self.tw = None

    def _hide(self, _=None):
        if self.tw:
            try:
                self.tw.destroy()
            except Exception:
                pass
            self.tw = None


def _thumb_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _download_image(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read()
    except Exception:
        return None


def _icon_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "namachan.ico")


def _apply_icon(win):
    try:
        if os.path.exists(_icon_path()):
            win.iconbitmap(_icon_path())
    except Exception:
        pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.settings = core.load_settings()
        self.theme_name = load_theme(self.settings.get("theme", DEFAULT_THEME))
        self._restart = False
        self.title("NamaChan Account Manager")
        self.geometry("1240x760")
        self.minsize(1040, 640)
        self.configure(fg_color=BG)
        ctk.set_appearance_mode("dark")
        _apply_icon(self)

        self.current_account = None
        self.current_place = None
        self._profiles = []
        self._sel_acc = None
        self._sel_id = None
        self._closing = False
        self.var_force = ctk.BooleanVar()
        self._q = queue.Queue()
        self.views = {}
        self._img_cache = {}
        self._acc_btns = {}

        self.build_sidebar_shell()
        self.build_view_comptes_sidebar()
        self.build_view_instances(title="Multi Roblox")
        self.build_view_features()
        self.build_view_logs()
        self.build_view_settings()

        self.antiafk = features.AntiAFK(log=self.log)
        self.rejoin = features.AutoRejoin(launch_fn=self.api_launch, log=self.log)
        s = self.settings
        if hasattr(self, "sw_ar"):
            self.sw_ar.select() if s.get("auto_rejoin") else self.sw_ar.deselect()
            self.e_ar_delay.insert(0, str(s.get("ar_delay", 6)))
        if hasattr(self, "sw_aa"):
            self.sw_aa.select() if s.get("anti_afk") else self.sw_aa.deselect()
            self.e_aa_int.insert(0, str(s.get("aa_interval", 120)))
        self.var_force.set(s.get("force_mutex", True))
        self.apply_feature_settings()

        self.show_view("comptes")
        self.after(100, self._drain)
        updater.cleanup_old_files()
        try:
            fixed = core.ensure_fps_cap()
            if fixed:
                self.log(f"[FPS] Cap {self.settings.get('fps_default')} réappliqué après MAJ Roblox ({len(fixed)} version(s)).")
        except Exception:
            pass
        self.refresh_accounts_list()
        self.reload_profiles_menu()
        self.after(2000, self.refresh_table_loop)
        self.after(3000, self._auto_check_update)

    def _sync_guardian(self):
        try:
            if self.var_force.get():
                core.start_guardian()
                th = getattr(core, "_guardian_thread", None)
                if th and th.is_alive():
                    self.log("[Multi-instance] ON — gardien actif : fermer une instance n'fermera plus les autres.")
                else:
                    self.log("[Multi-instance] ERREUR : gardien non démarré !")
            else:
                core.stop_guardian()
                self.log("[Multi-instance] OFF — gardien arrêté.")
        except Exception as e:
            self.log(f"[Multi-instance] ERREUR gardien : {e}")

    def build_sidebar_shell(self):
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color=CARD)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        bg = self._bg_image(200, 900, CARD, _mix(BG, ACCENT, 0.25))
        if bg:
            ctk.CTkLabel(self.sidebar, text="", image=bg).place(x=0, y=0, relwidth=1, relheight=1)
        ctk.CTkLabel(self.sidebar, text="NamaChan", font=FONT_T, text_color=ACCENT).pack(pady=(22, 0))
        ctk.CTkLabel(self.sidebar, text="Account Manager", font=("Segoe UI", 12), text_color=MUT).pack(pady=(0, 2))
        ctk.CTkLabel(self.sidebar, text=f"v{__version__}", font=FONT_S, text_color=MUT).pack(pady=(0, 18))
        self.nav_buttons = {}
        for key, label in [("comptes", "  Comptes"), ("multi", "  Multi Roblox"),
                           ("features", "  Anti-AFK / Rejoin"), ("logs", "  Console"), ("settings", "  Paramètres")]:
            b = ctk.CTkButton(self.sidebar, text=label, font=FONT_B, anchor="w", height=42,
                              fg_color="transparent", hover_color=CARD2, text_color=MUT,
                              command=lambda k=key: self.show_view(k))
            b.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = b
        ctk.CTkButton(self.sidebar, text="♥  Donation", font=FONT_B, height=38,
                      fg_color=ACCENT, hover_color=ACCENT_H, text_color="#000000",
                      command=lambda: webbrowser.open("https://ko-fi.com/namachan")).pack(side="bottom", padx=14, pady=16)

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)
        header = ctk.CTkFrame(right, height=58, corner_radius=0, fg_color=CARD)
        header.pack(fill="x")
        header.pack_propagate(False)
        hbg = self._bg_image(1400, 58, _mix(CARD, ACCENT, 0.10), BG, horizontal=True)
        if hbg:
            ctk.CTkLabel(header, text="", image=hbg).place(x=0, y=0, relwidth=1, relheight=1)
        self.hdr_title = ctk.CTkLabel(header, text="Comptes", font=FONT_T, anchor="w")
        self.hdr_title.pack(side="left", padx=18)
        self.hdr_account = ctk.CTkLabel(header, text="", font=FONT_S, text_color=MUT)
        self.hdr_account.pack(side="right", padx=(0, 14))
        ctk.CTkButton(header, text="+ Ajouter un compte", font=FONT_B, height=36, corner_radius=8,
                      fg_color=ACCENT, hover_color=ACCENT_H, text_color="#000000",
                      command=self.dlg_add_choice).pack(side="right", padx=6)
        self.status_lbl = ctk.CTkLabel(right, text="Prêt.", font=FONT_S, anchor="w", text_color=MUT, height=24)
        self.status_lbl.pack(fill="x", padx=10, pady=(4, 0))
        self.container = ctk.CTkFrame(right, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=14, pady=(8, 10))

    def show_view(self, key):
        for v in self.views.values():
            v.pack_forget()
        self.views[key].pack(fill="both", expand=True)
        titles = {"comptes": "Comptes", "multi": "Multi Roblox",
                  "features": "Anti-AFK / Auto-Rejoin",
                  "logs": "Console", "settings": "Paramètres"}
        if hasattr(self, "hdr_title"):
            self.hdr_title.configure(text=titles.get(key, key))
        for k, b in self.nav_buttons.items():
            b.configure(fg_color=(ACCENT if k == key else "transparent"),
                        text_color=("#000000" if k == key else MUT))
        if key == "comptes":
            self.refresh_accounts_list()

    def _bg_image(self, w, h, c1, c2, horizontal=False):
        if not HAS_PIL:
            return None
        key = ("bg", w, h, c1, c2, horizontal)
        if key in self._img_cache:
            return self._img_cache[key]
        img = None
        for name in ("sidebar_bg.png", "sidebar_bg.jpg"):
            p = os.path.join(accounts.APP_DIR, name)
            if os.path.exists(p):
                try:
                    src = Image.open(p).convert("RGBA")
                    sw, sh = src.size
                    scale = max(w / sw, h / sh)
                    src = src.resize((max(1, int(sw * scale)), max(1, int(sh * scale))), Image.LANCZOS)
                    l = (src.width - w) // 2
                    t = (src.height - h) // 2
                    img = src.crop((l, t, l + w, t + h))
                except Exception:
                    img = None
                break
        if img is None:
            from PIL import ImageDraw
            g = Image.new("RGB", (w, h))
            d = ImageDraw.Draw(g)
            n = w if horizontal else h
            step = max(1, n - 1)
            for i in range(n):
                col = _mix(c1, c2, i / step)
                if horizontal:
                    d.line([(i, 0), (i, h)], fill=col)
                else:
                    d.line([(0, i), (w, i)], fill=col)
            img = g.convert("RGBA")
        cim = ctk.CTkImage(light_image=img, dark_image=img, size=(w, h))
        self._img_cache[key] = cim
        return cim

    def ui(self, fn):
        if threading.current_thread() is threading.main_thread():
            fn()
        else:
            self._q.put(fn)

    def _drain(self):
        try:
            while True:
                fn = self._q.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        if not self._closing:
            self.after(100, self._drain)

    def _safe_grab(self, w):
        try:
            w.grab_set()
        except Exception:
            pass

    def report_callback_exception(self, exc, val, tb):
        self.log(f"[ERREUR] {exc.__name__ if hasattr(exc, '__name__') else exc}: {val}")

    def log(self, msg):
        def _write(m=msg):
            try:
                ts = time.strftime("%H:%M:%S")
                self.logs_box.insert("end", f"[{ts}] {m}\n")
                self.logs_box.see("end")
            except Exception:
                pass
        try:
            if threading.current_thread() is threading.main_thread():
                _write()
            else:
                self.ui(_write)
        except Exception:
            pass

    def set_status(self, msg):
        def _set(m=msg):
            try:
                self.status_lbl.configure(text=m)
            except Exception:
                pass
        try:
            if threading.current_thread() is threading.main_thread():
                _set()
            else:
                self.ui(_set)
        except Exception:
            pass

    def update_header(self):
        if hasattr(self, "hdr_account"):
            acc = self._sel_acc.get("name") if self._sel_acc else (self.current_account or "")
            self.hdr_account.configure(text=f"● {acc}" if acc else "")

    def _avatar_for(self, user_id):
        key = f"u{user_id}"
        if key in self._img_cache:
            return self._img_cache[key]
        d = _thumb_json(f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=150x150&format=Png&isCircular=true")
        url = (d or {}).get("data", [{}])[0].get("imageUrl")
        raw = _download_image(url) if url else None
        img = None
        if raw and HAS_PIL:
            try:
                pil = Image.open(io.BytesIO(raw)).resize((36, 36))
                img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(36, 36))
                self._img_cache[key] = img
            except Exception:
                img = None
        return img

    def _game_icon_for(self, place_id, size=64):
        key = f"g{place_id}_{size}"
        if key in self._img_cache:
            return self._img_cache[key]
        d = _thumb_json(f"https://thumbnails.roblox.com/v1/places/gameicons?placeIds={place_id}&size=256x256&format=Png&isCircular=false")
        url = (d or {}).get("data", [{}])[0].get("imageUrl")
        raw = _download_image(url) if url else None
        img = None
        if raw and HAS_PIL:
            try:
                pil = Image.open(io.BytesIO(raw)).resize((size, size))
                img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
                self._img_cache[key] = img
            except Exception:
                img = None
        return img

    def selected_account(self):
        return getattr(self, "_sel_acc", None)

    def refresh_accounts_list(self):
        accs = accounts.get_accounts()
        if not self._sel_id and accs:
            self._sel_acc = accs[0]
            self._sel_id = accs[0].get("id")
            self.current_account = accs[0]["name"]
            self.update_header()
        for w in self.acc_list.winfo_children():
            w.destroy()
        self._acc_btns = {}
        if not accs:
            ctk.CTkLabel(self.acc_list, text="Aucun compte.\nClique sur « + Ajouter un compte ».",
                         font=FONT_S, text_color=MUT, justify="center").pack(pady=30)
            return

        def fill_avatar(btn, uid):
            img = self._avatar_for(uid)
            if img:
                self.ui(lambda b=btn, i=img: (b.winfo_exists() and b.configure(image=i)))

        for a in accs:
            badge = "  [nav]" if a.get("chrome_profile") else ""
            sel = self._sel_id == a.get("id")
            card = ctk.CTkFrame(self.acc_list, fg_color=(ACCENT if sel else CARD2), corner_radius=8)
            card.pack(fill="x", pady=3)
            av_btn = ctk.CTkButton(card, text=a["name"][:16] + badge, font=FONT_B, anchor="w", height=44,
                                   corner_radius=8, image=None,
                                   fg_color="transparent", hover_color="#26303f",
                                   text_color=("#000000" if sel else "#ffffff"),
                                   command=lambda aa=a: self.select_account(aa))
            av_btn.pack(side="left", fill="x", expand=True, padx=(2, 0))
            self._acc_btns[a["id"]] = av_btn
            ctk.CTkButton(card, text="⚙", width=34, height=30, corner_radius=6,
                          fg_color="transparent", hover_color="#26303f", text_color=MUT,
                          command=lambda aa=a: self.open_details_dialog(aa)).pack(side="right", padx=3)
            if a.get("user_id"):
                threading.Thread(target=fill_avatar, args=(av_btn, a["user_id"]), daemon=True).start()

    def select_account(self, acc):
        self._sel_acc = acc
        self._sel_id = acc.get("id")
        self.current_account = acc.get("name")
        self.update_header()
        self.refresh_accounts_list()

    def open_details_dialog(self, acc):
        w = ctk.CTkToplevel(self)
        w.title(f"Détails — {acc['name']}")
        w.geometry("480x430")
        _apply_icon(w)
        w.configure(fg_color=BG)
        w.transient(self)
        w.after(200, lambda: self._safe_grab(w))
        ctk.CTkLabel(w, text=f"Détails du compte", font=("Segoe UI", 15, "bold"), anchor="w").pack(fill="x", padx=18, pady=(16, 6))
        r1 = ctk.CTkFrame(w, fg_color="transparent")
        r1.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(r1, text="Pseudo :", font=FONT).pack(side="left")
        ent_name = ctk.CTkEntry(r1, font=FONT)
        ent_name.insert(0, acc.get("name", ""))
        ent_name.pack(side="left", padx=8, fill="x", expand=True)
        ctk.CTkLabel(w, text="Notes :", font=FONT, anchor="w").pack(fill="x", padx=18, pady=(8, 0))
        txt_notes = ctk.CTkTextbox(w, height=90, font=FONT, fg_color=CARD2)
        txt_notes.insert("1.0", acc.get("notes", ""))
        txt_notes.pack(fill="x", padx=18, pady=4)
        r2 = ctk.CTkFrame(w, fg_color="transparent")
        r2.pack(fill="x", padx=18, pady=6)
        ctk.CTkLabel(r2, text="Profil navigateur :", font=FONT).pack(side="left")
        titles = ["—"] + [p["title"] for p in self._profiles]
        cur = "—"
        prof = acc.get("chrome_profile")
        if prof:
            for p in self._profiles:
                if f"{p['browser']}:{p['rel']}" == prof:
                    cur = p["title"]
                    break
        opt = ctk.CTkOptionMenu(r2, values=titles, font=FONT_S, width=240,
                                fg_color=CARD2, button_color=CARD2, button_hover_color="#26303f",
                                text_color=FG, dropdown_fg_color=CARD2, dropdown_text_color=FG)
        opt.set(cur)
        opt.pack(side="left", padx=8, fill="x", expand=True)

        def do_save():
            prof_key = None
            t = opt.get()
            for p in self._profiles:
                if p["title"] == t:
                    prof_key = f"{p['browser']}:{p['rel']}"
                    break
            accounts.update_account(acc["id"],
                                    name=ent_name.get().strip() or acc["name"],
                                    notes=txt_notes.get("1.0", "end").strip(),
                                    chrome_profile=prof_key)
            self.refresh_accounts_list()
            self.reload_profiles_menu()
            self.set_status("Compte enregistré.")
            try:
                w.grab_release()
            except Exception:
                pass
            w.destroy()

        def do_delete():
            if messagebox.askyesno("Confirmer", f"Supprimer le compte '{acc['name']}' ?"):
                accounts.remove_account(acc["id"])
                if self._sel_id == acc.get("id"):
                    self._sel_acc = None
                    self._sel_id = None
                self.refresh_accounts_list()
                self.set_status("Compte supprimé.")
                try:
                    w.grab_release()
                except Exception:
                    pass
                w.destroy()

        def do_open_browser():
            prof_key = acc.get("chrome_profile")
            if not prof_key:
                self.set_status("Lie d'abord un profil navigateur.")
                return
            browser, rel = prof_key.split(":", 1)
            paths = {
                "Chrome": [os.path.join(os.getenv("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
                           os.path.join(os.getenv("PROGRAMFILES", ""), r"Google\Chrome\Application\chrome.exe"),
                           os.path.join(os.getenv("PROGRAMFILES(X86)", ""), r"Google\Chrome\Application\chrome.exe")],
                "Edge": [os.path.join(os.getenv("LOCALAPPDATA", ""), r"Microsoft\Edge\Application\msedge.exe"),
                         os.path.join(os.getenv("PROGRAMFILES(X86)", ""), r"Microsoft\Edge\Application\msedge.exe")],
                "Brave": [os.path.join(os.getenv("LOCALAPPDATA", ""), r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                          os.path.join(os.getenv("PROGRAMFILES(X86)", ""), r"BraveSoftware\Brave-Browser\Application\brave.exe")],
            }
            exe = next((p for p in paths.get(browser, []) if os.path.isfile(p)), None)
            if exe:
                subprocess.Popen([exe, f"--profile-directory={rel}", "--no-first-run",
                                  "https://www.roblox.com/home"], cwd=os.path.dirname(exe))

        btns = ctk.CTkFrame(w, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(10, 16))
        ctk.CTkButton(btns, text="Enregistrer", command=do_save).pack(side="left")
        ctk.CTkButton(btns, text="Ouvrir navigateur", fg_color=CARD2, hover_color="#26303f",
                      command=do_open_browser).pack(side="left", padx=8)
        ctk.CTkButton(btns, text="Supprimer", fg_color=RED, hover_color=RED_H,
                      command=do_delete).pack(side="right")

    def dlg_add_choice(self):
        self.start_browser_login()

    def start_browser_login(self):
        if getattr(self, "_login_running", False):
            return
        self._login_running = True

        def done(ok, msg):
            self._login_running = False
            self.log(msg)
            self.set_status(msg)
            if ok:
                self.refresh_accounts_list()

        def work():
            try:
                h = accounts.start_temp_login(log=self.log)
            except Exception as ex:
                m = f"Login navigateur impossible : {ex}"
                self.ui(lambda m=m: done(False, m))
                return
            user_closed = False
            last_ping = time.time()
            tried = set()
            attempts = 0
            for _ in range(900):
                if h["proc"].poll() is not None:
                    time.sleep(3)
                    cands = accounts.poll_temp_login_candidates(h) or []
                    if not cands:
                        user_closed = True
                    break
                cands = accounts.poll_temp_login_candidates(h) or []
                new_tokens = [t for t in cands if t not in tried]
                for t in new_tokens:
                    attempts += 1
                    self.log(f"[Login navigateur] Vérification de la session (essai {attempts})...")
                    try:
                        name, existed = accounts.add_account(t)
                    except ValueError as ex:
                        self.log(f"[Login navigateur] Pas encore valide : {ex}")
                        tried.add(t)
                        time.sleep(4)
                        continue
                    accounts.finish_temp_login(h, True)
                    self.log(f"[Login navigateur] Connexion détectée : '{name}' — fermeture du navigateur.")
                    self.ui(lambda: done(True, f"Compte '{name}' " + ("mis à jour." if existed else "ajouté avec succès !")))
                    return
                if h["proc"].poll() is not None:
                    user_closed = True
                    break
                if time.time() - last_ping > 20:
                    last_ping = time.time()
                    self.log("[Login navigateur] En attente — connecte-toi (ou finalise le captcha/2FA) sur roblox.com.")
                time.sleep(1.5)
            if user_closed:
                accounts.finish_temp_login(h, False)
                self.ui(lambda: done(False, "Navigateur fermé sans connexion à Roblox."))
            else:
                accounts.finish_temp_login(h, False)
                self.ui(lambda: done(False, "Timeout : aucune connexion détectée en 15 min."))

        threading.Thread(target=work, daemon=True).start()

    def _add_worker(self, token, note, win):
        try:
            name, existed = accounts.add_account(token, notes=note)
            ok = True
            msg = f"Compte '{name}' " + ("mis à jour." if existed else "ajouté.")
        except Exception as e:
            ok = False
            msg = f"Ajout impossible : {e}"
        self.ui(lambda: self._add_done(ok, msg))

    def refresh_names_thread(self):
        accs = accounts.get_accounts()
        self.set_status(f"Actualisation de {len(accs)} compte(s)...")

        def work():
            for a in accs:
                tok = accounts.get_token(a["id"])
                info = accounts.fetch_user_info(tok) if tok else None
                new_name = info["name"] if info else None
                if new_name and new_name != a["name"]:
                    accounts.update_account(a["id"], name=new_name)
                    if self.current_account == a["name"]:
                        self.current_account = new_name
                        self.update_header()
            self.ui(lambda: (self.refresh_accounts_list(),
                             self.set_status("Liste actualisée.")))

        threading.Thread(target=work, daemon=True).start()

    def reload_profiles_menu(self):
        self._profiles = accounts.list_chrome_profiles()

    def _fetch_place_name(self, place_id):
        try:
            req = urllib.request.Request(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe")
            with urllib.request.urlopen(req, timeout=8) as r:
                uid = json.loads(r.read()).get("universeId")
            if not uid:
                return None
            req2 = urllib.request.Request(f"https://games.roblox.com/v1/games?universeIds={uid}")
            with urllib.request.urlopen(req2, timeout=8) as r:
                d = json.loads(r.read()).get("data", [])
                return d[0].get("name") if d else None
        except Exception:
            return None

    def api_launch_home(self):
        return self.api_launch(self._play_account(), None)

    def _play_account(self):
        acc = self.selected_account()
        if acc:
            return acc["name"]
        accs = accounts.get_accounts()
        if accs:
            self._sel_acc = accs[0]
            self._sel_id = accs[0].get("id")
            return accs[0]["name"]
        return None

    def api_launch(self, account_name, target=None):
        acc = accounts.find_account(account_name or "")
        if not acc:
            self.ui(lambda: messagebox.showwarning("Aucun compte", "Sélectionne un compte dans la liste (ou ajoute-en un) avant de lancer."))
            return False, "Aucun compte sélectionné."
        exe = core.find_roblox_exe()
        if not exe:
            from tkinter import filedialog
            self.ui(lambda: messagebox.showwarning("Roblox introuvable", "RobloxPlayerBeta.exe introuvable. Lance Roblox une fois, ou indique le chemin."))
            path = [None]

            def pick():
                path[0] = filedialog.askopenfilename(title="RobloxPlayerBeta.exe", filetypes=[("Executable", "*.exe")])
                if path[0]:
                    core.save_custom_exe(path[0])
            self.ui(lambda: threading.Thread(target=pick, daemon=True).start())
            return False, "Roblox introuvable."
        token = accounts.get_token(acc["id"])
        if not token:
            return False, f"Token illisible pour {acc['name']}."
        self.log(f"[Lancement] Récupération du ticket pour '{acc['name']}' (navigateur sécurisé)...")
        ticket, code = accounts.get_ticket_via_cdp(token, self.log)
        if not ticket:
            self.log(f"[Lancement] Voie navigateur échouée ({code}), essai direct...")
            ticket = accounts.get_auth_ticket(token)
            code = accounts._last_ticket_status["code"]
        if not ticket:
            m = f"Ticket refusé par Roblox (HTTP {code})."
            self.log(f"[Lancement] {m}")
            return False, m
        uri = accounts.build_launch_uri(ticket, target)
        if self.var_force.get():
            try:
                n = core.unlock_all()
            except Exception as e:
                n = 0
                self.log(f"[Multi-instance] ERREUR strip avant lancement : {e}")
            self.log(f"[Multi-instance] Strip des instances existantes : {n} verrou(s) fermé(s).")
        try:
            core.ensure_fps_cap()
        except Exception:
            pass
        try:
            proc = subprocess.Popen([exe, uri], cwd=os.path.dirname(exe))
        except Exception as e:
            return False, f"Échec du lancement : {e}"
        time.sleep(0.9)
        place_id = (target or {}).get("place_id")
        self.rejoin.track(proc.pid, acc["name"], target)
        if place_id:
            self.current_place = place_id
        self.current_account = acc["name"]
        self.ui(self.update_header)
        if place_id:

            def work():
                nm = self._fetch_place_name(place_id)
                if nm:
                    accounts.add_recent(place_id, nm, acc["name"])
                    self.ui(self.reload_recents)
            threading.Thread(target=work, daemon=True).start()
            self.log(f"[OK] '{acc['name']}' lancé sur la place {place_id} (PID {proc.pid}).")
        else:
            self.log(f"[OK] '{acc['name']}' connecté sur Roblox Home (PID {proc.pid}).")
        return True, f"PID {proc.pid}"

    def build_view_comptes_sidebar(self):
        v = ctk.CTkFrame(self.container, fg_color="transparent")
        self.views["comptes"] = v
        body = ctk.CTkFrame(v, fg_color="transparent")
        body.pack(fill="both", expand=True)

        left = ctk.CTkFrame(body, width=300, fg_color="transparent")
        left.pack(side="left", fill="y", padx=(0, 12))
        lt = ctk.CTkFrame(left, fg_color="transparent")
        lt.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(lt, text="MES COMPTES", font=("Segoe UI", 11, "bold"), text_color=MUT, anchor="w").pack(side="left")
        ctk.CTkButton(lt, text="↻", width=32, height=26, corner_radius=6,
                      fg_color=CARD2, hover_color="#26303f", text_color=MUT,
                      command=self.refresh_names_thread).pack(side="right")
        self.acc_list = ctk.CTkScrollableFrame(left, width=290, fg_color=CARD)
        self.acc_list.pack(fill="both", expand=True)

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)
        card = ctk.CTkFrame(right, fg_color=CARD, corner_radius=12)
        card.pack(fill="x")
        ctk.CTkLabel(card, text="CHOISIR UN JEU", font=("Segoe UI", 11, "bold"), text_color=MUT, anchor="w").pack(fill="x", padx=16, pady=(12, 2))
        r1 = ctk.CTkFrame(card, fg_color="transparent")
        r1.pack(fill="x", padx=16, pady=4)
        self.ent_target = ctk.CTkEntry(r1, font=FONT, placeholder_text="Place ID ou lien : roblox.com/games/2753915549 ou lien serveur privé")
        self.ent_target.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(r1, text="Charger l'aperçu", width=130, fg_color=CARD2, hover_color="#26303f",
                      command=self.load_game_preview).pack(side="left", padx=(8, 0))

        pv = ctk.CTkFrame(card, fg_color=CARD2, corner_radius=10)
        pv.pack(fill="x", padx=16, pady=(8, 6))
        self.pv_icon = ctk.CTkLabel(pv, text="🎮", font=("Segoe UI", 44), width=120, height=120)
        self.pv_icon.pack(side="left", padx=14, pady=12)
        pvinfo = ctk.CTkFrame(pv, fg_color="transparent")
        pvinfo.pack(side="left", fill="both", expand=True, padx=(0, 12), pady=12)
        self.pv_name = ctk.CTkLabel(pvinfo, text="Entre un Place ID ou colle un lien de jeu,\npuis clique « Charger l'aperçu ».",
                                    font=FONT_B, anchor="w", justify="left", text_color=MUT)
        self.pv_name.pack(anchor="w")
        self.pv_mode = ctk.CTkLabel(pvinfo, text="", font=FONT_S, anchor="w", text_color=MUT)
        self.pv_mode.pack(anchor="w")
        self._preview_target = None
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(4, 14))
        self.btn_join = ctk.CTkButton(btns, text="▶  REJOINDRE AVEC LE COMPTE SÉLECTIONNÉ", font=("Segoe UI", 13, "bold"),
                                      height=46, corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_H, text_color="#000000",
                                      command=self.launch_selected_game)
        self.btn_join.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(btns, text="Home", width=90, height=46, corner_radius=10,
                      fg_color=CARD2, hover_color="#26303f",
                      command=lambda: threading.Thread(target=self.api_launch_home, daemon=True).start()).pack(side="left", padx=(8, 0))

        card2 = ctk.CTkFrame(right, fg_color=CARD, corner_radius=12)
        card2.pack(fill="x", pady=(10, 0))
        c2 = ctk.CTkFrame(card2, fg_color="transparent")
        c2.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(c2, text="REJOINDRE UN JOUEUR", font=("Segoe UI", 11, "bold"), text_color=MUT).pack(side="left")
        self.ent_player = ctk.CTkEntry(c2, width=170, font=FONT, placeholder_text="Pseudo Roblox",
                                       justify="center")
        self.ent_player.pack(side="right")
        self.btn_joinp = ctk.CTkButton(c2, text="🎯  Rejoindre sa partie", width=170, font=FONT_B,
                                       fg_color=ACCENT, hover_color=ACCENT_H, text_color="#000000",
                                       command=self.join_by_player)
        self.btn_joinp.pack(side="right", padx=(0, 8))
        self.ent_player.bind("<Return>", lambda e: self.join_by_player())

        rt = ctk.CTkFrame(right, fg_color="transparent")
        rt.pack(fill="x", pady=(10, 4))
        ctk.CTkLabel(rt, text="JEUX RÉCENTS", font=("Segoe UI", 11, "bold"), text_color=MUT, anchor="w").pack(side="left")
        ctk.CTkButton(rt, text="↻", width=32, height=26, corner_radius=6,
                      fg_color=CARD2, hover_color="#26303f", text_color=MUT,
                      command=self.reload_recents).pack(side="right")
        self.rec_grid = ctk.CTkScrollableFrame(right, fg_color=CARD, height=170)
        self.rec_grid.pack(fill="both", expand=True)

    def load_game_preview(self):
        raw = self.ent_target.get().strip()
        t = accounts.parse_join_target(raw)
        if not t:
            self.set_status("Lien ou Place ID non reconnu.")
            return
        self._preview_target = t
        pid = t["place_id"]
        mode_lbl = {"public": "Serveur public", "private": "Serveur privé (code détecté)", "job": "Job ID précis"}.get(t.get("mode", "public"), "")
        self.pv_name.configure(text=f"Chargement...", text_color=MUT)
        self.pv_mode.configure(text=mode_lbl)

        def work():
            img = self._game_icon_for(pid, 110) if pid else None
            name = self._fetch_place_name(pid) if pid else None
            if not name:
                name = f"Place {pid}"

            def upd():
                if img:
                    self.pv_icon.configure(image=img, text="")
                else:
                    self.pv_icon.configure(image=None, text="🎮")
                self.pv_name.configure(text=name, text_color=FG)
            self.ui(upd)

        threading.Thread(target=work, daemon=True).start()

    def launch_selected_game(self):
        acc = self.selected_account()
        if not acc:
            self.set_status("Sélectionne d'abord un compte à gauche.")
            return
        raw = self.ent_target.get().strip()
        t = accounts.parse_join_target(raw) or self._preview_target
        if not t:
            self.set_status("Entre un jeu (Place ID ou lien) d'abord.")
            return
        threading.Thread(target=lambda: self._launch_thread(t), daemon=True).start()

    def _launch_thread(self, target):
        ok, msg = self.api_launch(self._play_account(), target)
        self.ui(lambda: self.set_status(("Lancé — " + msg) if ok else ("Échec — " + msg)))

    def join_by_player(self):
        acc = self._play_account()
        if not acc:
            self.set_status("Aucun compte disponible — ajoute-en un d'abord.")
            return
        name = (self.ent_player.get() or "").strip().lstrip("@")
        if not name:
            self.set_status("Entre un pseudo Roblox à rejoindre.")
            return
        self.set_status(f"Recherche de '{name}'...")
        try:
            self.btn_joinp.configure(state="disabled")
        except Exception:
            pass
        threading.Thread(target=self._join_player_thread, args=(name,), daemon=True).start()

    def _join_player_thread(self, username):
        def reenable():
            try:
                self.btn_joinp.configure(state="normal")
            except Exception:
                pass

        u = accounts.resolve_player(username)
        if not u:
            self.ui(lambda: self.set_status(f"Joueur '{username}' introuvable."))
            self.ui(reenable)
            return
        p = accounts.get_player_presence(u["id"])
        if not p:
            self.ui(lambda: self.set_status("API présence indisponible — réessaie."))
            self.ui(reenable)
            return
        if p["status"] != "in_game":
            loc = p.get("last") or ""
            self.log(f"[Join joueur] {u['name']} : présence Roblox = {p['status']} ('{loc}') -> join impossible.")
            if p["status"] == "offline":
                detail = "hors ligne"
            elif loc and loc.lower() not in ("website", "site"):
                detail = f"sur '{loc}' (pas en partie)"
            else:
                detail = "sur le site / pas en partie"
            self.ui(lambda: self.set_status(f"{u['name']} n'est pas en jeu — {detail}. Si il vient de rejoindre, réessaie dans ~1 min."))
            self.ui(reenable)
            return
        if p["job_id"]:
            target = {"mode": "job", "place_id": p["place_id"], "code": p["job_id"]}
            self.log(f"[Join joueur] {u['name']} est sur la place {p['place_id']} -> joint le MÊME serveur.")
        else:
            target = {"mode": "public", "place_id": p["place_id"], "code": ""}
            self.log(f"[Join joueur] {u['name']} est sur la place {p['place_id']} (serveur inconnu -> serveur public).")
        ok, msg = self.api_launch(self._play_account(), target)
        if ok:
            self.ui(lambda: self.set_status(f"Joint la partie de {u['name']} ({msg})."))
        else:
            self.ui(lambda: self.set_status(f"Échec du join : {msg}"))
        self.ui(reenable)

    def reload_recents(self):
        if not hasattr(self, "rec_grid"):
            return
        host = self.rec_grid
        for w in host.winfo_children():
            w.destroy()
        rows = accounts.get_recent()
        if not rows:
            ctk.CTkLabel(host, text="Aucun jeu récent — lance un jeu et il apparaîtra ici.",
                         font=FONT_S, text_color=MUT).pack(pady=24)
            return
        for r in rows[:20]:
            pid = r["place_id"]
            row = ctk.CTkFrame(host, fg_color=CARD2, corner_radius=8)
            row.pack(fill="x", padx=6, pady=3)
            tt = {"mode": "public", "place_id": pid, "code": ""}

            def go(t=tt):
                threading.Thread(target=lambda: self._launch_thread(t), daemon=True).start()

            icon = ctk.CTkLabel(row, text="🎮", width=34, font=("Segoe UI", 16))
            icon.pack(side="left", padx=(10, 6), pady=6)

            mid = ctk.CTkFrame(row, fg_color="transparent")
            mid.pack(side="left", fill="x", expand=True)
            nm = r.get("name") or f"Place {pid}"
            ctk.CTkLabel(mid, text=(nm[:42] + "…") if len(nm) > 43 else nm,
                         font=FONT_B, anchor="w").pack(anchor="w")
            ctk.CTkLabel(mid, text=f"Place {pid} · {r.get('account', '')}",
                         font=("Segoe UI", 9), text_color=MUT, anchor="w").pack(anchor="w")

            ctk.CTkButton(row, text="▶", width=40, height=32, corner_radius=8,
                          fg_color=ACCENT, hover_color=ACCENT_H, text_color="#000000",
                          command=go).pack(side="right", padx=(6, 10), pady=6)

            if HAS_PIL:
                threading.Thread(target=self._fill_recent_icon, args=(icon, pid), daemon=True).start()

    def _fill_recent_icon(self, lbl, pid):
        img = self._game_icon_for(pid, 30)
        if not img:
            return

        def upd():
            try:
                if lbl.winfo_exists():
                    lbl.configure(image=img, text="")
            except Exception:
                pass

        self.ui(upd)

    def build_view_instances(self, title="Multi Roblox"):
        v = ctk.CTkFrame(self.container, fg_color="transparent")
        self.views["multi"] = v
        top = ctk.CTkFrame(v, fg_color="transparent")
        top.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(top, text=title, font=FONT_T).pack(side="left")
        self.sw_multi = ctk.CTkSwitch(top, text="Multi-instance", variable=self.var_force, font=FONT_B,
                                      progress_color=ACCENT, command=self.apply_feature_settings,
                                      onvalue=True, offvalue=False)
        self.sw_multi.pack(side="right")

        bar = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        bar.pack(fill="x", pady=(0, 8))
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)
        self._arm = {}
        self.btn_kill_sel = ctk.CTkButton(inner, text="Kill sélection", fg_color=ACCENT, hover_color=ACCENT_H, text_color="#000000",
                                          command=lambda: self._armed_kill(self.kill_selected, self.btn_kill_sel, "Kill sélection"))
        self.btn_kill_sel.pack(side="left")
        self.btn_kill_all = ctk.CTkButton(inner, text="Kill TOUT", fg_color=ACCENT, hover_color=ACCENT_H, text_color="#000000",
                                          command=lambda: self._armed_kill(self.kill_all, self.btn_kill_all, "Kill TOUT"))
        self.btn_kill_all.pack(side="left", padx=8)
        ctk.CTkButton(inner, text="Suspendre", fg_color=CARD2, hover_color="#26303f",
                      command=self.suspend_selected).pack(side="left", padx=(14, 0))
        ctk.CTkButton(inner, text="Reprendre", fg_color=CARD2, hover_color="#26303f",
                      command=self.resume_selected).pack(side="left", padx=8)
        ctk.CTkLabel(inner, text="Priorité :", font=FONT_B).pack(side="left", padx=(18, 4))
        self.opt_prio = ctk.CTkOptionMenu(inner, values=["High", "Normal", "Low"], font=FONT, width=110,
                                          fg_color=CARD2, button_color=CARD2, button_hover_color="#26303f",
                                          dropdown_fg_color=CARD2, dropdown_text_color=FG)
        self.opt_prio.set("High")
        self.opt_prio.pack(side="left")
        ctk.CTkButton(inner, text="Appliquer", width=90, fg_color=CARD2, hover_color="#26303f",
                      command=self.apply_priority).pack(side="left", padx=6)

        fps = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        fps.pack(fill="x", pady=(0, 8))
        finner = ctk.CTkFrame(fps, fg_color="transparent")
        finner.pack(fill="x", padx=12, pady=10)
        ctk.CTkLabel(finner, text="Limite FPS :", font=FONT_B).pack(side="left")
        self.e_fps = ctk.CTkEntry(finner, width=80, font=FONT, justify="center")
        self.e_fps.insert(0, str(self.settings.get("fps_default", 240)))
        self.e_fps.pack(side="left", padx=8)
        lbl_gfx = ctk.CTkLabel(finner, text="Qualité :", font=FONT_B)
        lbl_gfx.pack(side="left", padx=(18, 4))
        self.opt_gfx = ctk.CTkOptionMenu(finner, values=["Auto", "Perf++", "Perf", "Perf Render Max", "Équilibré", "Pro"], font=FONT, width=130,
                                         fg_color=CARD2, button_color=CARD2, button_hover_color="#26303f",
                                         dropdown_fg_color=CARD2, dropdown_text_color=FG)
        self.opt_gfx.set({v: k for k, v in core.GFX_LABELS.items()}.get(self.settings.get("gfx_quality", "auto"), "Auto"))
        self.opt_gfx.pack(side="left")
        _gt = lambda: GFX_TOOLTIPS.get(self.opt_gfx.get(), "")
        ToolTip(lbl_gfx, _gt)
        ToolTip(self.opt_gfx, _gt)
        info_btn = ctk.CTkLabel(finner, text="ℹ", font=FONT_B, text_color=ACCENT, cursor="hand2", width=18)
        info_btn.pack(side="left", padx=(2, 0))
        ToolTip(info_btn, _gt)
        ctk.CTkButton(finner, text="Appliquer FastFlags", fg_color=ACCENT, hover_color=ACCENT_H, text_color="#000000",
                      command=self.apply_fps).pack(side="left", padx=(10, 0))
        self.fps_status = ctk.CTkLabel(finner, text="", font=FONT_S, text_color=MUT)
        self.fps_status.pack(side="left", padx=12)

        tf = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        tf.pack(fill="both", expand=True)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Rtm.Treeview", background="#131924", foreground=FG, fieldbackground="#131924",
                        rowheight=28, borderwidth=0, font=("Segoe UI", 11))
        style.map("Rtm.Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
        style.configure("Rtm.Treeview.Heading", background=CARD2, foreground=FG,
                        font=("Segoe UI", 11, "bold"), relief="flat")
        cols = ("pid", "compte", "cpu", "ram", "status", "uptime")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", style="Rtm.Treeview", height=9)
        for col, txt, w in [("pid", "PID", 70), ("compte", "Compte", 140), ("cpu", "CPU %", 80),
                            ("ram", "RAM (MB)", 100), ("status", "Statut", 100), ("uptime", "Uptime", 90)]:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor="center", stretch=True)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        sb.pack(side="right", fill="y", pady=10, padx=(0, 10))

    def _sel_pids(self):
        return [int(self.tree.item(i)["values"][0]) for i in self.tree.selection()]

    def kill_selected(self):
        ok = []
        for pid in self._sel_pids():
            if core.kill_pid(pid):
                ok.append(pid)
        if ok:
            self.log(f"[Kill] Instance(s) fermée(s) : {', '.join(str(p) for p in ok)}.")
        else:
            self.set_status("Aucune instance sélectionnée.")

    def kill_all(self):
        rows = core.get_instances()
        if not rows:
            self.set_status("Aucune instance Roblox en cours.")
            return
        killed = [r["pid"] for r in rows if core.kill_pid(r["pid"])]
        self.log(f"[Kill] TOUT fermé : {len(killed)}/{len(rows)} instance(s) (PID {', '.join(str(p) for p in killed)}).")

    def _armed_kill(self, action, btn, label):
        if self._arm.get(id(btn)):
            self._arm[id(btn)] = False
            btn.configure(text=label)
            action()
            return
        self._arm[id(btn)] = True
        btn.configure(text="Confirmer ?")
        self.after(2500, lambda: self._disarm(btn, label))

    def _disarm(self, btn, label):
        if self._arm.get(id(btn)):
            self._arm[id(btn)] = False
            btn.configure(text=label)

    def suspend_selected(self):
        pids = [p for p in self._sel_pids() if core.suspend_pid(p)]
        if pids:
            self.log(f"[Instances] Suspendue(s) : {', '.join(str(p) for p in pids)}.")
        self.set_status(f"{len(pids)} instance(s) suspendue(s) — CPU libéré en arrière-plan.")

    def resume_selected(self):
        pids = [p for p in self._sel_pids() if core.resume_pid(p)]
        if pids:
            self.log(f"[Instances] Reprise(s) : {', '.join(str(p) for p in pids)}.")
        self.set_status(f"{len(pids)} instance(s) reprise(s).")

    def apply_priority(self):
        pids = [p for p in self._sel_pids() if core.set_priority(p, self.opt_prio.get())]
        if pids:
            self.log(f"[Instances] Priorité {self.opt_prio.get()} appliquée à : {', '.join(str(p) for p in pids)}.")
        self.set_status(f"Priorité {self.opt_prio.get()} appliquée à {len(pids)} instance(s).")

    def apply_fps(self):
        try:
            fps = int(self.e_fps.get())
            assert 1 <= fps <= 2000
        except (ValueError, AssertionError):
            messagebox.showerror("Invalide", "FPS entre 1 et 2000.")
            return
        gfx_label = self.opt_gfx.get()
        gfx_mode = core.GFX_LABELS.get(gfx_label, "auto")
        try:
            written = core.apply_fps_cap(fps, gfx_mode)
        except Exception as e:
            messagebox.showerror("Erreur", f"Écriture impossible : {e}")
            return
        s = self.settings
        s["fps_default"] = fps
        s["gfx_quality"] = gfx_mode
        core.save_settings(s)
        if written:
            self.fps_status.configure(text=f"OK — {len(written)} version(s). Redémarre Roblox.", text_color=GREEN)
            self.log(f"[FPS] Limite {fps} appliquée.")
            if gfx_mode != "auto":
                self.log(f"[FastFlags] Qualité graphique forcée : {gfx_label}.")
            else:
                self.log("[FastFlags] Qualité graphique : Auto (aucun forçage).")
        else:
            self.fps_status.configure(text="Aucune version trouvée (lance Roblox une fois).", text_color=AMBER)

    def build_view_features(self):
        v = ctk.CTkFrame(self.container, fg_color="transparent")
        self.views["features"] = v
        card = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(card, text="AUTO-REJOIN", font=("Segoe UI", 11, "bold"), text_color=MUT, anchor="w").pack(fill="x", padx=14, pady=(12, 0))
        r1 = ctk.CTkFrame(card, fg_color="transparent")
        r1.pack(fill="x", padx=14, pady=(4, 14))
        self.sw_ar = ctk.CTkSwitch(r1, text="Reconnecte automatiquement si tu es déconnecté", font=FONT_B,
                                   progress_color=ACCENT, command=self.apply_feature_settings)
        self.sw_ar.pack(side="left")
        ctk.CTkLabel(r1, text="Délai (s) :", font=FONT).pack(side="left", padx=(20, 4))
        self.e_ar_delay = ctk.CTkEntry(r1, width=50, justify="center")
        self.e_ar_delay.pack(side="left")
        ctk.CTkLabel(card, justify="left", anchor="w", font=FONT_S, text_color=MUT,
                     text="NB : tant que ce switch est ON, fermer une instance manuellement la relancera aussi.\n"
                          "Désactive-le avant de fermer un compte volontairement.").pack(fill="x", padx=14, pady=(0, 10))
        card2 = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        card2.pack(fill="x", pady=8)
        ctk.CTkLabel(card2, text="ANTI-AFK", font=("Segoe UI", 11, "bold"), text_color=MUT, anchor="w").pack(fill="x", padx=14, pady=(12, 0))
        r2 = ctk.CTkFrame(card2, fg_color="transparent")
        r2.pack(fill="x", padx=14, pady=(4, 14))
        self.sw_aa = ctk.CTkSwitch(r2, text="Empêche la déconnexion pour inactivité (touche F13 en fond)", font=FONT_B,
                                   progress_color=ACCENT, command=self.apply_feature_settings)
        self.sw_aa.pack(side="left")
        ctk.CTkLabel(r2, text="Intervalle (s) :", font=FONT).pack(side="left", padx=(14, 4))
        self.e_aa_int = ctk.CTkEntry(r2, width=60, justify="center")
        self.e_aa_int.pack(side="left")

    def build_view_logs(self):
        v = ctk.CTkFrame(self.container, fg_color="transparent")
        self.views["logs"] = v
        top = ctk.CTkFrame(v, fg_color="transparent")
        top.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(top, text="Vider", fg_color=CARD2, hover_color="#26303f",
                      command=lambda: self.logs_box.delete("1.0", "end")).pack(side="right")
        self.logs_box = ctk.CTkTextbox(v, font=("Consolas", 11), fg_color="#0a0e15", text_color="#9fd4ff")
        self.logs_box.pack(fill="both", expand=True)
        self.logs_box.bind("<Key>", self._log_key)
        self.logs_box.bind("<Button-1>", lambda e: self._log_to_end())
        self.logs_box.bind("<ButtonRelease-1>", lambda e: self._log_to_end())

    def _log_to_end(self):
        try:
            self.logs_box.tag_remove("sel", "1.0", "end")
            self.logs_box.mark_set("insert", "end")
            self.logs_box.see("end")
        except Exception:
            pass

    def _log_key(self, e):
        self._log_to_end()
        if e.keysym in ("BackSpace", "Delete"):
            return "break"
        if (e.state & 0x0004) and e.keysym.lower() == "x":
            return "break"

    def build_view_settings(self):
        v = ctk.CTkFrame(self.container, fg_color="transparent")
        self.views["settings"] = v

        # ── Mises à jour ──
        card_upd = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        card_upd.pack(fill="x", pady=(0, 8))
        r_upd = ctk.CTkFrame(card_upd, fg_color="transparent")
        r_upd.pack(fill="x", padx=14, pady=12)
        self.lbl_version = ctk.CTkLabel(r_upd, text=f"Version : {__version__}", font=FONT_B)
        self.lbl_version.pack(side="left")
        self.btn_check = ctk.CTkButton(r_upd, text="Vérifier les mises à jour", font=FONT_B, height=32,
                                       fg_color=CARD2, hover_color="#26303f", text_color=FG,
                                       command=self._check_update_ui)
        self.btn_check.pack(side="right")
        self._upd_info_frame = ctk.CTkFrame(card_upd, fg_color="transparent")
        self._upd_info_frame.pack(fill="x", padx=14, pady=(0, 10))
        self.lbl_upd_info = ctk.CTkLabel(self._upd_info_frame, text="", font=FONT_S, text_color=MUT, anchor="w", justify="left")
        self.lbl_upd_info.pack(side="left")
        self.btn_download = ctk.CTkButton(self._upd_info_frame, text="Mettre à jour", font=FONT_B, height=30,
                                          fg_color=GREEN, hover_color=GREEN_H, text_color="#000000",
                                          command=self._download_update_ui)
        self.btn_download.pack(side="right")
        self.btn_download.pack_forget()
        self._pending_update = None

        # ── Thème ──
        card1 = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        card1.pack(fill="x", pady=(0, 8))
        r1 = ctk.CTkFrame(card1, fg_color="transparent")
        r1.pack(fill="x", padx=14, pady=12)
        ctk.CTkLabel(r1, text="Thème :", font=FONT_B).pack(side="left")
        self.opt_theme = ctk.CTkOptionMenu(r1, values=list(THEMES.keys()), font=FONT, width=160,
                                           fg_color=CARD2, button_color=CARD2, button_hover_color="#26303f",
                                           dropdown_fg_color=CARD2, dropdown_text_color=FG,
                                           command=self.change_theme)
        self.opt_theme.set(self.theme_name)
        self.opt_theme.pack(side="left", padx=8)
        ctk.CTkLabel(r1, text="(appliqué au redémarrage de l'app)", font=FONT_S, text_color=MUT).pack(side="left", padx=4)

        # ── Sécurité ──
        card2 = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        card2.pack(fill="x", pady=8)
        ctk.CTkLabel(card2, justify="left", anchor="w", font=FONT_S, text_color=MUT,
                     text="Les cookies sont chiffrés avec DPAPI Windows (liés à ton compte utilisateur).\n"
                          "Ils ne quittent jamais ce PC, sauf requête d'authentification vers roblox.com.\n"
                           "Ne partage jamais ton .ROBLOSECURITY : ça donne un accès total au compte.").pack(padx=14, pady=12, anchor="w")

    def change_theme(self, choice):
        if choice == self.theme_name:
            return
        s = self.settings
        s["theme"] = choice
        core.save_settings(s)
        self.log(f"[Thème] Passage en '{choice}' — redémarrage...")
        self._restart = True
        self.after(250, self.destroy)

    def _check_update_ui(self):
        self.btn_check.configure(state="disabled", text="Vérification…")
        self.lbl_upd_info.configure(text="")
        self.btn_download.pack_forget()
        self._pending_update = None

        def _worker():
            info = updater.check_update(__version__)
            self.after(0, lambda: self._on_update_result(info))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_result(self, info):
        self.btn_check.configure(state="normal", text="Vérifier les mises à jour")
        if info:
            size_mb = info["size"] / (1024 * 1024) if info["size"] else 0
            size_str = f" ({size_mb:.1f} Mo)" if size_mb else ""
            notes_preview = ""
            if info["notes"]:
                first_line = info["notes"].split("\n")[0][:120]
                notes_preview = f"\n{first_line}"
            self.lbl_upd_info.configure(
                text=f"v{info['version']} disponible{size_str}{notes_preview}",
                text_color=GREEN)
            self._pending_update = info
            if info.get("url"):
                self.btn_download.pack(side="right")
            else:
                self.lbl_upd_info.configure(text=self.lbl_upd_info.cget("text") + "\n(pas de fichier exe trouvé)")
        else:
            self.lbl_upd_info.configure(text="À jour !", text_color=MUT)

    def _download_update_ui(self):
        info = self._pending_update
        if not info or not info.get("url"):
            return
        self.btn_download.configure(state="disabled", text="Téléchargement…")
        self.btn_check.configure(state="disabled")
        self.lbl_upd_info.configure(text=f"Téléchargement de v{info['version']}…", text_color=FG)

        def _worker():
            def _progress(downloaded, total):
                pct = int(downloaded / total * 100) if total else 0
                self.after(0, lambda p=pct: self.lbl_upd_info.configure(text=f"Téléchargement… {p}%"))
            try:
                path = updater.download_update(info["url"], progress_fn=_progress)
                self.after(0, lambda: self._on_download_done(path))
            except Exception as e:
                self.after(0, lambda: self._on_download_error(str(e)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_download_done(self, path):
        self.btn_download.configure(state="normal", text="Mettre à jour")
        self.btn_check.configure(state="normal")
        self.lbl_upd_info.configure(text="Téléchargé ! Redémarrage…", text_color=GREEN)
        self.log(f"[Update] Fichier téléchargé, application de la mise à jour…")
        self.after(800, lambda: updater.apply_update(path))

    def _on_download_error(self, err):
        self.btn_download.configure(state="normal", text="Mettre à jour")
        self.btn_check.configure(state="normal")
        self.lbl_upd_info.configure(text=f"Erreur : {err}", text_color=RED)
        self.log(f"[Update] Erreur téléchargement : {err}")

    def _auto_check_update(self):
        def _worker():
            info = updater.check_update(__version__)
            if info:
                self.after(0, lambda: self.log(f"[Update] v{info['version']} disponible — Parameters pour mettre à jour."))
        threading.Thread(target=_worker, daemon=True).start()

    def apply_feature_settings(self):
        try:
            ar_d = max(2, int(float(self.e_ar_delay.get() or 6))) if hasattr(self, "e_ar_delay") else 6
        except ValueError:
            ar_d = 6
        try:
            aa_i = max(15, int(float(self.e_aa_int.get() or 120))) if hasattr(self, "e_aa_int") else 120
        except ValueError:
            aa_i = 120
        s = {
            "auto_rejoin": bool(self.sw_ar.get()) if hasattr(self, "sw_ar") else False,
            "ar_delay": ar_d,
            "anti_afk": bool(self.sw_aa.get()) if hasattr(self, "sw_aa") else False,
            "aa_interval": aa_i,
            "force_mutex": bool(self.var_force.get()),
            "fps_default": self.settings.get("fps_default", 240),
            "gfx_quality": self.settings.get("gfx_quality", "auto"),
            "theme": self.theme_name,
        }
        self.settings = s
        core.save_settings(s)
        self.rejoin.enabled = s["auto_rejoin"]
        self.rejoin.delay = ar_d
        self.antiafk.enabled = s["anti_afk"]
        self.antiafk.interval = aa_i
        if s["auto_rejoin"]:
            self.rejoin.start()
        if s["anti_afk"]:
            self.antiafk.start()
        self.set_status("Paramètres appliqués.")
        self._sync_guardian()

    def refresh_table_loop(self):
        if self._closing:
            return
        try:
            sel = {int(self.tree.item(i)["values"][0]) for i in self.tree.selection()}
            rows = core.get_instances()
            ctx = getattr(self.rejoin, "contexts", {})
            pids_now = {r["pid"] for r in rows}
            prev = getattr(self, "_prev_pids", None)
            if prev is None:
                self._prev_pids = pids_now
            else:
                for pid in sorted(pids_now - prev):
                    acc = ctx.get(pid, {}).get("account")
                    extra = f" — {acc}" if acc else ""
                    self.log(f"[Instance] Ouverte (PID {pid}){extra}.")
                for pid in sorted(prev - pids_now):
                    acc = ctx.get(pid, {}).get("account")
                    extra = f" — {acc}" if acc else ""
                    self.log(f"[Instance] Fermée (PID {pid}){extra}.")
                self._prev_pids = pids_now
            self.tree.delete(*self.tree.get_children())
            for i, r in enumerate(rows):
                acc_name = ""
                if r["pid"] in ctx:
                    acc_name = ctx[r["pid"]].get("account", "")
                tags = ["odd"] if i % 2 else []
                if r["suspended"]:
                    status = "Suspendu"
                elif not r.get("visible", True):
                    status = "Arrière-plan"
                else:
                    status = "En jeu/app"
                iid = self.tree.insert("", "end", values=(
                    r["pid"], acc_name or "—", f"{r['cpu']:.0f}", f"{r['mem_mb']:.0f}",
                    status, r["uptime"]), tags=tags)
                if r["pid"] in sel:
                    self.tree.selection_add(iid)
        except Exception:
            pass
        self.after(2000, self.refresh_table_loop)

    def destroy(self):
        self._closing = True
        core.stop_guardian()
        super().destroy()


if __name__ == "__main__":
    try:
        _crash_log = open(os.path.join(accounts.APP_DIR, "namachan_crash.log"), "a", encoding="utf-8")
        faulthandler.enable(_crash_log)

        def _hook(tp, val, tb):
            try:
                _crash_log.write("\n=== CRASH ===\n")
                traceback.print_exception(tp, val, tb, file=_crash_log)
                _crash_log.flush()
            except Exception:
                pass
        sys.excepthook = _hook
        threading.excepthook = lambda a: _hook(a.exc_type, a.exc_value, a.exc_traceback)
    except Exception:
        pass
    while True:
        app = App()
        app.mainloop()
        if not getattr(app, "_restart", False):
            break
