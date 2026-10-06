"""Live IP desktop widget (PySide6).
Install: pip install PySide6      Run: pythonw ip_overlay_v3.py
Drag = move | Drag bottom-right corner = resize (remembered) | Double-click = copy IP | Right-click = menu
"""
import sys, os, re, json, math, time, socket, struct, random, threading, ipaddress, ctypes, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FTimeout
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPointF, QRectF, QLockFile
from PySide6.QtGui import (QPainter, QColor, QFont, QLinearGradient, QPainterPath, QPen, QBrush,
                           QGuiApplication, QPixmap, QActionGroup, QFontMetrics, QPolygonF)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

INTERVAL = 5
DEF_W, DEF_H = 300, 150
CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "IPOverlay")
CFG = os.path.join(CFG_DIR, "config.json")

THEMES = {
    "Midnight": dict(bg1="#141d26", bg2="#090e13", acc="#5ad1ff", ok="#3ddc97", bad="#ff5d6c", mut="#7d8d9b", txt="#c3d0db", line=(255, 255, 255, 30), a=238),
    "Graphite": dict(bg1="#202124", bg2="#111113", acc="#f1f1f3", ok="#7ee787", bad="#ff6b6b", mut="#8b8d93", txt="#c9cacf", line=(255, 255, 255, 28), a=240),
    "Aurora":   dict(bg1="#1d1636", bg2="#0b0818", acc="#b98cff", ok="#5df0c0", bad="#ff6b9a", mut="#8a80ad", txt="#cfc8e8", line=(255, 255, 255, 30), a=238),
    "Emerald":  dict(bg1="#102219", bg2="#07110c", acc="#3ddc97", ok="#7bf1b8", bad="#ff6b5d", mut="#76978a", txt="#bfe0d0", line=(255, 255, 255, 28), a=238),
    "Sunset":   dict(bg1="#2b1620", bg2="#130a10", acc="#ffa45c", ok="#7be0a0", bad="#ff5d78", mut="#a58591", txt="#e5cdd3", line=(255, 255, 255, 28), a=238),
    "Paper":    dict(bg1="#fbfbf8", bg2="#ebe9e1", acc="#0a6cff", ok="#10a463", bad="#d93a4a", mut="#6b7783", txt="#2a343c", line=(0, 0, 0, 40), a=246),
    "Capsule":  dict(bg1="#13253c", bg2="#0a1423", acc="#3d8bff", ok="#38d996", bad="#ff5d6c", mut="#8fa6c4", txt="#e3edf9", line=(90, 140, 210, 80), a=242, pill=True),
}
PILL = (262, 54)
for _t in THEMES.values(): _t["pill"] = True  # single layout: capsule; themes are color variants

# ---------------- networking ----------------
DEFAULT = urllib.request.build_opener()
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
URLS = ["https://api.ipify.org", "https://ipv4.icanhazip.com", "https://checkip.amazonaws.com",
        "https://ipinfo.io/ip", "https://v4.ident.me", "https://ipv4.wtfismyip.com/text",
        "https://ifconfig.me/ip", "https://ipecho.net/plain", "https://ifconfig.co/ip",
        "http://api.ipify.org", "http://ipv4.icanhazip.com", "http://ip-api.com/line/?fields=query"]
M_DEFAULT = [("http", u, "default") for u in URLS]
M_DIRECT = [("http", u, "direct") for u in URLS[:6]]
M_DNS = [("dns", "208.67.222.222", "myip.opendns.com", 1), ("dns", "208.67.220.220", "myip.opendns.com", 1),
         ("dns", "216.239.32.10", "o-o.myaddr.l.google.com", 16)]

def race(jobs, timeout=3.5):
    ex = ThreadPoolExecutor(max_workers=len(jobs))
    futs = [ex.submit(j) for j in jobs]
    try:
        for f in as_completed(futs, timeout=timeout):
            try:
                return f.result()
            except Exception:
                pass
    except FTimeout:
        pass
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return None

def http_get(url, opener, timeout=3.0, limit=65536):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.4"})
    with opener.open(req, timeout=timeout) as r:
        return r.read(limit)

def http_ip(url, opener):
    t0 = time.time()
    txt = http_get(url, opener, limit=512).decode(errors="ignore").strip()
    if len(txt) > 64:
        raise ValueError
    ip = ipaddress.ip_address(re.search(r"(?:\d{1,3}\.){3}\d{1,3}", txt).group(0))
    return str(ip)

def dns_ip(server, name, qtype, timeout=2.5):
    t, rd = dns_raw(server, name, qtype, timeout)
    return ".".join(map(str, rd)) if t == 1 else str(ipaddress.ip_address(rd[1:1 + rd[0]].decode()))

def dns_raw(server, name, qtype, timeout=2.5):
    q = struct.pack(">HHHHHH", random.randint(0, 65535), 0x0100, 1, 0, 0, 0)
    for part in name.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack(">HH", qtype, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(timeout)
    try:
        s.sendto(q, (server, 53)); data, _ = s.recvfrom(1024)
    finally:
        s.close()
    i = 12
    while data[i] != 0: i += data[i] + 1
    i += 5
    for _ in range(struct.unpack(">H", data[6:8])[0]):
        if data[i] & 0xC0 == 0xC0: i += 2
        else:
            while data[i] != 0: i += data[i] + 1
            i += 1
        t, _c, _ttl, rl = struct.unpack(">HHIH", data[i:i + 10]); i += 10
        rd = data[i:i + rl]; i += rl
        if (t == 1 and rl == 4) or t == 16: return t, rd
    raise ValueError

def run(m):
    t0 = time.time()
    ip = http_ip(m[1], DEFAULT if m[2] == "default" else DIRECT) if m[0] == "http" else dns_ip(*m[1:])
    if ipaddress.ip_address(ip).version != 4: raise ValueError
    return ip, int((time.time() - t0) * 1000), m

def detect(good, n):
    if good and ((good[0] == "http" and good[2] == "default") or n % 5):
        try: return run(good)
        except Exception: pass
    for group in (M_DEFAULT, M_DIRECT, M_DNS):
        r = race([lambda m=m: run(m) for m in group], 3.5 if group is not M_DNS else 3.0)
        if r: return r
    return None

def _asn(o):
    p = (o or "").split(" ", 1)
    return (p[0], p[1] if len(p) > 1 else "")

GEO = [
    ("https://ipwho.is/{ip}", lambda j: (j["country_code"], j.get("city", ""), "AS%s" % j["connection"].get("asn", ""), j["connection"].get("isp", "")) if j.get("success") else None),
    ("https://get.geojs.io/v1/ip/geo/{ip}.json", lambda j: (j["country_code"], j.get("city", ""), "AS%s" % j.get("asn", ""), j.get("organization_name", ""))),
    ("https://ipapi.co/{ip}/json/", lambda j: (j["country_code"], j.get("city", ""), j.get("asn", ""), j.get("org", ""))),
    ("https://ipinfo.io/{ip}/json", lambda j: (j["country"], j.get("city", ""), *_asn(j.get("org")))),
    ("http://ip-api.com/json/{ip}?fields=status,countryCode,city,as,isp", lambda j: (j["countryCode"], j.get("city", ""), j.get("as", "").split(" ")[0], j.get("isp", "")) if j.get("status") == "success" else None),
    ("https://freeipapi.com/api/json/{ip}", lambda j: (j["countryCode"], j.get("cityName", ""), "", "")),
]

def geo_lookup(ip):
    def job(url, parse, op):
        g = parse(json.loads(http_get(url.format(ip=ip), op, 4.0)))
        if not g or not g[0]: raise ValueError
        return tuple(str(x or "") for x in g)
    jobs = [lambda u=u, p=p: job(u, p, DEFAULT) for u, p in GEO]
    jobs += [lambda u=u, p=p: job(u, p, DIRECT) for u, p in GEO[:2]]
    g = race(jobs, 5.0)
    if g: return g
    try:  # DNS-based fallback (works when HTTP geo services are blocked)
        rev = ".".join(reversed(ip.split(".")))
        def q(name):
            def one(srv):
                _t, rd = dns_raw(srv, name, 16); return rd[1:1 + rd[0]].decode()
            return race([lambda s=s: one(s) for s in ("8.8.8.8", "1.1.1.1", "9.9.9.9")], 3.0)
        a = q(rev + ".origin.asn.cymru.com")
        if a:
            f = [x.strip() for x in a.split("|")]
            asn, isp = "AS" + f[0].split()[0], ""
            b = q(f"{asn}.asn.cymru.com")
            if b: isp = b.split("|")[-1].strip().rsplit(",", 1)[0]
            return (f[2], "", asn, isp)
    except Exception:
        pass
    try:  # country only
        j = json.loads(http_get(f"https://api.country.is/{ip}", DEFAULT, 3.0))
        return (j["country"], "", "", "")
    except Exception:
        return None

def flag_bytes(cc):
    cc = cc.lower()
    path = os.path.join(CFG_DIR, "flags", cc + ".png")
    if os.path.exists(path):
        return open(path, "rb").read()
    urls = [f"https://flagcdn.com/w80/{cc}.png",
            f"https://raw.githubusercontent.com/hampusborgos/country-flags/main/png250px/{cc}.png",
            f"https://flagpedia.net/data/flags/w160/{cc}.png", f"https://flagsapi.com/{cc.upper()}/flat/64.png"]
    def job(u, op):
        b = http_get(u, op, 4.0, 300000)
        if not b.startswith(b"\x89PNG"): raise ValueError
        return b
    jobs = [lambda u=u: job(u, DEFAULT) for u in urls] + [lambda u=u: job(u, DIRECT) for u in urls[:2]]
    b = race(jobs, 5.0)
    if b:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").write(b)
    return b

STRIPES = {
    "DE": ("h", "000000 DD0000 FFCE00"), "NL": ("h", "AE1C28 FFFFFF 21468B"), "FR": ("v", "0055A4 FFFFFF EF4135"),
    "IT": ("v", "009246 FFFFFF CE2B37"), "RU": ("h", "FFFFFF 0039A6 D52B1E"), "UA": ("h", "0057B7 FFD700"),
    "AM": ("h", "D90012 0033A0 F2A800"), "AT": ("h", "ED2939 FFFFFF ED2939"), "BE": ("v", "000000 FAE042 ED2939"),
    "IE": ("v", "169B62 FFFFFF FF883E"), "ES": ("h", "AA151B F1BF00 F1BF00 AA151B"), "HU": ("h", "CE2939 FFFFFF 477050"),
    "BG": ("h", "FFFFFF 00966E D62612"), "LT": ("h", "FDB913 006A44 C1272D"), "EE": ("h", "0072CE 000000 FFFFFF"),
    "RO": ("v", "002B7F FCD116 CE1126"), "PL": ("h", "FFFFFF DC143C"), "ID": ("h", "FF0000 FFFFFF"),
    "LU": ("h", "EF3340 FFFFFF 00A3E0"), "IQ": ("h", "CE1126 FFFFFF 000000"), "EG": ("h", "CE1126 FFFFFF 000000"),
    "LV": ("h", "9E3039 9E3039 FFFFFF 9E3039 9E3039"), "LI": ("h", "002B7F CE1126"),
}
SPECIAL = {"IR", "AZ", "TR", "US", "JP", "GB", "SE", "FI", "CH", "CA"}

def has_builtin(cc): return cc in STRIPES or cc in SPECIAL

def _c(h): return QColor("#" + h)

def _star(p, cx, cy, R, col):
    import math
    pts = [QPointF(cx + (R if i % 2 == 0 else R * 0.382) * math.cos(-math.pi / 2 + i * math.pi / 5),
                   cy + (R if i % 2 == 0 else R * 0.382) * math.sin(-math.pi / 2 + i * math.pi / 5)) for i in range(10)]
    p.setBrush(col); p.setPen(Qt.NoPen); p.drawPolygon(QPolygonF(pts))

def draw_flag(p, cc, r):
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    cy = y + h / 2
    p.setPen(Qt.NoPen)
    def bands(d, cols):
        n = len(cols)
        for i, c in enumerate(cols):
            p.setBrush(_c(c))
            p.drawRect(QRectF(x, y + h * i / n, w, h / n + 0.6) if d == "h" else QRectF(x + w * i / n, y, w / n + 0.6, h))
    def fill(c): p.setBrush(_c(c)); p.drawRect(r)
    def circ(cx, rad, c): p.setBrush(_c(c)); p.setPen(Qt.NoPen); p.drawEllipse(QPointF(cx, cy), rad, rad)
    if cc in STRIPES:
        d, cols = STRIPES[cc]; bands(d, cols.split()); return True
    if cc == "IR":
        bands("h", ["239F40", "FFFFFF", "DA0000"])
        p.setBrush(Qt.NoBrush); p.setPen(QPen(_c("DA0000"), h * 0.1)); p.drawEllipse(QPointF(x + w / 2, cy), h * 0.11, h * 0.11); return True
    if cc == "AZ":
        bands("h", ["00B5E2", "EF3340", "509E2F"])
        circ(x + w * .45, h * .17, "FFFFFF"); circ(x + w * .49, h * .14, "EF3340")
        _star(p, x + w * .6, cy, h * .09, QColor("white")); return True
    if cc == "TR":
        fill("E30A17"); circ(x + w * .38, h * .25, "FFFFFF"); circ(x + w * .44, h * .2, "E30A17")
        _star(p, x + w * .58, cy, h * .12, QColor("white")); return True
    if cc == "US":
        for i in range(13):
            p.setBrush(_c("B22234" if i % 2 == 0 else "FFFFFF")); p.drawRect(QRectF(x, y + h * i / 13, w, h / 13 + 0.6))
        p.setBrush(_c("3C3B6E")); p.drawRect(QRectF(x, y, w * .4, h * 7 / 13)); return True
    if cc == "JP":
        fill("FFFFFF"); circ(x + w / 2, h * .3, "BC002D"); return True
    if cc == "GB":
        fill("012169")
        for col, wd in (("FFFFFF", .2), ("C8102E", .08)):
            p.setPen(QPen(_c(col), h * wd, Qt.SolidLine, Qt.FlatCap))
            p.drawLine(QPointF(x, y), QPointF(x + w, y + h)); p.drawLine(QPointF(x + w, y), QPointF(x, y + h))
        p.setPen(Qt.NoPen)
        p.setBrush(_c("FFFFFF")); p.drawRect(QRectF(x + w * .4, y, w * .2, h)); p.drawRect(QRectF(x, y + h * .34, w, h * .32))
        p.setBrush(_c("C8102E")); p.drawRect(QRectF(x + w * .44, y, w * .12, h)); p.drawRect(QRectF(x, y + h * .4, w, h * .2)); return True
    if cc == "SE":
        fill("006AA7"); p.setBrush(_c("FECC00")); p.drawRect(QRectF(x + w * .3, y, w * .13, h)); p.drawRect(QRectF(x, y + h * .4, w, h * .2)); return True
    if cc == "FI":
        fill("FFFFFF"); p.setBrush(_c("003580")); p.drawRect(QRectF(x + w * .28, y, w * .17, h)); p.drawRect(QRectF(x, y + h * .38, w, h * .24)); return True
    if cc == "CH":
        fill("DC143C"); p.setBrush(QColor("white")); p.drawRect(QRectF(x + w * .43, y + h * .2, w * .14, h * .6)); p.drawRect(QRectF(x + w * .3, y + h * .43, w * .4, h * .14)); return True
    if cc == "CA":
        bands("v", ["D52B1E", "FFFFFF", "FFFFFF", "D52B1E"]); circ(x + w / 2, h * .15, "D52B1E"); return True
    return False

class Bus(QObject):
    data = Signal(dict)
    clean = Signal(int)

def worker(bus):
    good, n, geo_cache, flags, last_geo, last_flag = None, 0, {}, {}, {}, {}
    while True:
        r = detect(good, n); n += 1
        if r is None:
            bus.data.emit({"ok": False})
        else:
            ip, ms, good = r
            if ip not in geo_cache and time.time() - last_geo.get(ip, 0) > 8:
                last_geo[ip] = time.time()
                g = geo_lookup(ip)
                if g: geo_cache[ip] = g
            g = geo_cache.get(ip)
            fb = None
            if g and not has_builtin(g[0]):
                if flags.get(g[0]) is None and time.time() - last_flag.get(g[0], 0) > 30:
                    last_flag[g[0]] = time.time(); flags[g[0]] = flag_bytes(g[0])
                fb = flags.get(g[0])
            bus.data.emit({"ok": True, "ip": ip, "ms": ms, "geo": g, "flag": fb})
        time.sleep(INTERVAL)

# ---------------- UI ----------------
def load_cfg():
    try: return json.load(open(CFG))
    except Exception: return {}

def save_cfg(c):
    try:
        os.makedirs(CFG_DIR, exist_ok=True); json.dump(c, open(CFG, "w"))
    except Exception: pass

def mix(a, b, t):
    return QColor(*[int(x + (y - x) * t) for x, y in ((a.red(), b.red()), (a.green(), b.green()), (a.blue(), b.blue()))])

def clean_ram():
    """Trim working sets of all accessible processes. Returns freed MB (approx)."""
    if sys.platform != "win32": return 0
    try:
        from ctypes import wintypes
        k32, ps = ctypes.windll.kernel32, ctypes.windll.psapi
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + \
                       [(f"x{i}", ctypes.c_ulonglong) for i in range(7)]
        def avail():
            m = MS(); m.dwLength = ctypes.sizeof(MS); k32.GlobalMemoryStatusEx(ctypes.byref(m)); return m.x1
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        ps.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        before = avail()
        pids = (wintypes.DWORD * 4096)(); need = wintypes.DWORD()
        ps.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(need))
        for pid in pids[: need.value // 4]:
            if pid <= 4: continue
            h = k32.OpenProcess(0x0500, False, pid)  # QUERY_INFORMATION | SET_QUOTA
            if h:
                ps.EmptyWorkingSet(h); k32.CloseHandle(h)
        time.sleep(0.5)
        return max(0, int((avail() - before) / 1048576))
    except Exception:
        return 0

_cpu_prev = [None]

def sys_stats():
    """(RAM %, CPU %) on Windows without extra packages."""
    if sys.platform != "win32": return None, None
    try:
        from ctypes import wintypes
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + \
                       [(f"x{i}", ctypes.c_ulonglong) for i in range(7)]
        ms = MS(); ms.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        idle, ker, usr = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(ker), ctypes.byref(usr))
        v = [(f.dwHighDateTime << 32) | f.dwLowDateTime for f in (idle, ker, usr)]
        prev, _cpu_prev[0], cpu = _cpu_prev[0], v, None
        if prev:
            di, dk, du = (v[i] - prev[i] for i in range(3))
            cpu = int(round(100 * (dk + du - di) / (dk + du))) if dk + du > 0 else 0
        return int(ms.dwMemoryLoad), cpu
    except Exception:
        return None, None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def autostart_get():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "IPOverlay"); return True
    except Exception:
        return False

def autostart_set(on):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                exe = sys.executable; pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
                if os.path.exists(pw): exe = pw
                winreg.SetValueEx(k, "IPOverlay", 0, winreg.REG_SZ, f'"{exe}" "{os.path.abspath(__file__)}"')
            else:
                winreg.DeleteValue(k, "IPOverlay")
    except Exception:
        pass

class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        self.theme = self.cfg.get("theme", "Midnight") if self.cfg.get("theme") in THEMES else "Midnight"
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.apply_mode(); self.place()
        self.ip = self.geo = self.flag = self.flag_cc = self.last_ok = None
        self.ok, self.lats, self.changes, self.flash_t, self.s = None, [], 0, 0.0, 1.0
        self._d = self._rs = None; self._abtn = False; self._arrow = None; self.stats = (None, None)
        self.anim_t, self.clean_msg, self.clean_t, self.cleaning, self._st_t = 0.0, "", 0.0, False, 0.0
        self._fails = 0
        self.bus = Bus(); self.bus.data.connect(self.on_data); self.bus.clean.connect(self.on_clean)
        threading.Thread(target=worker, args=(self.bus,), daemon=True).start()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(1000)
        self.sinker = QTimer(self); self.sinker.timeout.connect(self.sink); self.sinker.start(1500)

    def sink(self):
        if sys.platform != "win32": return
        try:
            from ctypes import wintypes
            u = ctypes.windll.user32
            u.FindWindowW.restype = wintypes.HWND
            u.GetWindow.restype = wintypes.HWND
            u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
            u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.UINT]
            me = int(self.winId())
            prog = u.FindWindowW("Progman", None)
            prev = u.GetWindow(prog, 3)  # GW_HWNDPREV: lowest normal window above the desktop
            if prog and prev and prev != me:
                u.SetWindowPos(me, prev, 0, 0, 0, 0, 0x13)  # sit right above desktop, below all apps
        except Exception: pass

    def showEvent(self, e):
        super().showEvent(e); self.sink()
        QTimer.singleShot(0, self.place); QTimer.singleShot(600, self.place)

    def tick(self):
        busy = (self.flash_t and time.time() - self.flash_t < 3.2) or (self.anim_t and time.time() - self.anim_t < 2.0)
        self.timer.setInterval(40 if busy else 1000)
        if time.time() - self._st_t >= 0.9:
            self.stats = sys_stats(); self._st_t = time.time()
        self.fit(); self.update()

    def on_data(self, d):
        self.ok = d["ok"]
        if not self.ok:
            self._fails += 1
            if self._fails == 2 and self.ip: self.play("down")  # 2 failed checks in a row = really offline
        if self.ok:
            was_down = self._fails >= 2
            self._fails = 0
            changed = bool(self.ip and d["ip"] != self.ip)
            if changed: self.changes += 1; self.flash_t = time.time()
            snd = (["up"] if was_down else []) + (["ip"] if changed else [])
            if snd: self.play(*snd)
            self.ip, self.geo = d["ip"], d["geo"]
            self.last_ok = time.strftime("%H:%M:%S")
            self.lats = (self.lats + [d["ms"]])[-60:]
            if self.geo and d.get("flag") and self.geo[0] != self.flag_cc:
                pm = QPixmap()
                if pm.loadFromData(d["flag"]): self.flag, self.flag_cc = pm, self.geo[0]
        self.update()

    # interaction
    def _grip(self, pos): return not self.is_pill() and pos.x() > self.width() - 24 and pos.y() > self.height() - 24
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton: return
        if self.is_pill() and self._arrow and self._arrow.contains(e.position()):
            self._abtn = True; return
        if self._grip(e.position()): self._rs = (e.globalPosition().toPoint(), self.size())
        else: self._d = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            if self._rs:
                dp = e.globalPosition().toPoint() - self._rs[0]
                self.resize(max(210, self._rs[1].width() + dp.x()), max(110, self._rs[1].height() + dp.y()))
            elif self._d: self.move(e.globalPosition().toPoint() - self._d)
        else:
            over = self._arrow is not None and self._arrow.contains(e.position())
            self.setCursor(Qt.PointingHandCursor if over else (Qt.SizeFDiagCursor if self._grip(e.position()) else Qt.ArrowCursor))
    def mouseReleaseEvent(self, e):
        if self._abtn:
            self._abtn = False
            if self._arrow and self._arrow.contains(e.position()):
                self.start_clean()
            return
        if self._rs:
            self.cfg.update(w=self.width(), h=self.height()); save_cfg(self.cfg)
        self._rs = self._d = None; self.sink()
    def mouseDoubleClickEvent(self, e):
        if self.ip: QApplication.clipboard().setText(self.ip)
    def play(self, *kinds):
        """Plays Windows system sounds in order: ip=chimes, down=Speech Off, up=Speech On."""
        if not self.cfg.get("sound", True) or sys.platform != "win32": return
        files = {"ip": "chimes.wav", "down": "Speech Off.wav", "up": "Speech On.wav"}
        def job():
            try:
                import winsound
                media = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Media")
                for k in kinds:
                    path = os.path.join(media, files[k])
                    if os.path.exists(path): winsound.PlaySound(path, winsound.SND_FILENAME)
                    else: winsound.MessageBeep(winsound.MB_OK)
            except Exception: pass
        threading.Thread(target=job, daemon=True).start()

    def set_sound(self, on):
        self.cfg["sound"] = bool(on); save_cfg(self.cfg)
        if on: self.play("ip")  # preview

    def start_clean(self):
        if self.cleaning: return
        self.cleaning, self.anim_t = True, time.time()
        self.clean_msg, self.clean_t = "Cleaning RAM…", time.time()
        self.timer.setInterval(40)
        def job(): self.bus.clean.emit(clean_ram())
        threading.Thread(target=job, daemon=True).start()

    def on_clean(self, freed):
        wait = max(0, int((1.7 - (time.time() - self.anim_t)) * 1000))  # let the animation finish
        def done():
            self.cleaning = False
            self.clean_msg = f"Freed {freed} MB" if freed > 0 else "RAM already tidy"
            self.clean_t = time.time(); self._st_t = 0.0
        QTimer.singleShot(wait, done)

    def fit(self):
        """Width = content + tiny padding; right edge stays where it is."""
        H = PILL[1]; self.s = 1.0
        x0 = 0.5 + 6 + (H - 12) + 10
        w1 = QFontMetrics(self.F("Cascadia Mono, Consolas", 15, QFont.DemiBold)).horizontalAdvance(self.ip or "000.000.000.00")
        if self.geo: w1 += 16.5 + 7
        fm2 = QFontMetrics(self.F("Segoe UI Variable, Segoe UI", 11.5))
        v = lambda a: "–" if a is None else str(a)
        tail = f"{self.lats[-1]} ms" if self.lats else "000 ms"
        w2 = max(fm2.horizontalAdvance(t) for t in ("RAM: 00%   CPU: 00%   000 ms",
                 f"RAM: {v(self.stats[0])}%   CPU: {v(self.stats[1])}%   {tail}"))
        W = int(x0 + max(w1, w2) + 12)
        if W != self.width():
            right = self.x() + self.width()
            self.setFixedSize(W, H); self.move(right - W, self.y())

    def is_pill(self): return bool(THEMES[self.theme].get("pill"))

    def apply_mode(self):
        if self.is_pill():
            self.setFixedSize(*PILL)
        else:
            self.setMinimumSize(210, 110); self.setMaximumSize(16777215, 16777215)
            self.resize(int(self.cfg.get("w", DEF_W)), int(self.cfg.get("h", DEF_H)))

    def place(self):
        name = self.cfg.get("screen")
        scr = next((x for x in QGuiApplication.screens() if x.name() == name), None) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(g.left() + g.width() - self.width(), g.top())  # flush top-right, no margin

    def set_screen(self, name):
        self.cfg["screen"] = name; save_cfg(self.cfg); self.place()

    def set_theme(self, n):
        if THEMES[n].get("pill") and not self.is_pill(): self.cfg["prev"] = self.theme
        self.theme = n; self.cfg["theme"] = n; save_cfg(self.cfg); self.apply_mode(); self.update()

    def rocket(self, p, ic, col, off=QPointF(0, 0), flame=0.0):
        u = ic.width() / 2
        p.save(); p.translate(ic.center() + off); p.rotate(45); p.setPen(Qt.NoPen)
        body = QPainterPath(); body.moveTo(0, -0.62 * u)
        body.cubicTo(0.42 * u, -0.25 * u, 0.34 * u, 0.25 * u, 0.22 * u, 0.4 * u)
        body.lineTo(-0.22 * u, 0.4 * u)
        body.cubicTo(-0.34 * u, 0.25 * u, -0.42 * u, -0.25 * u, 0, -0.62 * u)
        p.setBrush(col); p.drawPath(body)
        for sg in (-1, 1):
            p.drawPolygon(QPolygonF([QPointF(sg * 0.22 * u, 0.02 * u), QPointF(sg * 0.5 * u, 0.46 * u), QPointF(sg * 0.22 * u, 0.32 * u)]))
        p.setBrush(QColor("#0d1d36")); p.drawEllipse(QPointF(0, -0.13 * u), 0.14 * u, 0.14 * u)
        L = 0.3 + 0.9 * flame
        p.setBrush(QColor(255, 170, 60, 230)); p.drawPolygon(QPolygonF([QPointF(-0.14 * u, 0.42 * u), QPointF(0, (0.42 + L) * u), QPointF(0.14 * u, 0.42 * u)]))
        p.setBrush(QColor(255, 235, 150, 240)); p.drawPolygon(QPolygonF([QPointF(-0.07 * u, 0.42 * u), QPointF(0, (0.42 + L * 0.55) * u), QPointF(0.07 * u, 0.42 * u)]))
        p.restore()

    def flag_box(self, p, cc, r, mut, fs=8):
        path = QPainterPath(); path.addRoundedRect(r, 2 * self.s, 2 * self.s)
        p.save(); p.setClipPath(path)
        if not draw_flag(p, cc, r):
            if self.flag and self.flag_cc == cc: p.drawPixmap(r, self.flag, QRectF(self.flag.rect()))
            else:
                p.fillRect(r, QColor(128, 128, 128, 70)); p.setPen(mut)
                p.setFont(self.F("Segoe UI Variable, Segoe UI", fs, QFont.DemiBold)); p.drawText(r, Qt.AlignCenter, cc)
        p.restore()
        p.setPen(QPen(QColor(128, 128, 128, 90), 1)); p.setBrush(Qt.NoBrush); p.drawRoundedRect(r, 2 * self.s, 2 * self.s)

    def paint_pill(self, p, t, acc, okc, bad, mut, txt):
        W, H = self.width(), self.height(); s = self.s = H / 54.0
        r = QRectF(0.5, 0, W - 0.5, H); rad = r.height() / 2
        c1, c2 = QColor(t["bg1"]), QColor(t["bg2"]); c1.setAlpha(t["a"]); c2.setAlpha(t["a"])
        g = QLinearGradient(0, 0, 0, H); g.setColorAt(0, c1); g.setColorAt(1, c2)
        shape = QPainterPath(); shape.moveTo(r.right(), r.top()); shape.lineTo(r.left() + rad, r.top())
        shape.arcTo(QRectF(r.left(), r.top(), 2 * rad, 2 * rad), 90, 180)
        shape.lineTo(r.right(), r.bottom()); shape.closeSubpath()
        p.setBrush(QBrush(g)); p.setPen(QPen(QColor(*t["line"]), 1)); p.drawPath(shape)
        online = self.ok is True
        stc = okc if online else (mut if self.ok is None else bad)
        d = r.height() - 12 * s
        ic = QRectF(r.left() + 6 * s, r.center().y() - d / 2, d, d)
        ig = QLinearGradient(ic.topLeft(), ic.bottomRight()); ig.setColorAt(0, QColor("#23477d")); ig.setColorAt(1, QColor("#0d1d36"))
        p.setBrush(ig); p.setPen(QPen(stc, 1.6 * s)); p.drawEllipse(ic)
        self._arrow = ic  # clickable rocket area
        e = time.time() - self.anim_t if self.anim_t else 99.0
        off, flame = QPointF(0, 0), 0.0
        if e < 0.5:      # shake + ignite
            k = e / 0.5; off = QPointF(math.sin(e * 95) * 1.3 * s * k, math.cos(e * 120) * 1.3 * s * k); flame = k
        elif e < 1.0:    # launch up-right
            k = (e - 0.5) / 0.5; off = QPointF(k * k * d * 0.9, -k * k * d * 0.9); flame = 1.0
        elif e < 1.5:    # come back in from bottom-left
            k = (e - 1.0) / 0.5; q = (1 - k) ** 3; off = QPointF(-q * d * 0.9, q * d * 0.9); flame = 1 - k
        p.save(); clip = QPainterPath(); clip.addEllipse(ic.adjusted(2 * s, 2 * s, -2 * s, -2 * s)); p.setClipPath(clip)
        self.rocket(p, ic, acc.lighter(150), off, flame)
        p.restore()
        if 0.85 < e < 1.8:  # shockwave ring
            k = (e - 0.85) / 0.95; rc = QColor(acc); rc.setAlpha(int(200 * (1 - k)))
            p.setPen(QPen(rc, 2 * s)); p.setBrush(Qt.NoBrush)
            p.drawEllipse(ic.center(), d / 2 + k * 5 * s, d / 2 + k * 5 * s)
        x0, x1 = ic.right() + 10 * s, r.right() - 10 * s
        flash = max(0.0, 1 - (time.time() - self.flash_t) / 3.0) if self.flash_t else 0.0
        ipcol = mix(acc, bad, flash) if (online or self.ok is None) else mut
        y1, x = H / 2 - 2 * s, x0
        if self.geo:
            fh = 11 * s; self.flag_box(p, self.geo[0], QRectF(x, y1 - fh + s, fh * 1.5, fh), mut, 7); x += fh * 1.5 + 7 * s
        size, text = 15, self.ip or "…"
        while size > 9:
            if QFontMetrics(self.F("Cascadia Mono, Consolas", size, QFont.DemiBold)).horizontalAdvance(text) <= x1 - x: break
            size -= 1
        p.setFont(self.F("Cascadia Mono, Consolas", size, QFont.DemiBold)); p.setPen(ipcol); p.drawText(QPointF(x, y1), text)
        ram, cpu = self.stats
        v = lambda a: "–" if a is None else str(a)
        tail = f"{self.lats[-1]} ms" if (online and self.lats) else ("Offline" if self.ok is False else "")
        line = f"RAM: {v(ram)}%   CPU: {v(cpu)}%" + (f"   {tail}" if tail else "")
        pen = txt
        if self.clean_msg and (self.cleaning or time.time() - self.clean_t < 3.5):
            line, pen = self.clean_msg, (acc if self.cleaning else okc)
        p.setFont(self.F("Segoe UI Variable, Segoe UI", 11.5)); p.setPen(pen)
        p.drawText(QPointF(x0, H / 2 + 14 * s), p.fontMetrics().elidedText(line, Qt.ElideRight, int(x1 - x0)))
    def reset_size(self):
        self.resize(DEF_W, DEF_H); self.cfg.update(w=DEF_W, h=DEF_H); save_cfg(self.cfg)
    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("Copy IP", lambda: self.ip and QApplication.clipboard().setText(self.ip))
        tm = m.addMenu("Theme"); grp = QActionGroup(tm)
        for n in THEMES:
            a = tm.addAction(n); a.setCheckable(True); a.setChecked(n == self.theme); grp.addAction(a)
            a.triggered.connect(lambda _=False, n=n: self.set_theme(n))
        dm = m.addMenu("Display"); dg = QActionGroup(dm)
        cur = self.cfg.get("screen")
        for sc in QGuiApplication.screens():
            a = dm.addAction(f"{sc.name()}  ({sc.size().width()}×{sc.size().height()})"); a.setCheckable(True)
            a.setChecked(sc.name() == cur or (cur is None and sc is QGuiApplication.primaryScreen())); dg.addAction(a)
            a.triggered.connect(lambda _=False, n=sc.name(): self.set_screen(n))
        m.addAction("Snap to top-right corner", self.place)
        if sys.platform == "win32":
            a = m.addAction("Start with Windows"); a.setCheckable(True); a.setChecked(autostart_get())
            a.triggered.connect(lambda on: autostart_set(on))
        sa = m.addAction("Sound alerts"); sa.setCheckable(True); sa.setChecked(self.cfg.get("sound", True))
        sa.triggered.connect(self.set_sound)
        m.addAction("Clean RAM", self.start_clean)
        m.addAction("Reset change counter", lambda: setattr(self, "changes", 0))
        m.addSeparator(); m.addAction("Quit", QApplication.quit)
        m.exec(e.globalPos())

    # drawing
    def F(self, fam, px, w=QFont.Normal):
        f = QFont(); f.setFamilies([x.strip() for x in fam.split(",")])
        f.setPixelSize(max(6, int(px * self.s))); f.setWeight(w); return f

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        s = self.s = max(0.7, min(W / DEF_W, H / DEF_H))
        t = THEMES[self.theme]
        acc, okc, bad, mut, txt = (QColor(t[k]) for k in ("acc", "ok", "bad", "mut", "txt"))
        if t.get("pill"):
            self.paint_pill(p, t, acc, okc, bad, mut, txt); return
        card = QRectF(4, 4, W - 8, H - 8); pad = 14 * s

        c1, c2 = QColor(t["bg1"]), QColor(t["bg2"]); c1.setAlpha(t["a"]); c2.setAlpha(t["a"])
        g = QLinearGradient(0, 0, 0, H); g.setColorAt(0, c1); g.setColorAt(1, c2)
        p.setBrush(QBrush(g)); p.setPen(QPen(QColor(*t["line"]), 1)); p.drawRoundedRect(card, 16 * s, 16 * s)

        online = self.ok is True
        flash = max(0.0, 1 - (time.time() - self.flash_t) / 3.0) if self.flash_t else 0.0
        label, col = ("Online", okc) if online else (("Connecting", mut) if self.ok is None else ("Offline", bad))
        ipcol = mix(acc, bad, flash) if (online or self.ok is None) else mut

        # header
        p.setFont(self.F("Segoe UI Variable, Segoe UI", 10.5, QFont.DemiBold))
        pw = p.fontMetrics().horizontalAdvance(label) + 24 * s
        pill = QRectF(card.right() - pad - pw, card.top() + 10 * s, pw, 18 * s)
        pc = QColor(col); pc.setAlpha(40); p.setPen(Qt.NoPen); p.setBrush(pc); p.drawRoundedRect(pill, 9 * s, 9 * s)
        p.setBrush(col); p.drawEllipse(QPointF(pill.left() + 10 * s, pill.center().y()), 3 * s, 3 * s)
        p.setPen(col); p.drawText(pill.adjusted(16 * s, 0, 0, 0), Qt.AlignCenter, label)
        p.setPen(mut); p.setFont(self.F("Segoe UI Variable, Segoe UI", 10.5))
        p.drawText(QRectF(card.left() + pad, pill.top(), 110 * s, pill.height()), Qt.AlignVCenter | Qt.AlignLeft, "Public IP")

        # IP (shrinks to fit width)
        size, text = 28, self.ip or "…"
        while size > 12:
            fm = QFontMetrics(self.F("Cascadia Mono, Consolas, Menlo", size, QFont.DemiBold))
            if fm.horizontalAdvance(text) <= card.width() - 2 * pad: break
            size -= 1
        p.setFont(self.F("Cascadia Mono, Consolas, Menlo", size, QFont.DemiBold)); p.setPen(ipcol)
        p.drawText(QPointF(card.left() + pad, card.top() + 56 * s), text)

        # geo line
        gy = card.top() + 76 * s; x = card.left() + pad
        p.setFont(self.F("Segoe UI Variable, Segoe UI", 11.5)); fm = p.fontMetrics()
        if self.geo:
            cc, city, asn, isp = self.geo
            fh = 12 * s; fw = fh * 1.5
            r = QRectF(x, gy - fh + 2 * s, fw, fh)
            path = QPainterPath(); path.addRoundedRect(r, 2 * s, 2 * s)
            p.save(); p.setClipPath(path)
            if not draw_flag(p, cc, r):
                if self.flag and self.flag_cc == cc:
                    p.drawPixmap(r, self.flag, QRectF(self.flag.rect()))
                else:
                    p.fillRect(r, QColor(128, 128, 128, 70)); p.setPen(mut)
                    p.setFont(self.F("Segoe UI Variable, Segoe UI", 8, QFont.DemiBold)); p.drawText(r, Qt.AlignCenter, cc)
            p.restore()
            p.setPen(QPen(QColor(128, 128, 128, 90), 1)); p.setBrush(Qt.NoBrush); p.drawRoundedRect(r, 2 * s, 2 * s)
            p.setFont(self.F("Segoe UI Variable, Segoe UI", 11.5))
            x += fw + 7 * s
            info = "  ".join(v for v in (cc, city) if v) + ("  ·  " + " ".join(v for v in (asn, isp) if v) if (asn or isp) else "")
            p.setPen(QColor(txt)); p.drawText(QPointF(x, gy), fm.elidedText(info, Qt.ElideRight, int(card.right() - pad - x)))
        else:
            p.setPen(mut)
            msg = "Last known IP, no connection" if self.ok is False else ("Location unavailable" if online else "Detecting…")
            p.drawText(QPointF(x, gy), fm.elidedText(msg, Qt.ElideRight, int(card.right() - pad - x)))

        # sparkline
        top, bot = card.top() + 86 * s, card.bottom() - 24 * s
        if bot - top > 12 and len(self.lats) >= 2:
            mx, n, wd = max(max(self.lats), 80), len(self.lats), card.width() - 2 * pad
            pts = [QPointF(card.right() - pad - (n - 1 - i) * wd / 59, bot - v / mx * (bot - top)) for i, v in enumerate(self.lats)]
            path = QPainterPath(pts[0])
            for a, b in zip(pts, pts[1:]): path.quadTo(a, QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))
            path.lineTo(pts[-1])
            fill = QPainterPath(path); fill.lineTo(pts[-1].x(), bot); fill.lineTo(pts[0].x(), bot); fill.closeSubpath()
            lg = QLinearGradient(0, top, 0, bot); a1 = QColor(acc); a1.setAlpha(90); a0 = QColor(acc); a0.setAlpha(0)
            lg.setColorAt(0, a1); lg.setColorAt(1, a0)
            p.setPen(Qt.NoPen); p.setBrush(lg); p.drawPath(fill)
            p.setBrush(Qt.NoBrush); p.setPen(QPen(acc, 1.8 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.drawPath(path)
            p.setBrush(acc); p.setPen(Qt.NoPen); p.drawEllipse(pts[-1], 3 * s, 3 * s)

        # footer
        p.setFont(self.F("Cascadia Mono, Consolas", 10)); p.setPen(mut)
        ms = f"{self.lats[-1]} ms" if self.lats else "– ms"
        p.drawText(QPointF(card.left() + pad, card.bottom() - 9 * s), f"{ms}  ·  changes {self.changes}")
        p.drawText(QRectF(card.left() + pad, card.bottom() - 22 * s, card.width() - 2 * pad - 12 * s, 14 * s),
                   Qt.AlignRight | Qt.AlignVCenter, self.last_ok or "")
        # resize grip
        gc = QColor(mut); gc.setAlpha(110); p.setPen(QPen(gc, 1.2))
        for k in (4, 8, 12):
            p.drawLine(QPointF(card.right() - k * s - 3, card.bottom() - 4), QPointF(card.right() - 4, card.bottom() - k * s - 3))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    os.makedirs(CFG_DIR, exist_ok=True)
    lock = QLockFile(os.path.join(CFG_DIR, "app.lock"))
    if not lock.tryLock(100): sys.exit(0)  # already running
    _c0 = load_cfg()
    if sys.platform == "win32" and not _c0.get("autostart_init"):
        autostart_set(True); _c0["autostart_init"] = True; save_cfg(_c0)  # enable once on first run
    w = Overlay(); w.show()
    sys.exit(app.exec())
