import json
import glob
import os
import shutil
import subprocess
import sys
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
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


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
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    tmp = os.path.join(exe_dir, "namachan_update.new")
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
    """Remplace l'exe en cours d'exécution.

    Pourquoi rename + copie et NON une écriture en place : le bootloader
    PyInstaller (onefile) tient l'exe ouvert SANS partage en écriture, donc
    `open(current, "r+b")` lève `PermissionError` (constaté sur Win11 avec
    un vrai NamaChanAccountManager.exe lancé -> l'update ne partait JAMAIS,
    et l'ancien code catchait l'OSError en silence). Le rename, lui, est
    toujours autorisé sur une image en cours d'exécution : ça libère le nom
    et la copie se fait alors sur un fichier tout neuf, sans verrou.
    Le `.old` ne peut pas être supprimé tout de suite (encore chargé en
    mémoire) -> il est nettoyé au prochain démarrage par cleanup_old_files().

    Retourne (ok, message_erreur). Appelle os._exit(0) si tout s'est bien
    passé (le nouveau fichier est en place, à l'utilisateur de relancer).
    """
    current = os.path.abspath(sys.argv[0])
    try:
        with open(exe_path, "rb") as f:
            data = f.read()
    except OSError as e:
        return False, f"lecture du fichier téléchargé impossible : {e}"
    if not data:
        return False, "le fichier téléchargé est vide"

    backup = current + ".old"
    renamed = False
    last_err = None
    for _ in range(20):                      # antivirus qui bloque ~5 s max
        try:
            os.rename(current, backup)
            renamed = True
            break
        except OSError as e:
            last_err = e
            time.sleep(0.25)
    if not renamed:
        return False, f"impossible de libérer le fichier exe ({last_err})"

    try:
        with open(backup, "rb") as f:         # relit l'ancien exe
            data_old = f.read()
        with open(current, "wb") as f:        # écrit le nouveau par-dessus
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        # on restaure l'ancien exe, l'utilisateur n'a rien perdu
        try:
            with open(current, "wb") as f:
                f.write(data_old)
        except OSError:
            pass
        try:
            os.remove(backup)
        except OSError:
            pass
        return False, f"écriture de la mise à jour impossible : {e}"

    try:
        os.remove(exe_path)                   # le .new
    except OSError:
        pass
    try:
        os.remove(backup)                     # .old (souvent encore verrouillé)
    except OSError:
        pass
    os._exit(0)
