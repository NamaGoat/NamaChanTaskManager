import ctypes
import glob
import json
import os
import subprocess
import sys
import threading
import time

import psutil

ROBLOX_PROCESS = "RobloxPlayerBeta.exe"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
LOCALAPPDATA = os.getenv("LOCALAPPDATA", "")
if not LOCALAPPDATA:
    LOCALAPPDATA = os.path.join(os.getenv("USERPROFILE", ""), "AppData", "Local")
VERSIONS_DIR = os.path.join(LOCALAPPDATA, "Roblox", "Versions")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")
user32 = ctypes.WinDLL("user32")

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
user32.EnumWindows.argtypes = [WNDENUMPROC, ctypes.c_void_p]


def pid_has_visible_window(pid):
    found = []

    def cb(hwnd, _):
        wpid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(True)
            return False
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(cb), None)
    except Exception:
        return True
    return bool(found)

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
        ("ProcessId", ctypes.c_size_t),
        ("Handle", ctypes.c_void_p),
        ("GrantedAccess", ctypes.c_ulong),
        ("CreatorBackTraceIndex", ctypes.c_ushort),
        ("ObjectTypeIndex", ctypes.c_ushort),
        ("Object", ctypes.c_void_p),
        ("Reserved", ctypes.c_ulong),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_void_p),
    ]


kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
kernel32.CloseHandle.restype = ctypes.c_int
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.DuplicateHandle.restype = ctypes.c_int
kernel32.DuplicateHandle.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.c_int, ctypes.c_uint,
]
kernel32.GetCurrentProcess.restype = ctypes.c_void_p
ntdll.NtQuerySystemInformation.restype = ctypes.c_ulong
ntdll.NtQuerySystemInformation.argtypes = [
    ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
]
ntdll.NtQueryObject.restype = ctypes.c_ulong
ntdll.NtQueryObject.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong),
]
ntdll.NtSuspendProcess.restype = ctypes.c_ulong
ntdll.NtSuspendProcess.argtypes = [ctypes.c_void_p]
ntdll.NtResumeProcess.restype = ctypes.c_ulong
ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]


def _nt_query_name(handle, info_class):
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
    buf = None
    for _ in range(6):
        buf = ctypes.create_string_buffer(length)
        ret_len = ctypes.c_ulong()
        status = ntdll.NtQuerySystemInformation(
            SystemExtendedHandleInformation, buf, length, ctypes.byref(ret_len)
        )
        if status == STATUS_INFO_LENGTH_MISMATCH:
            length *= 2
            continue
        break
    if status != 0 or buf is None:
        return 0

    entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
    base = ctypes.addressof(buf) + 24
    count = max(0, (ret_len.value or 0) - 24) // entry_size
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
            dup = ctypes.c_void_p()
            if not kernel32.DuplicateHandle(h_proc, handle, kernel32.GetCurrentProcess(), ctypes.byref(dup), 0, False, DUPLICATE_SAME_ACCESS):
                continue
            try:
                type_name = _nt_query_name(dup, ObjectTypeInformation)
                if type_name not in ("Mutant", "Event", "Section"):
                    continue
                obj_name = _nt_query_name(dup, ObjectNameInformation)
                if obj_name:
                    n = obj_name.upper()
                    if ("ROBLOX_SINGLETON" in n or "ROBLOX_SINGLEINSTANCE" in n
                            or "ROBLOXPLAYERBETA.EXE.MTX" in n
                            or "ROBLOXPLAYERBETA.EXE.SHM" in n):
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


_DEFAULTS = {"fps_default": 240, "gfx_quality": "auto", "theme": "Miyabi",
             "force_mutex": True, "anti_afk": False, "aa_interval": 120,
             "auto_rejoin": False, "ar_delay": 6}


def load_settings():
    try:
        with open(SETTINGS_PATH, "r") as f:
            d = json.load(f)
    except Exception:
        d = {}
    for k, v in _DEFAULTS.items():
        d.setdefault(k, v)
    return d


def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def save_custom_exe(path):
    s = load_settings()
    s["roblox_exe"] = path
    save_settings(s)


def find_roblox_exe():
    custom = load_settings().get("roblox_exe")
    if custom and os.path.isfile(custom):
        return custom
    candidates = glob.glob(os.path.join(VERSIONS_DIR, "version-*", ROBLOX_PROCESS))
    if not candidates:
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.getenv(env, "")
            if base:
                candidates += glob.glob(os.path.join(base, "Roblox", "Versions", "version-*", ROBLOX_PROCESS))
    if candidates:
        candidates.sort(key=lambda p: os.path.getmtime(os.path.dirname(p)), reverse=True)
        return candidates[0]
    return None


GFX_FLAG = "DFIntDebugFRMQualityLevelOverride"
GFX_QUALITY_LEVELS = {"perf": "1", "perfplus": "1", "perfrendermax": "21", "equilibre": "8", "pro": "21"}
GFX_LABELS = {"Auto": "auto", "Perf": "perfplus", "Perf++": "perf", "Perf Render Max": "perfrendermax",
              "Équilibré": "equilibre", "Pro": "pro"}

_LOD_KEYS = [
    "DFIntCSGLevelOfDetailSwitchingDistance",
    "DFIntCSGLevelOfDetailSwitchingDistanceL12",
    "DFIntCSGLevelOfDetailSwitchingDistanceL23",
    "DFIntCSGLevelOfDetailSwitchingDistanceL34",
]

def _lod_flags(val):
    d = {k: val for k in _LOD_KEYS}
    return d

GFX_PRESET_FLAGS = {
    "perf": dict({
        "FFlagDebugSkyGray": "True",
        "DFFlagTextureQualityOverrideEnabled": "True",
        "DFIntTextureQualityOverride": "1",
        "DFFlagDisableDPIScale": "True",
        "FIntRenderShadowIntensity": "0",
        "DFFlagDebugPauseVoxelizer": "True",
        "DFIntDebugRestrictGCDistance": "500000",
    }, **_lod_flags("200000")),
    "perfplus": dict({
        "DFFlagTextureQualityOverrideEnabled": "True",
        "DFIntTextureQualityOverride": "1",
        "DFFlagDisableDPIScale": "True",
        "FIntRenderShadowIntensity": "0",
        "DFFlagDebugPauseVoxelizer": "True",
        "DFIntDebugRestrictGCDistance": "500000",
    }, **_lod_flags("200000")),
    "perfrendermax": dict({
        "DFFlagTextureQualityOverrideEnabled": "True",
        "DFIntTextureQualityOverride": "1",
        "DFFlagDisableDPIScale": "True",
        "FIntRenderShadowIntensity": "0",
        "DFFlagDebugPauseVoxelizer": "True",
        "DFIntDebugRestrictGCDistance": "500000",
    }, **_lod_flags("200000")),
    "equilibre": {},
    "pro": {},
}
GFX_MANAGED_KEYS = {GFX_FLAG} | {k for p in GFX_PRESET_FLAGS.values() for k in p}

GLOBAL_SETTINGS_XML = os.path.join(LOCALAPPDATA, "Roblox", "GlobalBasicSettings_13.xml")


def _find_version_dirs():
    dirs = [d for d in glob.glob(os.path.join(VERSIONS_DIR, "version-*")) if os.path.isdir(d)]
    if not dirs:
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.getenv(env, "")
            if base:
                dirs += [d for d in glob.glob(os.path.join(base, "Roblox", "Versions", "version-*")) if os.path.isdir(d)]
    return dirs


def _read_client_settings(path):
    if os.path.isfile(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _global_framerate_cap_path():
    return GLOBAL_SETTINGS_XML


def _ensure_global_settings_xml():
    if os.path.isfile(GLOBAL_SETTINGS_XML):
        return True
    try:
        os.makedirs(os.path.dirname(GLOBAL_SETTINGS_XML), exist_ok=True)
        with open(GLOBAL_SETTINGS_XML, "w", encoding="utf-8") as f:
            f.write('<roblox version="4">\n<int name="FramerateCap">60</int>\n'
                    '<token name="SavedQualityLevel">1</token>\n'
                    '<int name="GraphicsQualityLevel">1</int>\n</roblox>\n')
        return True
    except Exception:
        return False


def write_global_framerate_cap(fps):
    fps = int(fps)
    _ensure_global_settings_xml()
    if not os.path.isfile(GLOBAL_SETTINGS_XML):
        return False
    import re
    try:
        with open(GLOBAL_SETTINGS_XML, "r", encoding="utf-8") as f:
            data = f.read()
        new, n = re.subn(r'(<int name="FramerateCap">)\d+(</int>)', r"\g<1>" + str(fps) + r"\g<2>", data)
        if not n:
            data = data.replace("</roblox>",
                               f'<int name="FramerateCap">{fps}</int>\n</roblox>')
            new, n = data, True
        if n and new != data:
            with open(GLOBAL_SETTINGS_XML, "w", encoding="utf-8") as f:
                f.write(new)
        return bool(n)
    except Exception:
        return False


def read_global_framerate_cap():
    import re
    try:
        with open(GLOBAL_SETTINGS_XML, "r", encoding="utf-8") as f:
            m = re.search(r'<int name="FramerateCap">(\d+)</int>', f.read())
        return int(m.group(1)) if m else None
    except Exception:
        return None


def write_global_quality_level(level):
    """Écrit SavedQualityLevel/GraphicsQualityLevel (1-10) dans
    GlobalBasicSettings_13.xml. Le rayon de STREAMING dépend de CE réglage
    utilisateur réel, pas de DFIntDebugFRMQualityLevelOverride."""
    _ensure_global_settings_xml()
    if not os.path.isfile(GLOBAL_SETTINGS_XML):
        return False
    import re
    ok = False
    try:
        with open(GLOBAL_SETTINGS_XML, "r", encoding="utf-8") as f:
            data = f.read()
        for key in ("SavedQualityLevel", "GraphicsQualityLevel"):
            data, n = re.subn(r'(<(?:int|token) name="' + key + r'">)\d+(</(?:int|token)>)',
                              r"\g<1>" + str(int(level)) + r"\g<2>", data)
            ok = ok or bool(n)
        if ok:
            with open(GLOBAL_SETTINGS_XML, "w", encoding="utf-8") as f:
                f.write(data)
        return ok
    except Exception:
        return False


def read_global_quality_level():
    import re
    try:
        with open(GLOBAL_SETTINGS_XML, "r", encoding="utf-8") as f:
            m = re.search(r'<(?:int|token) name="SavedQualityLevel">(\d+)</(?:int|token)>', f.read())
        return int(m.group(1)) if m else None
    except Exception:
        return None


def apply_fps_cap(fps, gfx_mode="auto"):
    versions = _find_version_dirs()
    written = []
    gfx_val = GFX_QUALITY_LEVELS.get(gfx_mode)
    preset = GFX_PRESET_FLAGS.get(gfx_mode, {})
    for v in versions:
        cs_dir = os.path.join(v, "ClientSettings")
        os.makedirs(cs_dir, exist_ok=True)
        path = os.path.join(cs_dir, "ClientAppSettings.json")
        data = _read_client_settings(path)
        data["DFIntTaskSchedulerTargetFps"] = str(fps)
        data["FFlagTaskSchedulerLimitTargetFpsTo2402"] = "False"
        for k in GFX_MANAGED_KEYS:
            data.pop(k, None)
        if gfx_val is not None:
            data[GFX_FLAG] = gfx_val
            for k, val in preset.items():
                data[k] = val
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            written.append(path)
        except PermissionError:
            pass
    write_global_framerate_cap(fps)
    if gfx_mode != "auto":
        write_global_quality_level(10)
    return written


def ensure_fps_cap():
    s = load_settings()
    fps = s.get("fps_default")
    if not fps:
        return None
    target = str(fps)
    gfx_mode = s.get("gfx_quality", "auto")
    gfx_target = GFX_QUALITY_LEVELS.get(gfx_mode)
    expected = {}
    if gfx_target is not None:
        expected[GFX_FLAG] = gfx_target
        expected.update(GFX_PRESET_FLAGS.get(gfx_mode, {}))
    versions = _find_version_dirs()
    need = False
    for v in versions:
        path = os.path.join(v, "ClientSettings", "ClientAppSettings.json")
        data = _read_client_settings(path)
        if data.get("DFIntTaskSchedulerTargetFps") != target:
            need = True
            break
        bad_gfx = any((data.get(k) or None) != (v2 or None) for k, v2 in expected.items())
        if bad_gfx:
            need = True
            break
    if not need and read_global_framerate_cap() not in (None, int(fps)):
        need = True
    if not need and gfx_mode != "auto" and read_global_quality_level() != 10:
        need = True
    if need and versions:
        return apply_fps_cap(fps, gfx_mode)
    return []


PRIORITY_CLASSES = {
    "Low": psutil.IDLE_PRIORITY_CLASS,
    "Normal": psutil.NORMAL_PRIORITY_CLASS,
    "High": psutil.HIGH_PRIORITY_CLASS,
}


def set_priority(pid, name):
    try:
        psutil.Process(pid).nice(PRIORITY_CLASSES[name])
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
        return False


_proc_cache = {}
_CPU_COUNT = psutil.cpu_count(logical=True) or 1


def get_instances():
    global _proc_cache
    rows = []
    alive = {}
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"] and p.info["name"].lower() == ROBLOX_PROCESS.lower():
                alive[p.pid] = _proc_cache.get(p.pid, p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _proc_cache = alive
    for pid, p in alive.items():
        try:
            with p.oneshot():
                mem_mb = p.memory_info().rss / (1024 * 1024)
                uptime = time.time() - p.create_time()
                cpu = p.cpu_percent(None) / _CPU_COUNT
                suspended = p.status() == psutil.STATUS_STOPPED
            m, s = divmod(int(uptime), 60)
            h, m = divmod(m, 60)
            rows.append({
                "pid": pid,
                "cpu": max(0.0, min(cpu, 100.0)),
                "mem_mb": mem_mb,
                "suspended": suspended,
                "visible": pid_has_visible_window(pid),
                "uptime": f"{h}:{m:02d}:{s:02d}",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return rows


def kill_pid(pid):
    try:
        psutil.Process(pid).terminate()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _roblox_pids():
    pids = []
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"] and p.info["name"].lower() == ROBLOX_PROCESS.lower():
                pids.append(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return pids


def unlock_all(min_age=8.0):
    now = time.time()
    total = 0
    for pid in _roblox_pids():
        try:
            if psutil.Process(pid).create_time() > now - min_age:
                continue
        except Exception:
            continue
        total += close_single_instance_mutex(pid)
    return total


def launch_one(exe):
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    time.sleep(0.8)
    return proc.pid


def launch_instances(exe, count, force, progress=None):
    launched = 0
    unlocked = 0
    for i in range(count):
        try:
            if force:
                unlocked += unlock_all()
                launch_one(exe)
                launched += 1
                time.sleep(2.0)
            else:
                launch_one(exe)
                launched += 1
        except Exception:
            break
        if progress:
            progress(i + 1, count)
    return launched, unlocked


_guardian_stop = threading.Event()
_guardian_thread = None


def _guardian_loop(interval=0.5, min_age=8.0):
    while not _guardian_stop.is_set():
        try:
            now = time.time()
            for pid in _roblox_pids():
                if _guardian_stop.is_set():
                    break
                try:
                    if psutil.Process(pid).create_time() > now - min_age:
                        continue
                    close_single_instance_mutex(pid)
                except Exception:
                    pass
        except Exception:
            pass
        _guardian_stop.wait(interval)


def start_guardian():
    global _guardian_thread
    if _guardian_thread and _guardian_thread.is_alive():
        return
    _guardian_stop.clear()
    _guardian_thread = threading.Thread(target=_guardian_loop, daemon=True)
    _guardian_thread.start()


def stop_guardian():
    _guardian_stop.set()
