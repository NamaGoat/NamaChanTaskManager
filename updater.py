import json
import glob
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

GITHUB_REPO = "NamaGoat/NamaChanTaskManager"
EXE_NAME = "NamaChanAccountManager.exe"


def _parse_version(v):
    v = v.strip().lstrip("v")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def cleanup_old_files():
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    for pattern in ("*.old", "*.vbs", "namachan_update.*"):
        for f in glob.glob(os.path.join(exe_dir, pattern)):
            try:
                os.remove(f)
            except OSError:
                pass


def check_update(current_version):
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={
            "User-Agent": "NamaChanUpdater",
            "Accept": "application/vnd.github.v3+json",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        tag = data.get("tag_name", "")
        remote = _parse_version(tag)
        local = _parse_version(current_version)
        exe_asset = None
        for asset in data.get("assets", []):
            if asset.get("name", "").lower() == EXE_NAME.lower():
                exe_asset = asset
                break
        if remote > local:
            return {
                "version": tag,
                "notes": data.get("body", ""),
                "url": exe_asset["browser_download_url"] if exe_asset else None,
                "size": exe_asset.get("size", 0) if exe_asset else 0,
            }
    except Exception:
        pass
    return None


def download_update(url, progress_fn=None):
    tmp = os.path.join(tempfile.gettempdir(), "namachan_update.exe")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NamaChanUpdater"})
        with urllib.request.urlopen(req, timeout=120) as r:
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 65536
            with open(tmp, "wb") as f:
                while True:
                    chunk = r.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_fn and total:
                        progress_fn(downloaded, total)
        return tmp
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise e


def apply_update(exe_path):
    current = os.path.abspath(sys.argv[0])
    backup = current + ".old"
    ps1 = os.path.join(tempfile.gettempdir(), "namachan_update.ps1")
    cur = current.replace("'", "''")
    bak = backup.replace("'", "''")
    upd = exe_path.replace("'", "''")
    ps1_path = ps1.replace("'", "''")
    exe_dir = os.path.dirname(current)
    cleanup = " ".join(
        f"Remove-Item '{os.path.join(exe_dir, f).replace(chr(39), chr(39)+chr(39))}' -Force -ErrorAction SilentlyContinue;"
        for f in os.listdir(exe_dir)
        if f.endswith(".old") or f.endswith(".vbs")
    )
    script = (
        "Start-Sleep -Seconds 3\n"
        f"Remove-Item '{bak}' -Force -ErrorAction SilentlyContinue\n"
        f"Rename-Item '{cur}' '{bak}' -Force -ErrorAction SilentlyContinue\n"
        f"Copy-Item '{upd}' '{cur}' -Force\n"
        f"Remove-Item '{upd}' -Force -ErrorAction SilentlyContinue\n"
        f"{cleanup}\n"
        f"Start-Process '{cur}'\n"
        f"Remove-Item '{ps1_path}' -Force -ErrorAction SilentlyContinue\n"
    )
    with open(ps1, "w", encoding="utf-8") as f:
        f.write(script)
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", ps1],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)
    time.sleep(0.5)
    os._exit(0)
