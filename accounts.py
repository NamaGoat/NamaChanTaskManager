import base64
import ctypes
import glob
import json
import os
import random
import re
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from ctypes import wintypes

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(APP_DIR, "accounts.json")

TICKET_URL = "https://auth.roblox.com/v2/authentication/ticket"
USER_URL = "https://users.roblox.com/v1/users/authenticated"

_crypt32 = ctypes.WinDLL("crypt32")
_kernel32 = ctypes.WinDLL("kernel32")
_local_free = _kernel32.LocalFree


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_P_DATA_BLOB = ctypes.POINTER(DATA_BLOB)
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptProtectData.argtypes = [_P_DATA_BLOB, wintypes.LPCWSTR, _P_DATA_BLOB,
                                      ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, _P_DATA_BLOB]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [_P_DATA_BLOB, ctypes.POINTER(wintypes.LPWSTR),
                                        _P_DATA_BLOB, ctypes.c_void_p, ctypes.c_void_p,
                                        wintypes.DWORD, _P_DATA_BLOB]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def dpapi_protect(plain: str) -> str:
    b_in, _ = _blob(plain.encode("utf-8"))
    b_out = DATA_BLOB()
    if not _crypt32.CryptProtectData(ctypes.byref(b_in), "RobloxTaskManager", None, None, None, 0, ctypes.byref(b_out)):
        raise OSError("DPAPI protect failed")
    try:
        raw = ctypes.string_at(b_out.pbData, b_out.cbData)
    finally:
        _local_free(b_out.pbData)
    return base64.b64encode(raw).decode()


def dpapi_unprotect(enc_b64: str) -> str:
    raw = base64.b64decode(enc_b64)
    b_in, _ = _blob(raw)
    b_out = DATA_BLOB()
    if not _crypt32.CryptUnprotectData(ctypes.byref(b_in), None, None, None, None, 0, ctypes.byref(b_out)):
        raise OSError("DPAPI unprotect failed")
    try:
        data = ctypes.string_at(b_out.pbData, b_out.cbData)
    finally:
        _local_free(b_out.pbData)
    return data.decode("utf-8", "replace")


def load_data():
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"accounts": [], "recent": []}


def save_data(data):
    try:
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def get_accounts():
    return load_data().get("accounts", [])


def add_account(token, notes="", chrome_profile=None, name=None):
    token = token.strip()
    if not token:
        raise ValueError("Token vide")
    info = fetch_user_info(token)
    if info is None:
        code = _last_verify_status["code"]
        raise ValueError(f"vérification impossible (HTTP {code})" if code else "vérification impossible (réseau)")
    display = name or info["name"]
    data = load_data()
    for a in data["accounts"]:
        try:
            if dpapi_unprotect(a["token_enc"]) == token:
                a["name"] = display
                if notes:
                    a["notes"] = notes
                if chrome_profile:
                    a["chrome_profile"] = chrome_profile
                save_data(data)
                return display, True
        except Exception:
            continue
    entry = {
        "id": str(uuid.uuid4())[:8],
        "name": display,
        "user_id": info.get("id"),
        "token_enc": dpapi_protect(token),
        "notes": notes,
        "chrome_profile": chrome_profile,
        "added": int(time.time()),
    }
    data["accounts"].append(entry)
    save_data(data)
    return display, False


def remove_account(acc_id):
    data = load_data()
    before = len(data["accounts"])
    data["accounts"] = [a for a in data["accounts"] if a.get("id") != acc_id]
    save_data(data)
    return len(data["accounts"]) < before


def update_account(acc_id, name=None, notes=None, chrome_profile="__unset__"):
    data = load_data()
    for a in data["accounts"]:
        if a.get("id") == acc_id:
            if name is not None:
                a["name"] = name
            if notes is not None:
                a["notes"] = notes
            if chrome_profile != "__unset__":
                a["chrome_profile"] = chrome_profile
            save_data(data)
            return True
    return False


def get_token(acc_id):
    for a in get_accounts():
        if a.get("id") == acc_id:
            try:
                return dpapi_unprotect(a["token_enc"])
            except Exception:
                return None
    return None


def find_account(name):
    for a in get_accounts():
        if a.get("name") == name:
            return a
    return None


def add_recent(place_id, place_name, account_name):
    data = load_data()
    recent = [r for r in data.get("recent", []) if not (r.get("place_id") == place_id and r.get("account") == account_name)]
    recent.insert(0, {"place_id": str(place_id), "name": place_name or f"Place {place_id}", "account": account_name, "ts": int(time.time())})
    data["recent"] = recent[:30]
    save_data(data)


def get_recent():
    return load_data().get("recent", [])


def resolve_player(username):
    username = (username or "").strip().lstrip("@")
    if not username:
        return None
    url = "https://users.roblox.com/v1/usernames/users"
    body = json.dumps({"usernames": [username]}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read()).get("data", [])
            return d[0] if d else None
    except Exception:
        return None


def get_player_presence(user_id):
    url = "https://presence.roblox.com/v1/presence/users"
    body = json.dumps({"userIds": [int(user_id)]}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read()).get("userPresences", [])
        if not d:
            return None
        p = d[0]
        ptype = p.get("presenceType", 0)
        if ptype == 2:
            return {"status": "in_game", "place_id": str(p.get("placeId") or ""),
                    "job_id": p.get("gameId") or "", "last": p.get("lastLocation") or ""}
        return {"status": "online" if ptype == 1 else "offline",
                "place_id": "", "job_id": "", "last": p.get("lastLocation") or ""}
    except Exception:
        return None


def _http_json(url, token=None, method="GET"):
    req = urllib.request.Request(url, data=b"" if method == "POST" else None, method=method)
    if method == "POST":
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Cookie", f".ROBLOSECURITY={token}")
        req.add_header("Referer", "https://www.roblox.com")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception:
        return 0, {}, b"{}"


_last_verify_status = {"code": 0}


def fetch_user_info(token):
    status, _, body = _http_json(USER_URL, token)
    _last_verify_status["code"] = status
    if status != 200:
        return None
    try:
        j = json.loads(body.decode("utf-8", "replace"))
        if j.get("id"):
            return {"id": j["id"], "name": j.get("name", "Compte")}
    except Exception:
        pass
    return None


_last_ticket_status = {"code": 0}


def get_auth_ticket(token):
    _last_ticket_status["code"] = 0
    status, headers, body = _http_json(TICKET_URL, token, method="POST")
    if status == 403:
        csrf = headers.get("x-csrf-token") or headers.get("X-CSRF-Token")
        if csrf:
            req = urllib.request.Request(TICKET_URL, data=b"", method="POST")
            req.add_header("Cookie", f".ROBLOSECURITY={token}")
            req.add_header("Referer", "https://www.roblox.com")
            req.add_header("X-CSRF-TOKEN", csrf)
            req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            try:
                with urllib.request.urlopen(req, timeout=12) as r:
                    status, body = r.status, r.read()
            except urllib.error.HTTPError as e2:
                status, body = e2.code, e2.read()
            except Exception:
                status, body = 0, b"{}"
    _last_ticket_status["code"] = status
    if status == 200:
        try:
            return json.loads(body.decode()).get("ticket")
        except Exception:
            return None
    return None


def parse_join_target(text):
    text = (text or "").strip()
    m = re.search(r"/games/(\d+)[^\s]*privateServerLinkCode=([\w-]+)", text)
    if m:
        return {"mode": "private", "place_id": m.group(1), "code": m.group(2)}
    m = re.search(r"/games/(\d+)", text)
    if m:
        return {"mode": "public", "place_id": m.group(1), "code": ""}
    if re.fullmatch(r"\d+", text):
        return {"mode": "public", "place_id": text, "code": ""}
    return None


def build_launch_uri(ticket, target=None):
    ms = int(time.time() * 1000)
    if not target:
        return f"roblox-player://1+launchmode:app+gameinfo:{ticket}+launchtime:{ms}"
    btid = str(random.randint(100000, 175000)) + str(random.randint(100000, 900000))
    base = "https://assetgame.roblox.com/game/PlaceLauncher.ashx?"
    if target:
        pid_ = target.get("place_id")
        mode = target.get("mode", "public")
        code = target.get("code", "")
        if mode == "private":
            url = f"{base}request=RequestPrivateGame&placeId={pid_}&accessCode={urllib.parse.quote(code)}&linkCode={urllib.parse.quote(code)}"
        elif mode == "job":
            url = f"{base}request=RequestGameJob&browserTrackerId={btid}&placeId={pid_}&gameId={urllib.parse.quote(code)}&isPlayTogetherGame=false"
        else:
            url = f"{base}request=RequestGame&browserTrackerId={btid}&placeId={pid_}&isPlayTogetherGame=false"
    else:
        url = f"{base}request=RequestGame&browserTrackerId={btid}&isPlayTogetherGame=false"
    return (f"roblox-player://1+launchmode:play+gameinfo:{ticket}+launchtime:{ms}"
            f"+placelauncherurl:{urllib.parse.quote(url, safe='')}"
            f"+browsertrackerid:{btid}+robloxLocale:en_us+gameLocale:en_us+channel:+LaunchExp:InApp")


def browser_user_dirs():
    local = os.getenv("LOCALAPPDATA", "")
    appdata = os.getenv("APPDATA", "")
    candidates = [
        ("Chrome", os.path.join(local, "Google", "Chrome", "User Data")),
        ("Edge", os.path.join(local, "Microsoft", "Edge", "User Data")),
        ("Brave", os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")),
        ("Opera", os.path.join(appdata, "Opera Software", "Opera Stable")),
    ]
    out = []
    for label, ud in candidates:
        if os.path.isdir(ud):
            out.append((label, ud))
    return out


def list_chrome_profiles():
    profiles = []
    for label, ud in browser_user_dirs():
        for pref in glob.glob(os.path.join(ud, "*", "Preferences")):
            prof_dir = os.path.dirname(pref)
            if not os.path.isdir(os.path.join(prof_dir, "Network")) and not os.path.isfile(os.path.join(prof_dir, "Cookies")):
                continue
            pname = os.path.basename(prof_dir)
            title = pname
            try:
                with open(pref, "r", encoding="utf-8") as f:
                    j = json.load(f)
                title = j.get("profile", {}).get("name", pname)
            except Exception:
                pass
            profiles.append({"browser": label, "dir": prof_dir, "rel": pname, "title": f"{label} — {title}"})
    return profiles


def _aes_key_from_local_state(user_data):
    ls = os.path.join(user_data, "Local State")
    if not os.path.isfile(ls):
        return None
    try:
        with open(ls, "r", encoding="utf-8") as f:
            key_b64 = json.load(f)["os_crypt"]["encrypted_key"]
        enc_key = base64.b64decode(key_b64)
        if not enc_key.startswith(b"DPAPI"):
            return None
        b_in, _ = _blob(enc_key[5:])
        b_out = DATA_BLOB()
        if not _crypt32.CryptUnprotectData(ctypes.byref(b_in), None, None, None, None, 0, ctypes.byref(b_out)):
            return None
        try:
            return ctypes.string_at(b_out.pbData, b_out.cbData)
        finally:
            _local_free(b_out.pbData)
    except Exception:
        return None


def extract_roblox_cookie(profile_dir, user_data_dir, aes_key):
    src_dir = os.path.join(profile_dir, "Network")
    if not os.path.isfile(os.path.join(src_dir, "Cookies")):
        src_dir = profile_dir
    if not os.path.isfile(os.path.join(src_dir, "Cookies")):
        return None
    tmpdir = tempfile.mkdtemp(prefix="cookie_read_")
    try:
        for name in ("Cookies", "Cookies-wal", "Cookies-journal"):
            src = os.path.join(src_dir, name)
            dst = os.path.join(tmpdir, name)
            for attempt in range(4):
                try:
                    if os.path.isfile(src):
                        shutil.copy2(src, dst)
                    break
                except OSError:
                    time.sleep(0.35)
        db = os.path.join(tmpdir, "Cookies")
        con = sqlite3.connect(db)
        cur = con.cursor()
        cur.execute(
            "SELECT encrypted_value FROM cookies WHERE name='.ROBLOSECURITY' "
            "AND (host_key='.roblox.com' OR host_key='www.roblox.com') "
            "ORDER BY (host_key='.roblox.com') DESC, expires_utc DESC LIMIT 1"
        )
        row = cur.fetchone()
        con.close()
        if not row or not row[0]:
            return None
        v = bytes(row[0])
        if v[:3] in (b"v10", b"v11") and aes_key:
            plain = AESGCM(aes_key).decrypt(v[3:15], v[15:], None)
            token = plain.decode("utf-8", "replace").strip()
            return token or None
        if v[:3] == b"v20":
            return "__APPBOUND__"
        return v.decode("utf-8", "replace").strip() or None
    except Exception:
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def find_browser_exe(preferred=None):
    local = os.getenv("LOCALAPPDATA", "")
    pf = os.getenv("PROGRAMFILES", "")
    pf86 = os.getenv("PROGRAMFILES(X86)", "")
    order = preferred or ["Chrome", "Edge", "Brave"]
    paths = {
        "Chrome": [os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
                   os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
                   os.path.join(pf86, r"Google\Chrome\Application\chrome.exe")],
        "Edge": [os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
                 os.path.join(local, r"Microsoft\Edge\Application\msedge.exe")],
        "Brave": [os.path.join(local, r"BraveSoftware\Brave-Browser\Application\brave.exe")],
    }
    for name in order:
        for p in paths.get(name, []):
            if p and os.path.isfile(p):
                return name, p
    return None, None


CDP_PORT = 9225


def start_temp_login(log=lambda m: None, headless=False, url="https://www.roblox.com/login", hidden=False):
    browser, exe = find_browser_exe()
    if not exe:
        raise RuntimeError("Aucun navigateur trouvé (Chrome/Edge/Brave).")
    tmpdir = tempfile.mkdtemp(prefix="namachan_login_")
    args = [exe,
            f"--user-data-dir={tmpdir}",
            f"--remote-debugging-port={CDP_PORT}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1100,800"]
    if headless:
        args.append("--headless=new")
    elif hidden:
        args += ["--window-position=-32000,-32000", "--window-size=900,700"]
    args.append(url)
    proc = subprocess.Popen(args, cwd=os.path.dirname(exe))
    if not headless:
        log(f"[Login navigateur] {browser} ouvert dans un profil temporaire — connecte-toi à Roblox.")
    return {"proc": proc, "tmpdir": tmpdir, "browser": browser, "port": CDP_PORT}


class _CdpSession:
    def __init__(self, wurl, timeout=8):
        self.sock, self.buf = _ws_connect(wurl, timeout)
        self.next_id = 0

    def call(self, method, params=None, timeout=8):
        self.next_id += 1
        mid = self.next_id
        _ws_send(self.sock, json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        buf = self.buf
        while time.time() < deadline:
            resp, buf = _ws_recv(self.sock, buf, timeout=max(1, int(deadline - time.time())))
            self.buf = buf
            d = json.loads(resp)
            if d.get("id") == mid:
                return d
        raise IOError("cdp timeout")


def _first_page_ws(port, wait=8):
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                targets = json.loads(r.read().decode())
            pages = [t["webSocketDebuggerUrl"] for t in targets
                     if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                return pages[0]
        except Exception:
            pass
        time.sleep(0.5)
    raise IOError("aucun onglet CDP")


def get_ticket_via_cdp(token, log=lambda m: None):
    h = None
    try:
        h = start_temp_login(log=log, headless=False, hidden=True,
                             url="https://auth.roblox.com/")
        wurl = None
        deadline = time.time() + 20
        while time.time() < deadline and wurl is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{h['port']}/json/list", timeout=2) as r:
                    targets = json.loads(r.read().decode())
                auth_pages = [t["webSocketDebuggerUrl"] for t in targets
                              if t.get("type") == "page" and "auth.roblox.com" in (t.get("url") or "")
                              and t.get("webSocketDebuggerUrl")]
                any_page = [t["webSocketDebuggerUrl"] for t in targets
                            if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
                if auth_pages:
                    wurl = auth_pages[0]
                elif any_page:
                    wurl = any_page[0]
            except Exception:
                pass
            time.sleep(0.6)
        if not wurl:
            return None, -4
        cdp = _CdpSession(wurl)
        cdp.call("Page.enable")
        r = cdp.call("Network.setCookie", {
            "name": ".ROBLOSECURITY", "value": token,
            "domain": ".roblox.com", "path": "/", "secure": True, "httpOnly": True,
            "url": "https://www.roblox.com/",
        })
        if not r.get("result", {}).get("success"):
            log("[Ticket] Cookie refusé par le navigateur.")
            return None, -2
        for _ in range(15):
            rr = cdp.call("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True}, timeout=8)
            if rr.get("result", {}).get("result", {}).get("value") == "complete":
                break
            time.sleep(1)
        worker_src = (
            "self.onmessage=async e=>{"
            "try{"
            "const o={method:'POST',credentials:'include',"
            "referrer:'https://auth.roblox.com/',referrerPolicy:'unsafe-url',"
            "headers:{'Content-Type':'application/json','rbxauthenticationnegotiation':'1'},body:'{}'};"
            "let r=await fetch('https://auth.roblox.com/v1/authentication-ticket',o);"
            "const csrf=r.headers.get('x-csrf-token');"
            "if(!csrf){self.postMessage('ERR|'+r.status+'|nocsvr');return;}"
            "o.headers['x-csrf-token']=csrf;"
            "r=await fetch('https://auth.roblox.com/v1/authentication-ticket',o);"
            "const tk=r.headers.get('rbx-authentication-ticket');"
            "if(tk){self.postMessage('OK|'+tk);}"
            "else{self.postMessage('ERR|'+r.status+'|'+(await r.text()).slice(0,200));}"
            "}catch(err){self.postMessage('EXC|0|'+((err&&err.message)||err));}"
            "};"
        )
        expr = ("(async()=>{try{"
                "const u=URL.createObjectURL(new Blob([" + json.dumps(worker_src) + "],{type:'text/javascript'}));"
                "const w=new Worker(u);"
                "return await new Promise((res,rej)=>{const t=setTimeout(()=>rej(new Error('timeout')),30000);"
                "w.onmessage=ev=>{clearTimeout(t);res(ev.data);};"
                "w.onerror=ev=>{clearTimeout(t);rej(new Error(ev.message||'worker'));};w.postMessage('go');});"
                "}catch(e){return 'EXC|0|'+((e&&e.message)||e);}})()")
        r2 = cdp.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True}, timeout=40)
        res = r2.get("result", {})
        if res.get("exceptionDetails"):
            log(f"[Ticket] Exception CDP: {str(res['exceptionDetails'].get('exception', {}).get('description', ''))[:150]}")
        val = res.get("result", {}).get("value", "") or ""
        parts = val.split("|", 2)
        if parts[0] == "OK" and len(parts) > 1:
            ticket = parts[1]
            log(f"[Ticket] Ticket obtenu (longueur {len(ticket)}).")
            return ticket, 200
        if parts[0] == "EXC":
            log(f"[Ticket] Fetch bloqué: {parts[-1][:150]}")
            return None, -3
        try:
            code = int(parts[1])
        except (ValueError, IndexError):
            log(f"[Ticket] Réponse inattendue: {val[:120]}")
            return None, -3
        body = parts[2] if len(parts) > 2 else ""
        log(f"[Ticket] Refus HTTP {code}: {body[:120]}")
        return None, code
    except Exception as e:
        log(f"[Ticket] Erreur voie navigateur: {e}")
        return None, -1
    finally:
        if h:
            finish_temp_login(h, True)


def _ws_connect(url, timeout=4):
    m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
    if not m:
        raise IOError("bad ws url")
    host, port, path = m.group(1), int(m.group(2)), m.group(3)
    sock = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\nOrigin: http://127.0.0.1:{port}\r\n\r\n"
    )
    sock.sendall(handshake.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise IOError("handshake failed")
        buf += chunk
    status_line = buf.split(b"\r\n", 1)[0]
    if b"101" not in status_line:
        raise IOError("ws refused")
    leftover = buf.split(b"\r\n\r\n", 1)[1]
    sock.settimeout(timeout)
    return sock, leftover


def _recv_exact(sock, n, buf):
    while len(buf) < n:
        chunk = sock.recv(65536)
        if not chunk:
            raise IOError("closed")
        buf += chunk
    return buf[:n], buf[n:]


def _ws_send(sock, payload):
    data = payload.encode()
    mask = os.urandom(4)
    n = len(data)
    if n < 126:
        header = struct.pack("!BB", 0x81, 0x80 | n)
    elif n < 65536:
        header = struct.pack("!BBH", 0x81, 0x80 | 126, n)
    else:
        header = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(header + mask + masked)


def _ws_recv(sock, leftover=b"", timeout=4):
    buf = leftover
    while True:
        header, buf = _recv_exact(sock, 2, buf)
        op = header[0] & 0x0F
        ln = header[1] & 0x7F
        if ln == 126:
            ext, buf = _recv_exact(sock, 2, buf)
            ln = struct.unpack("!H", ext)[0]
        elif ln == 127:
            ext, buf = _recv_exact(sock, 8, buf)
            ln = struct.unpack("!Q", ext)[0]
        payload, buf = _recv_exact(sock, ln, buf) if ln else (b"", buf)
        if op == 0x9:
            sock.sendall(struct.pack("!BB", 0x8A, len(payload)) + payload)
            continue
        if op == 0x8:
            raise IOError("ws closed")
        if op in (0x1, 0x2):
            return payload.decode("utf-8", "replace"), buf


def poll_cdp_cookie(handle):
    port = handle.get("port")
    if not port:
        return None
    bwurl = None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            version = json.loads(r.read().decode())
        bwurl = version.get("webSocketDebuggerUrl")
    except Exception:
        bwurl = None
    if bwurl:
        try:
            sock, leftover = _ws_connect(bwurl)
            _ws_send(sock, json.dumps({"id": 1, "method": "Storage.getCookies"}))
            resp, _ = _ws_recv(sock, leftover)
            try:
                sock.close()
            except Exception:
                pass
            j = json.loads(resp)
            vals = {c.get("value", "") for c in j.get("result", {}).get("cookies", [])
                    if c.get("name") == ".ROBLOSECURITY"}
            tokens = sorted(v for v in vals if v.startswith("_|WARNING"))
            if tokens:
                return tokens
        except Exception:
            pass
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
            targets = json.loads(r.read().decode())
    except Exception:
        return None
    pages = [t["webSocketDebuggerUrl"] for t in targets
             if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    vals = set()
    for wurl in pages[:6]:
        try:
            sock, leftover = _ws_connect(wurl)
            _ws_send(sock, json.dumps({
                "id": 1,
                "method": "Network.getCookies",
                "params": {"urls": ["https://www.roblox.com/", "https://roblox.com/"]},
            }))
            resp, _ = _ws_recv(sock, leftover)
            try:
                sock.close()
            except Exception:
                pass
            j = json.loads(resp)
            for c in j.get("result", {}).get("cookies", []):
                if c.get("name") == ".ROBLOSECURITY":
                    val = c.get("value", "")
                    if val.startswith("_|WARNING"):
                        vals.add(val)
        except Exception:
            continue
    tokens = sorted(vals)
    return tokens or None


def poll_temp_login(handle):
    token = poll_temp_login_candidates(handle)
    if token:
        return token[0]
    tmpdir = handle.get("tmpdir")
    if not tmpdir:
        return None
    key = _aes_key_from_local_state(tmpdir)
    if not key:
        return None
    token = extract_roblox_cookie(os.path.join(tmpdir, "Default"), tmpdir, key)
    if token == "__APPBOUND__" or not token:
        return None
    return token


def cdp_all_cookies(handle):
    port = handle.get("port")
    if not port:
        return []
    bwurl = None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            version = json.loads(r.read().decode())
        bwurl = version.get("webSocketDebuggerUrl")
    except Exception:
        return []
    if not bwurl:
        return []
    try:
        sock, leftover = _ws_connect(bwurl)
        _ws_send(sock, json.dumps({"id": 1, "method": "Storage.getCookies"}))
        resp, _ = _ws_recv(sock, leftover)
        try:
            sock.close()
        except Exception:
            pass
        j = json.loads(resp)
        out = []
        for c in j.get("result", {}).get("cookies", []):
            v = c.get("value", "")
            out.append((c.get("name", ""), c.get("domain", ""), len(v)))
        return out
    except Exception:
        return []


def poll_temp_login_candidates(handle):
    toks = poll_cdp_cookie(handle)
    if toks:
        return toks
    tmpdir = handle.get("tmpdir")
    if not tmpdir:
        return None
    key = _aes_key_from_local_state(tmpdir)
    if not key:
        return None
    token = extract_roblox_cookie(os.path.join(tmpdir, "Default"), tmpdir, key)
    if token in (None, "__APPBOUND__"):
        return None
    return [token]


def finish_temp_login(handle, success):
    try:
        subprocess.run(["taskkill", "/PID", str(handle["proc"].pid), "/T", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        pass
    time.sleep(1.5)
    try:
        shutil.rmtree(handle["tmpdir"], ignore_errors=True)
    except Exception:
        pass
    if success:
        pass


def open_profile_in_browser(browser, rel, url="https://www.roblox.com/home"):
    _, exe = find_browser_exe([browser])
    if not exe:
        return False
    user_data = None
    for label, ud in browser_user_dirs():
        if label == browser:
            user_data = ud
            break
    args = [exe]
    if user_data:
        args.append(f"--profile-directory={rel}")
    args += ["--no-first-run", url]
    subprocess.Popen(args, cwd=os.path.dirname(exe))
    return True


def import_from_browser(log=lambda m: None):
    found = []
    for label, ud in browser_user_dirs():
        key = _aes_key_from_local_state(ud)
        if not key:
            log(f"[{label}] Clé de chiffrement inaccessible (Chrome ouvert ? réessaie navigateur fermé).")
            continue
        for prof in glob.glob(os.path.join(ud, "*")):
            if not os.path.isdir(prof):
                continue
            base = os.path.basename(prof)
            if base in ("System Profile", "Guest Profile", "CrashpadMetrics-active", "CrashpadReports"):
                continue
            if not (base == "Default" or base.startswith("Profile")):
                continue
            token = extract_roblox_cookie(prof, ud, key)
            if token == "__APPBOUND__":
                log(f"[{label}/{base}] Cookie protégé App-Bound (Chrome récent), import impossible.")
                continue
            if token and token.startswith("_|WARNING:-DO-NOT-SHARE-THIS"):
                try:
                    name, existed = add_account(token, chrome_profile=f"{label}:{base}")
                    found.append(name)
                    log(f"[{label}/{base}] Compte importé : {name}" + (" (mis à jour)" if existed else ""))
                except ValueError as e:
                    log(f"[{label}/{base}] {e}")
            else:
                log(f"[{label}/{base}] Pas de session Roblox trouvée.")
    return found
