import ctypes
import threading
import time
from ctypes import wintypes

import accounts
import core

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_F13 = 0x7C


def get_roblox_windows():
    hwnds = []
    proto_pids = {p["pid"] for p in core.get_instances()}
    if not proto_pids:
        return hwnds

    @EnumWindowsProc
    def cb(hwnd, lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in proto_pids and user32.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True

    EnumWindows(cb, 0)
    return hwnds


class AntiAFK:
    def __init__(self, log=lambda m: None):
        self.enabled = False
        self.interval = 120
        self._stop = threading.Event()
        self._thread = None
        self.log = log

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            if self.enabled:
                try:
                    sent = 0
                    for hwnd in get_roblox_windows():
                        lp_down = 0x00000001 | (0x5B << 16)
                        lp_up = 0xC0000001 | (0x5B << 16)
                        if user32.PostMessageW(hwnd, WM_KEYDOWN, VK_F13, lp_down):
                            user32.PostMessageW(hwnd, WM_KEYUP, VK_F13, lp_up)
                            sent += 1
                    if sent:
                        self.log(f"[Anti-AFK] Signal envoyé à {sent} fenêtre(s).")
                except Exception as e:
                    self.log(f"[Anti-AFK] Erreur: {e}")
            self._stop.wait(max(10, int(self.interval)))


class AutoRejoin:
    def __init__(self, launch_fn, log=lambda m: None):
        self.enabled = False
        self.delay = 6
        self.launch_fn = launch_fn
        self.log = log
        self.contexts = {}
        self.rejoining = set()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def track(self, pid, account_name, target):
        self.contexts[pid] = {"account": account_name, "target": target}

    def _run(self):
        while not self._stop.is_set():
            if self.enabled:
                try:
                    alive_pids = {p["pid"] for p in core.get_instances()}
                    dead = [pid for pid in self.contexts if pid not in alive_pids]
                    for pid in dead:
                        ctx = self.contexts.pop(pid)
                        key = (ctx["account"], str(ctx["target"]))
                        if not ctx.get("target"):
                            continue
                        if key in self.rejoining:
                            continue
                        acc = accounts.find_account(ctx["account"])
                        if not acc:
                            continue
                        self.rejoining.add(key)
                        t = threading.Thread(target=self._do_rejoin, args=(ctx, key), daemon=True)
                        t.start()
                except Exception as e:
                    self.log(f"[Auto-Rejoin] Erreur: {e}")
            self._stop.wait(3)

    def _do_rejoin(self, ctx, key):
        try:
            self.log(f"[Auto-Rejoin] {ctx['account']} déconnecté — relance dans {self.delay}s...")
            time.sleep(max(2, int(self.delay)))
            ok, msg = self.launch_fn(ctx["account"], ctx["target"])
            self.log(("[Auto-Rejoin] Relancé : " + msg) if ok else ("[Auto-Rejoin] Échec : " + msg))
        finally:
            time.sleep(30)
            self.rejoining.discard(key)
