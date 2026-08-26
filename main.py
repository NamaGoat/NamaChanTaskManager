import ctypes
import glob
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk, messagebox, filedialog

import psutil

ROBLOX_PROCESS = "RobloxPlayerBeta.exe"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
LOCALAPPDATA = os.getenv("LOCALAPPDATA", "")
VERSIONS_DIR = os.path.join(LOCALAPPDATA, "Roblox", "Versions")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DUPLICATE_SAME_ACCESS = 0x0002
DUPLICATE_CLOSE_SOURCE = 0x0001
SystemExtendedHandleInformation = 64
ObjectTypeInformation = 2
ObjectNameInformation = 1
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004


class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object", ctypes.c_void_p),
        ("ProcessId", ctypes.c_size_t),
        ("Handle", ctypes.c_void_p),
        ("GrantedAccess", ctypes.c_ulong),
        ("CreatorBackTraceIndex", ctypes.c_ushort),
        ("ObjectTypeIndex", ctypes.c_ushort),
        ("HandleAttributes", ctypes.c_ulong),
        ("Reserved", ctypes.c_ulong),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_void_p),
    ]


kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.DuplicateHandle.restype = wintypes.BOOL
kernel32.DuplicateHandle.argtypes = [
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]
ntdll.NtQuerySystemInformation.restype = ctypes.c_long
ntdll.NtQuerySystemInformation.argtypes = [
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong),
]
ntdll.NtQueryObject.restype = ctypes.c_long
ntdll.NtQueryObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong),
]
ntdll.NtSuspendProcess.restype = ctypes.c_long
ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
ntdll.NtResumeProcess.restype = ctypes.c_long
ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]


def nt_query_name(handle, info_class):
    size = 4096
    for _ in range(5):
        buf = ctypes.create_string_buffer(size)
        ret_len = ctypes.c_ulong()
        status = ntdll.NtQueryObject(handle, info_class, buf, size, ctypes.byref(ret_len))
        if status == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, ret_len.value or size * 2)
            continue
        if status != 0:
            return None
        try:
            us = ctypes.cast(buf, ctypes.POINTER(UNICODE_STRING)).contents
            if us.Buffer and us.Length > 0:
                return ctypes.wstring_at(us.Buffer, us.Length // 2)
        except Exception:
            return None
        return None
    return None


def close_single_instance_mutex(pid):
    closed = 0
    length = 0x400000
    while True:
        buf = ctypes.create_string_buffer(length)
        ret_len = ctypes.c_ulong()
        status = ntdll.NtQuerySystemInformation(
            SystemExtendedHandleInformation, buf, length, ctypes.byref(ret_len)
        )
        if status != STATUS_INFO_LENGTH_MISMATCH:
            break
        length *= 2
        if length > 0x4000000:
            return 0
    if status != 0:
        return 0

    entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
    base = ctypes.addressof(buf)
    count = min(ret_len.value // entry_size if ret_len.value else 0, length // entry_size)
    targets = []
    for i in range(count):
        entry = SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_address(base + i * entry_size)
        if entry.ProcessId == pid:
            if (entry.GrantedAccess & 0x0012019F) == 0x0012019F:
                continue
            targets.append(entry.Handle)

    h_proc = kernel32.OpenProcess(PROCESS_DUP_HANDLE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h_proc:
        return 0
    try:
        for handle in targets:
            dup = wintypes.HANDLE()
            if not kernel32.DuplicateHandle(h_proc, handle, kernel32.GetCurrentProcess(), ctypes.byref(dup), 0, False, DUPLICATE_SAME_ACCESS):
                continue
            try:
                type_name = nt_query_name(dup, ObjectTypeInformation)
                if not type_name or type_name != "Mutant":
                    continue
                obj_name = nt_query_name(dup, ObjectNameInformation)
                if obj_name and "ROBLOX_SINGLEINSTANCE" in obj_name.upper():
                    if kernel32.DuplicateHandle(h_proc, handle, None, None, 0, False, DUPLICATE_CLOSE_SOURCE):
                        closed += 1
            finally:
                kernel32.CloseHandle(dup)
    finally:
        kernel32.CloseHandle(h_proc)
    return closed


def suspend_pid(pid):
    h = kernel32.OpenProcess(0x0800, False, pid)
    if not h:
        return False
    try:
        return ntdll.NtSuspendProcess(h) == 0
    finally:
        kernel32.CloseHandle(h)


def resume_pid(pid):
    h = kernel32.OpenProcess(0x0800, False, pid)
    if not h:
        return False
    try:
        return ntdll.NtResumeProcess(h) == 0
    finally:
        kernel32.CloseHandle(h)


def load_settings():
    try:
        with open(SETTINGS_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def find_roblox_exe():
    settings = load_settings()
    custom = settings.get("roblox_exe")
    if custom and os.path.isfile(custom):
        return custom
    candidates = glob.glob(os.path.join(VERSIONS_DIR, "version-*", ROBLOX_PROCESS))
    if candidates:
        candidates.sort(key=lambda p: os.path.getmtime(os.path.dirname(p)), reverse=True)
        return candidates[0]
    return None


def apply_fps_cap(fps):
    versions = [d for d in glob.glob(os.path.join(VERSIONS_DIR, "version-*")) if os.path.isdir(d)]
    written = []
    for v in versions:
        cs_dir = os.path.join(v, "ClientSettings")
        os.makedirs(cs_dir, exist_ok=True)
        path = os.path.join(cs_dir, "ClientAppSettings.json")
        data = {}
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["DFIntTaskSchedulerTargetFps"] = str(fps)
        data["FFlagTaskSchedulerLimitTargetFpsTo2402"] = "False"
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        written.append(path)
    return written


def get_roblox_processes():
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "create_time", "cpu_percent", "status"]):
        try:
            if p.info["name"] and p.info["name"].lower() == ROBLOX_PROCESS.lower():
                procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return procs


def fmt_uptime(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}"


BG = "#1e1f22"
FG = "#e6e6e6"
ACCENT = "#5865f2"
ROW_ALT = "#26272b"

FONT = ("Segoe UI", 9)
FONT_B = ("Segoe UI", 9, "bold")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Roblox Task Manager")
        self.geometry("780x520")
        self.minsize(700, 450)
        self.configure(bg=BG)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground=BG, font=FONT)
        style.configure("TButton", background="#3a3b40", foreground=FG, padding=4)
        style.map("TButton",
                  background=[("active", "#4a4b52"), ("disabled", "#2a2a2e")],
                  foreground=[("disabled", "#77777c")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#6875ff"), ("disabled", "#33345a")])
        style.configure("Danger.TButton", background="#da373c")
        style.map("Danger.TButton", background=[("active", "#e84850")])
        style.configure("TSpinbox", arrowsize=12, buttonbackground="#3a3b40", fieldbackground="#2a2b30")
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("TLabelframe", background=BG, foreground=FG, bordercolor="#3a3b40")
        style.configure("TLabelframe.Label", background=BG, foreground=FG, font=FONT_B)
        style.configure("Treeview", background="#25262a", foreground=FG, fieldbackground="#25262a",
                        rowheight=24, borderwidth=0)
        style.map("Treeview",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background="#2f3036", foreground=FG, font=FONT_B, relief="flat")
        style.map("Treeview.Heading", background=[("active", "#3a3b42")])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#2a2b30", borderwidth=0)

        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 4))

        ttk.Label(top, text="Instances:", font=FONT_B).pack(side="left")
        self.count_var = tk.IntVar(value=1)
        spin = ttk.Spinbox(top, from_=1, to=20, width=4, textvariable=self.count_var)
        spin.pack(side="left", padx=(4, 8))

        self.force_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Force multi-instance", variable=self.force_var).pack(side="left", padx=(0, 8))

        self.launch_btn = ttk.Button(top, text="Launch", command=self.launch_instances, style="Accent.TButton")
        self.launch_btn.pack(side="left")

        mid = ttk.Frame(self)
        mid.pack(fill="x", padx=10, pady=4)

        self.kill_btn = ttk.Button(mid, text="Kill selected", command=self.kill_selected, style="Danger.TButton")
        self.kill_btn.pack(side="left")
        ttk.Button(mid, text="Kill ALL", command=self.kill_all, style="Danger.TButton").pack(side="left", padx=(6, 12))
        self.suspend_btn = ttk.Button(mid, text="Suspend", command=self.suspend_selected)
        self.suspend_btn.pack(side="left")
        self.resume_btn = ttk.Button(mid, text="Resume", command=self.resume_selected)
        self.resume_btn.pack(side="left", padx=(6, 12))

        ttk.Label(mid, text="Priority:").pack(side="left")
        self.priority_var = tk.StringVar(value="High")
        prio = ttk.Combobox(mid, textvariable=self.priority_var, values=["Low", "Normal", "High"],
                            state="readonly", width=7)
        prio.pack(side="left", padx=(4, 6))
        ttk.Button(mid, text="Apply priority", command=self.apply_priority).pack(side="left")

        fps_frame = ttk.Labelframe(self, text="Frame cap (FastFlags)", padding=6)
        fps_frame.pack(fill="x", padx=10, pady=6)

        ttk.Label(fps_frame, text="FPS limit:").pack(side="left")
        self.fps_var = tk.IntVar(value=240)
        self.fps_spin = ttk.Spinbox(fps_frame, from_=1, to=2000, increment=10, width=6, textvariable=self.fps_var)
        self.fps_spin.pack(side="left", padx=(4, 8))
        ttk.Button(fps_frame, text="Apply FPS cap", command=self.on_apply_fps, style="Accent.TButton").pack(side="left")
        self.fps_status = ttk.Label(fps_frame, text="")
        self.fps_status.pack(side="left", padx=10)

        columns = ("pid", "cpu", "ram", "status", "uptime")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=10)
        for col, txt, w, anchor in [
            ("pid", "PID", 90, "center"),
            ("cpu", "CPU %", 90, "center"),
            ("ram", "RAM (MB)", 110, "center"),
            ("status", "Status", 120, "center"),
            ("uptime", "Uptime", 110, "center"),
        ]:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor=anchor, stretch=True)
        self.tree.tag_configure("odd", background=ROW_ALT)
        self.tree.tag_configure("suspended", foreground="#e6b84c")
        self.tree.pack(fill="both", expand=True, padx=10, pady=6)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        self.status_lbl = ttk.Label(bottom, text="Ready.", anchor="w")
        self.status_lbl.pack(side="left", fill="x", expand=True)
        self.count_lbl = ttk.Label(bottom, text="0 instance(s)", font=FONT_B)
        self.count_lbl.pack(side="right")

        self.refresh_table()

    def log(self, msg):
        self.status_lbl.config(text=msg)

    def refresh_table(self):
        selected_pids = {self.tree.item(i)["values"][0] for i in self.tree.selection()}
        self.tree.delete(*self.tree.get_children())
        procs = get_roblox_processes()
        for idx, p in enumerate(procs):
            try:
                mem_mb = p.info["memory_info"].rss / (1024 * 1024)
                up = fmt_uptime(time.time() - p.info["create_time"])
                cpu = p.cpu_percent(None)
                suspended = p.info["status"] == psutil.STATUS_STOPPED
                tags = []
                if idx % 2 == 1:
                    tags.append("odd")
                if suspended:
                    tags.append("suspended")
                vals = (p.pid, f"{cpu:.1f}", f"{mem_mb:.0f}", "Suspended" if suspended else "Running", up)
                iid = self.tree.insert("", "end", values=vals, tags=tags)
                if p.pid in selected_pids:
                    self.tree.selection_add(iid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        n = len(procs)
        self.count_lbl.config(text=f"{n} instance(s)")
        self.after(2000, self.refresh_table)

    def _selected_pids(self):
        return [int(self.tree.item(i)["values"][0]) for i in self.selection()]

    def selection(self):
        return self.tree.selection()

    def kill_selected(self):
        pids = self._selected_pids()
        killed = 0
        for pid in pids:
            try:
                psutil.Process(pid).terminate()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                self.log(f"Cannot kill PID {pid}: {e}")
        self.log(f"Killed {killed} process(es).")

    def kill_all(self):
        if not messagebox.askyesno("Confirm", "Close ALL Roblox instances?"):
            return
        killed = 0
        for p in get_roblox_processes():
            try:
                p.terminate()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self.log(f"Killed {killed} process(es).")

    def suspend_selected(self):
        ok = sum(1 for pid in self._selected_pids() if suspend_pid(pid))
        self.log(f"Suspended {ok} process(es). Background instances use ~0 CPU while suspended.")

    def resume_selected(self):
        ok = sum(1 for pid in self._selected_pids() if resume_pid(pid))
        self.log(f"Resumed {ok} process(es).")

    def apply_priority(self):
        classes = {
            "Low": psutil.IDLE_PRIORITY_CLASS,
            "Normal": psutil.NORMAL_PRIORITY_CLASS,
            "High": psutil.HIGH_PRIORITY_CLASS,
        }
        cls = classes[self.priority_var.get()]
        done = 0
        for pid in self._selected_pids():
            try:
                psutil.Process(pid).nice(cls)
                done += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                self.log(f"Cannot set priority for PID {pid}: {e}")
        self.log(f"Priority '{self.priority_var.get()}' applied to {done} process(es).")

    def on_apply_fps(self):
        try:
            fps = int(self.fps_var.get())
            if fps < 1 or fps > 2000:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid", "Enter a valid FPS value (1-2000).")
            return
        try:
            written = apply_fps_cap(fps)
        except Exception as e:
            messagebox.showerror("Error", f"Could not write FastFlags:\n{e}")
            return
        if not written:
            self.fps_status.config(text="No Roblox version found — start Roblox once first.", foreground="#e6b84c")
        else:
            self.fps_status.config(text=f"Applied to {len(written)} version(s). Restart Roblox to take effect.", foreground="#7dd87d")

    def launch_instances(self):
        exe = find_roblox_exe()
        if not exe:
            if messagebox.askyesno("Roblox not found", "RobloxPlayerBeta.exe was not found.\nLocate it manually?"):
                path = filedialog.askopenfilename(title="Select RobloxPlayerBeta.exe",
                                                  filetypes=[("Executable", "*.exe")])
                if path:
                    s = load_settings()
                    s["roblox_exe"] = path
                    save_settings(s)
                    exe = path
                else:
                    return
            else:
                return
        try:
            count = max(1, min(20, int(self.count_var.get())))
        except (ValueError, tk.TclError):
            count = 1
        force = self.force_var.get()
        self.launch_btn.state(["disabled"])
        threading.Thread(target=self._do_launch, args=(exe, count, force), daemon=True).start()

    def _do_launch(self, exe, count, force):
        unlocked = 0
        for i in range(count):
            try:
                proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
                time.sleep(0.8)
                if force and proc.pid:
                    unlocked += close_single_instance_mutex(proc.pid)
            except Exception as e:
                self.log(f"Launch failed: {e}")
                break
            time.sleep(0.4)
        msg = f"Launched {count} instance(s)."
        if force:
            msg += f" Closed {unlocked} single-instance lock(s)."
        self.log(msg)
        self.after(0, lambda: self.launch_btn.state(["!disabled"]))


if __name__ == "__main__":
    app = App()
    app.mainloop()
