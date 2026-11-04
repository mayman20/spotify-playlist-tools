"""
APE Music Visualizer v3
========================
Modes  : SCOPE / BARS / MIRROR / WATERFALL / LISSAJOUS / LUSH / COMBO / MEGA / VU
Keys   : SPACE = next mode  |  O = cycle orientation  |  Q = quit
Launch : python visualizer.py  [--top | --bottom]
"""

import argparse, atexit, ctypes, ctypes.wintypes
import collections, math, os, threading, time

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

import numpy as np
import pygame
import pygame.surfarray as surfarray
import pyaudiowpatch as pyaudio

# ── Config ─────────────────────────────────────────────────────────────────────

VERT_THICKNESS  = 180        # px (right-edge panel width)
HORIZ_THICKNESS = 160        # px (top/bottom panel height)
FPS             = 60
CHUNK_SIZE      = 512
FFT_WINDOW      = 2048
PRE_EMPHASIS    = 0.97
DB_FLOOR, DB_CEIL = -30.0, 55.0
PREFERRED_LOOPBACK = "Realtek"

# CAVA-style ballistic smoothing constants
NOISE_REDUCTION = 2.0
FALL_INC        = 0.028      # gravity acceleration per frame
REF_FPS         = 66.0

# Monstercat spread (makes BARS look like a mountain range)
MONSTERCAT       = True
MONSTERCAT_STR   = 1.6

MODES = ["MEGA"]

# ── Spotify (optional) ─────────────────────────────────────────────────────────

KEYS_MAJOR = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    from spotipy.cache_handler import CacheFileHandler
    _SPOTIPY_OK = True
except ImportError:
    _SPOTIPY_OK = False

# ── Colors ─────────────────────────────────────────────────────────────────────

BG          = (  6,   6,  10)
GRID        = ( 18,  35,  18)
OSC_CORE    = ( 60, 255,  80)   # bright phosphor green
OSC_GLOW    = (  0,  90,  30)   # green glow layers
DIVIDER     = ( 40,  80,  60)
MODE_COLOR  = ( 55,  55,  75)
VU_G        = ( 30, 215,  80)
VU_Y        = (255, 195,  30)
VU_R        = (255,  40,  40)

SPEC_STOPS = [
    ( 20,  60, 255), ( 30, 160, 255), ( 20, 240, 210), ( 40, 255, 100),
    (160, 255,  50), (255, 215,  30), (255, 110,  25), (255,  35,  50),
]

THERMAL_STOPS = [   # waterfall colormap
    (  0, (  0,   0,   0)),
    ( 55, (  0,   0, 160)),
    (120, (  0, 160, 220)),
    (185, (220, 160,   0)),
    (230, (255, 220,  50)),
    (255, (255, 255, 255)),
]

# ── Color helpers ──────────────────────────────────────────────────────────────

def _lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

def _spec_color(idx, n, boost=1.0):
    t = idx / max(n - 1, 1)
    s = t * (len(SPEC_STOPS) - 1)
    lo, hi = int(s), min(int(s) + 1, len(SPEC_STOPS) - 1)
    c = _lerp(SPEC_STOPS[lo], SPEC_STOPS[hi], s - lo)
    return tuple(min(255, int(v * boost)) for v in c)

def _vu_color(v):
    if v < 0.70: return _lerp(VU_G, VU_Y, v / 0.70)
    if v < 0.90: return _lerp(VU_Y, VU_R, (v - 0.70) / 0.20)
    return VU_R

# ── Oscilloscope image overlay ─────────────────────────────────────────────────

_osc_raw   = None
_osc_cache = {}   # keyed by (w, h)

def _build_osc_overlay(w, h):
    """Scale osc.png to (w,h) with the CRT screen area made transparent."""
    global _osc_raw
    key = (w, h)
    if key in _osc_cache:
        return _osc_cache[key]
    if _osc_raw is None:
        for p in [os.path.join(os.path.dirname(__file__), "osc.png"),
                  os.path.join(os.path.expanduser("~"), "Downloads", "osc.png")]:
            if os.path.exists(p):
                try: _osc_raw = pygame.image.load(p)
                except Exception: pass
                break
    if _osc_raw is None:
        _osc_cache[key] = None; return None
    iw, ih = _osc_raw.get_size()
    scale  = h / ih
    sw     = int(iw * scale)
    x_off  = max(0, (sw - w) // 2)
    scaled  = pygame.transform.smoothscale(_osc_raw, (sw, h))
    overlay = pygame.Surface((w, h), pygame.SRCALPHA)
    overlay.blit(scaled, (-x_off, 0))
    # Screen area fractions for Tektronix 475A
    sx0 = max(0, int(0.27 * sw) - x_off)
    sy0 = int(0.09 * h)
    sx1 = min(w, int(0.63 * sw) - x_off)
    sy1 = int(0.85 * h)
    scr = pygame.Rect(sx0, sy0, max(1, sx1-sx0), max(1, sy1-sy0))
    overlay.fill((0, 0, 0, 0), scr)
    # Make light-coloured instrument body semi-transparent so it blends
    px = pygame.surfarray.pixels3d(overlay)
    ax = pygame.surfarray.pixels_alpha(overlay)
    brightness = px.max(axis=2).astype(np.float32) / 255.0
    # Pixels brighter than 0.50 fade out; fully gone by 0.80
    fade = np.clip((0.80 - brightness) / 0.30, 0.0, 1.0)
    # Keep screen area (already 0) untouched
    already_clear = (ax == 0)
    ax[:] = np.where(already_clear, 0, (fade * 200).astype(np.uint8))
    del px, ax
    result = (overlay, scr)
    _osc_cache[key] = result
    return result


def _build_thermal_lut():
    lut = np.zeros((256, 3), dtype=np.uint8)
    stops = THERMAL_STOPS
    for i in range(256):
        for j in range(len(stops) - 1):
            v0, c0 = stops[j];  v1, c1 = stops[j + 1]
            if v0 <= i <= v1:
                t = (i - v0) / (v1 - v0)
                lut[i] = [int(c0[k] + (c1[k] - c0[k]) * t) for k in range(3)]
                break
    return lut

# ── Windows API ────────────────────────────────────────────────────────────────

_u32 = ctypes.windll.user32;  _shell = ctypes.windll.shell32
HWND_TOPMOST = -1;  GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80;  WS_EX_APPWINDOW = 0x40000
SWP_FRAMECHANGED = 0x0020
ABM_NEW=0; ABM_REMOVE=1; ABM_SETPOS=3; ABE_TOP=1; ABE_BOTTOM=3; ABE_RIGHT=2

class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize",ctypes.c_uint32),("hWnd",ctypes.wintypes.HWND),
                ("uCallbackMessage",ctypes.c_uint32),("uEdge",ctypes.c_uint32),
                ("rc",ctypes.wintypes.RECT),("lParam",ctypes.c_long)]

_ab_hwnd = None

def _register_appbar(hwnd, edge, rc):
    global _ab_hwnd;  _ab_hwnd = hwnd
    abd = APPBARDATA(); abd.cbSize = ctypes.sizeof(APPBARDATA); abd.hWnd = hwnd
    abd.uCallbackMessage = _u32.RegisterWindowMessageW("APEVisAppBar3")
    _shell.SHAppBarMessage(ABM_NEW, ctypes.byref(abd))
    abd.uEdge = edge;  abd.rc = rc
    _shell.SHAppBarMessage(ABM_SETPOS, ctypes.byref(abd))

def _unregister_appbar():
    if _ab_hwnd is None: return
    abd = APPBARDATA(); abd.cbSize = ctypes.sizeof(APPBARDATA); abd.hWnd = _ab_hwnd
    _shell.SHAppBarMessage(ABM_REMOVE, ctypes.byref(abd))

atexit.register(_unregister_appbar)

def _screen_size():
    return _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1)

def _work_area():
    """Usable area excluding taskbar: (x, y, w, h)."""
    rc = ctypes.wintypes.RECT()
    _u32.SystemParametersInfoW(48, 0, ctypes.byref(rc), 0)  # SPI_GETWORKAREA=48
    return rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top

def _apply_window(hwnd, x, y, w, h):
    _u32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_FRAMECHANGED)
    ex = _u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    _u32.SetWindowLongW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)

def _geometry(orientation, sw, sh):
    """Returns (win_x, win_y, win_w, win_h, edge, RECT)"""
    if orientation == "top":
        return 0, 0, sw, HORIZ_THICKNESS, ABE_TOP, \
               ctypes.wintypes.RECT(0, 0, sw, HORIZ_THICKNESS)
    if orientation == "bottom":
        return 0, sh - HORIZ_THICKNESS, sw, HORIZ_THICKNESS, ABE_BOTTOM, \
               ctypes.wintypes.RECT(0, sh - HORIZ_THICKNESS, sw, sh)
    # Right panel: use work area height to avoid overlapping taskbar
    _, wa_y, _, wa_h = _work_area()
    return sw - VERT_THICKNESS, wa_y, VERT_THICKNESS, wa_h, ABE_RIGHT, \
           ctypes.wintypes.RECT(sw - VERT_THICKNESS, wa_y, sw, wa_y + wa_h)

# ── Font cache ─────────────────────────────────────────────────────────────────

_FONTS = {}
def _fnt(size):
    if size not in _FONTS:
        _FONTS[size] = pygame.font.SysFont("consolas", size)
    return _FONTS[size]

# ── Audio capture ──────────────────────────────────────────────────────────────

class AudioCapture:
    def __init__(self):
        self._ring   = collections.deque([0.0] * FFT_WINDOW, maxlen=FFT_WINDOW)
        self._stereo = np.zeros((CHUNK_SIZE, 2), dtype=np.float32)
        self.sample_rate = 48000;  self.channels = 2
        self._lock   = threading.Lock();  self._running = False
        self.device_name = "searching..."

    def start(self):
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):  self._running = False

    def get_mono(self):
        with self._lock: return np.array(self._ring, dtype=np.float32)

    def get_stereo(self):
        with self._lock: return self._stereo.copy()

    def get_stereo_rms(self):
        s = self.get_stereo()
        return (min(float(np.sqrt(np.mean(s[:,0]**2)))*4.5, 1.0),
                min(float(np.sqrt(np.mean(s[:,1]**2)))*4.5, 1.0))

    def _find(self, p):
        try: wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError: return None
        lbs = [p.get_device_info_by_index(i) for i in range(p.get_device_count())
               if p.get_device_info_by_index(i).get("isLoopbackDevice")
               and p.get_device_info_by_index(i).get("maxInputChannels", 0) > 0]
        if PREFERRED_LOOPBACK:
            kw = PREFERRED_LOOPBACK.lower()
            for d in lbs:
                if kw in d["name"].lower(): return d
        try:
            df = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            for d in lbs:
                if d["name"] == df["name"]: return d
        except Exception: pass
        return lbs[0] if lbs else None

    def _run(self):
        p = pyaudio.PyAudio();  dev = self._find(p)
        if dev is None:
            print("  [!] No loopback device found."); p.terminate(); return
        self.device_name = dev["name"]
        self.sample_rate = int(dev["defaultSampleRate"])
        self.channels    = max(1, int(dev["maxInputChannels"]))
        s = p.open(format=pyaudio.paFloat32, channels=self.channels,
                   rate=self.sample_rate, input=True,
                   input_device_index=dev["index"], frames_per_buffer=CHUNK_SIZE)
        while self._running:
            try:
                raw = s.read(CHUNK_SIZE, exception_on_overflow=False)
                smp = np.frombuffer(raw, dtype=np.float32).copy()
                if self.channels >= 2:
                    mat = smp.reshape(-1, self.channels)
                    mono = mat[:,:2].mean(axis=1);  stereo = mat[:,:2]
                else:
                    mono = smp;  stereo = np.column_stack([smp, smp])
                with self._lock:
                    self._ring.extend(mono.tolist());  self._stereo = stereo
            except Exception: pass
        s.stop_stream(); s.close(); p.terminate()

# ── Spotify client ─────────────────────────────────────────────────────────────

class SpotifyClient:
    """
    Polls Spotify every 5s for current track + audio features.
    Requires SPOTIPY_CLIENT_ID + SPOTIPY_CLIENT_SECRET env vars.
    On first run: prints auth URL to terminal, paste into browser once.
    All subsequent runs: silent token refresh from .spotify_cache.
    """
    def __init__(self):
        self.track = self.artist = self.bpm = self.key = None
        self.energy = self.valence = None
        self.album_art = None       # pygame.Surface or None
        self._art_url  = None
        self.progress_ms  = 0
        self.duration_ms  = 0
        self.is_playing   = False
        self._active = False;  self._lock = threading.Lock()
        self._start()

    def _start(self):
        if not _SPOTIPY_OK: return
        if not (os.getenv("SPOTIPY_CLIENT_ID") and os.getenv("SPOTIPY_CLIENT_SECRET")):
            return
        try:
            sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                scope="user-read-currently-playing user-read-playback-state",
                redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI", "http://localhost:8888/callback"),
                open_browser=True,
                cache_handler=CacheFileHandler(
                    cache_path=os.path.join(os.path.dirname(__file__), ".spotify_cache")
                )
            ))
            self._sp = sp;  self._active = True
            threading.Thread(target=self._poll, daemon=True).start()
        except Exception as e:
            print(f"  [Spotify] Auth failed: {e}")

    def _fetch_art(self, url):
        import urllib.request, io
        try:
            data = urllib.request.urlopen(url, timeout=4).read()
            surf = pygame.image.load(io.BytesIO(data)).convert()
            with self._lock:
                self.album_art = surf
                self._art_url  = url
        except Exception:
            pass

    def _poll(self):
        while self._active:
            try:
                cur = self._sp.current_playback()
                if cur and cur.get("item"):
                    item    = cur["item"]
                    playing = bool(cur.get("is_playing"))
                    feat    = self._sp.audio_features([item["id"]])[0] or {}
                    key_idx = feat.get("key", -1)
                    mode    = feat.get("mode", 1)
                    key_str = (KEYS_MAJOR[key_idx] + (" maj" if mode else " min")
                               if key_idx >= 0 else "?")
                    art_url = None
                    try:
                        imgs = item.get("album", {}).get("images", [])
                        if imgs: art_url = imgs[-1]["url"]
                    except Exception: pass
                    with self._lock:
                        self.track       = item["name"][:28]
                        self.artist      = item["artists"][0]["name"][:28]
                        self.bpm         = round(feat.get("tempo", 0)) or None
                        self.key         = key_str
                        self.energy      = feat.get("energy")
                        self.valence     = feat.get("valence")
                        self.progress_ms = cur.get("progress_ms") or 0
                        self.duration_ms = item.get("duration_ms") or 0
                        self.is_playing  = playing
                    if art_url and art_url != self._art_url:
                        threading.Thread(target=self._fetch_art, args=(art_url,), daemon=True).start()
                else:
                    with self._lock:
                        self.track = self.artist = self.bpm = self.key = None
                        self.progress_ms = self.duration_ms = 0
            except Exception:
                pass
            time.sleep(5)

    def get(self):
        with self._lock:
            return (self.track, self.artist, self.bpm, self.key,
                    self.energy, self.valence,
                    self.album_art, self.progress_ms, self.duration_ms, self.is_playing)

# ── CAVA ballistic smoothing ───────────────────────────────────────────────────

class BallisticSmoother:
    """
    Implements CAVA's two-stage filter:
      1. Quadratic gravity fall-off  (instant attack, physics-based decay)
      2. Leaky integral smoothing    (removes frame-to-frame jitter)
    """
    def __init__(self, n):
        self.peaks    = np.zeros(n)
        self.fall_acc = np.zeros(n)
        self.mem      = np.zeros(n)
        self.prev     = np.zeros(n)
        self.n        = n

    def reset(self, n):
        self.__init__(n)

    def update(self, raw: np.ndarray, fps: float) -> np.ndarray:
        fm   = REF_FPS / max(fps, 1.0)
        grav = (fm ** 2.5) * 2.0 / NOISE_REDUCTION
        igral = fm ** 0.1

        rising  = raw >= self.prev
        falling = ~rising

        # Attack: instant snap to new peak
        self.peaks[rising]    = raw[rising]
        self.fall_acc[rising] = 0.0
        out = raw.copy()

        # Decay: quadratic gravity
        out[falling] = self.peaks[falling] * (
            1.0 - self.fall_acc[falling] ** 2 * grav
        )
        out[falling] = np.clip(out[falling], 0.0, None)
        self.fall_acc[falling] += FALL_INC

        out = np.clip(out, 0.0, 1.0)
        self.prev[:] = out
        return out

# ── Spectrum compute ───────────────────────────────────────────────────────────

def compute_spectrum(buf, sample_rate, n_bars):
    """FFT → log bins → pre-emphasis → dB → [0,1] raw levels."""
    if len(buf) < 64:
        return np.zeros(n_bars)
    pe  = np.append(buf[0], buf[1:] - PRE_EMPHASIS * buf[:-1])
    mag = np.abs(np.fft.rfft(pe * np.hanning(len(pe))))
    frq = np.fft.rfftfreq(len(pe), 1.0 / sample_rate)
    log_lo, log_hi = math.log10(30.0), math.log10(16000.0)
    lvl = np.zeros(n_bars)
    for i in range(n_bars):
        f0 = 10 ** (log_lo + (i / n_bars) * (log_hi - log_lo))
        f1 = 10 ** (log_lo + ((i+1) / n_bars) * (log_hi - log_lo))
        m  = (frq >= f0) & (frq < f1)
        if m.any():
            val = float(np.max(mag[m]))
        else:
            # No bins in range — use nearest single bin (common at low freqs)
            val = float(mag[np.argmin(np.abs(frq - (f0 + f1) * 0.5))])
        db  = 20 * math.log10(val + 1e-10)
        raw = max(0.0, min(1.0, (db - DB_FLOOR) / (DB_CEIL - DB_FLOOR)))
        lvl[i] = raw ** 0.6  # gamma — stretches bars to use full height
    return lvl

def monstercat(levels, strength=MONSTERCAT_STR):
    """Spread peaks to neighbours — creates the mountain-range look."""
    n   = len(levels)
    idx = np.arange(n)
    de  = np.abs(idx[:, None] - idx[None, :])
    w   = np.where(de > 0, 1.0 / (strength * 1.5) ** de, 1.0)
    return (levels[None, :] * w).max(axis=1)

# ── Peak hold arrays ───────────────────────────────────────────────────────────

class PeakHolder:
    def __init__(self, n):
        self.vals  = np.zeros(n)
        self.times = np.zeros(n)

    def reset(self, n): self.__init__(n)

    def update(self, levels):
        now = time.monotonic()
        rising = levels > self.vals
        self.vals[rising]  = levels[rising]
        self.times[rising] = now
        age  = now - self.times
        decay_mask = age > 1.4
        self.vals[decay_mask] = np.maximum(
            0.0, self.vals[decay_mask] - (age[decay_mask] - 1.4) * 2.0
        )

# ── Draw helpers ───────────────────────────────────────────────────────────────

def _bar_energy(levels):
    return float(np.mean(levels)) if len(levels) else 0.0

def _bass_energy(levels, n):
    return float(np.mean(levels[:max(1, n//7)])) if len(levels) else 0.0

# ── DRAW: Oscilloscope ─────────────────────────────────────────────────────────

def _draw_scope_interior(surf, buf):
    """Phosphor graticule + trace — drawn inside the CRT screen area."""
    w, h = surf.get_size()
    GC = (18, 48, 28)
    for i in range(1, 10):
        pygame.draw.line(surf, GC, (int(i*w/10), 0), (int(i*w/10), h), 1)
    for i in range(1, 8):
        pygame.draw.line(surf, GC, (0, int(i*h/8)), (w, int(i*h/8)), 1)
    CC = (30, 72, 42)
    pygame.draw.line(surf, CC, (0, h//2), (w, h//2), 1)
    pygame.draw.line(surf, CC, (w//2, 0), (w//2, h), 1)
    for i in range(50):
        x = int(i*w/50)
        pygame.draw.line(surf, GC, (x, h//2-2), (x, h//2+2), 1)
    for i in range(40):
        y = int(i*h/40)
        pygame.draw.line(surf, GC, (w//2-2, y), (w//2+2, y), 1)
    if len(buf) < 2: return
    cy = h // 2
    idx = np.linspace(0, len(buf)-1, w).astype(int)
    pts = [(x, max(1, min(h-2, int(cy - buf[idx[x]] * cy * 0.88)))) for x in range(w)]
    if len(pts) >= 2:
        pygame.draw.lines(surf, (0, 55, 20),  False, pts, 7)
        pygame.draw.lines(surf, (0, 135, 50), False, pts, 4)
        pygame.draw.lines(surf, (35, 235, 85), False, pts, 2)
        pygame.draw.lines(surf, (95, 255, 118), False, pts, 1)


def draw_scope(surf, buf):
    w, h = surf.get_size()
    result = _build_osc_overlay(w, h)
    if result:
        overlay, scr = result
        surf.fill((10, 10, 12))         # dark outside bezel
        clip = surf.subsurface(scr)
        clip.fill((2, 10, 10))          # phosphor dark teal screen
        _draw_scope_interior(clip, buf)
        surf.blit(overlay, (0, 0))      # bezel on top; screen area is transparent
    else:
        surf.fill((2, 10, 10))
        _draw_scope_interior(surf, buf)

# ── DRAW: Spectrum bars ────────────────────────────────────────────────────────

def draw_bars(surf, levels, peaks: PeakHolder, invert=False, energy=0.0):
    w, h = surf.get_size();  n = len(levels)
    bw   = w / n;  pad = max(1, int(bw * 0.12))
    peaks.update(levels)
    for i in range(n):
        lv  = levels[i];  pk = peaks.vals[i]
        bx  = int(i * bw) + pad;  bwidth = max(1, int(bw) - pad*2)
        bh  = int(lv * (h - 4));  by = (2 if invert else h - bh - 2)
        col = _spec_color(i, n, boost=1.0 + energy * 0.4)
        # dim track
        pygame.draw.rect(surf, _lerp(BG, col, 0.06), (bx, 2, bwidth, h-4))
        if bh > 0:
            pygame.draw.rect(surf, col, (bx, by, bwidth, bh))
            cap = min(3, bh)
            pygame.draw.rect(surf, _lerp(col, (255,255,255), 0.5),
                             (bx, by if invert else by, bwidth, cap))
        # peak tick
        ph = int(pk * (h-4))
        if ph > 3:
            py = (ph + 2) if invert else (h - ph - 3)
            pygame.draw.rect(surf, (210,210,210), (bx, py, bwidth, 2))

# ── DRAW: Mirror (symmetric) ──────────────────────────────────────────────────

def draw_mirror(surf, levels, peaks: PeakHolder, energy=0.0):
    w, h = surf.get_size();  cy = h // 2;  n = len(levels)
    bw   = w / n;  pad = max(1, int(bw * 0.12))
    peaks.update(levels)
    for i in range(n):
        lv  = levels[i];  pk = peaks.vals[i]
        bx  = int(i * bw) + pad;  bwidth = max(1, int(bw) - pad*2)
        bh  = int(lv * cy * 0.95)
        col = _spec_color(i, n, boost=1.0 + energy * 0.5)
        pygame.draw.rect(surf, _lerp(BG, col, 0.05), (bx, 2, bwidth, h-4))
        if bh > 0:
            bright = _lerp(col, (255,255,255), 0.5)
            pygame.draw.rect(surf, col, (bx, cy-bh, bwidth, bh))
            pygame.draw.rect(surf, col, (bx, cy, bwidth, bh))
            pygame.draw.rect(surf, bright, (bx, cy-bh, bwidth, min(3,bh)))
            pygame.draw.rect(surf, bright, (bx, cy+bh-min(3,bh), bwidth, min(3,bh)))
        ph = int(pk * cy * 0.95)
        if ph > 3:
            pygame.draw.rect(surf, (195,195,195), (bx, cy-ph-1, bwidth, 2))
            pygame.draw.rect(surf, (195,195,195), (bx, cy+ph,   bwidth, 2))
    pygame.draw.line(surf, DIVIDER, (0, cy), (w, cy), 1)

# ── DRAW: Waterfall spectrogram ───────────────────────────────────────────────

def draw_waterfall(surf, wf_surf, levels, lut, scroll_dir="down"):
    w, h = wf_surf.get_size()
    # Scroll existing content
    if scroll_dir == "down":
        wf_surf.scroll(0, 1)
        y_row = 0
    else:
        wf_surf.scroll(0, -1)
        y_row = h - 1

    # Map levels to LUT indices and paint one row
    n = len(levels)
    if n > 0:
        indices = np.clip((levels * 255).astype(np.uint8), 0, 255)
        # Draw one bar-width segment per frequency bar
        for i in range(n):
            x0 = int(i * w / n); x1 = int((i + 1) * w / n)
            if x1 > x0:
                color = (int(lut[indices[i], 0]), int(lut[indices[i], 1]), int(lut[indices[i], 2]))
                wf_surf.fill(color, (x0, y_row, x1 - x0, 1))

    surf.blit(wf_surf, (0, 0))

# ── DRAW: Lissajous (L vs R phase scope) ─────────────────────────────────────

def draw_lissajous(surf, liss_surf, stereo):
    w, h = liss_surf.get_size()
    # Fade existing content
    try:
        px = surfarray.pixels3d(liss_surf)
        np.multiply(px, 0.82, out=px, casting='unsafe')
        del px
    except Exception:
        liss_surf.fill((0, 0, 0))

    if stereo is None or len(stereo) < 2:
        surf.blit(liss_surf, (0, 0)); return

    L = np.clip(stereo[:, 0] * 3.5, -1.0, 1.0)
    R = np.clip(stereo[:, 1] * 3.5, -1.0, 1.0)
    cx, cy = w // 2, h // 2
    scale  = min(w, h) * 0.47

    xs = np.clip((cx + L * scale).astype(int), 0, w - 1)
    ys = np.clip((cy - R * scale).astype(int), 0, h - 1)

    # Color by amplitude
    amp = np.sqrt(L**2 + R**2)
    max_a = float(amp.max()) if amp.max() > 0 else 1.0

    for i in range(0, len(xs), 2):   # every other sample — still dense enough
        a  = min(1.0, float(amp[i]) / max_a)
        col = _lerp((0, 60, 120), (0, 220, 100), a)
        liss_surf.set_at((xs[i], ys[i]), col)

    # Center crosshair
    pygame.draw.line(liss_surf, (25, 45, 25), (cx, 0), (cx, h), 1)
    pygame.draw.line(liss_surf, (25, 45, 25), (0, cy), (w, cy), 1)

    surf.blit(liss_surf, (0, 0))

# ── DRAW: VU Meter ─────────────────────────────────────────────────────────────

def draw_vu(surf, l, r, l_pk, r_pk):
    w, h = surf.get_size()
    gap  = 10;  bw = (w - gap*3) // 2;  uh = h - 30

    def _bar(x, lvl, pk, label):
        pygame.draw.rect(surf, (16,16,20), (x, 10, bw, uh))
        bh = int(lvl * uh)
        for zl, zh, col in ((0.0,0.70,VU_G),(0.70,0.90,VU_Y),(0.90,1.0,VU_R)):
            sl = int(zl*uh);  sh = int(zh*uh);  fill = min(bh,sh)-sl
            if fill > 0:
                pygame.draw.rect(surf, col, (x, 10+uh-sl-fill, bw, fill))
        for pct in range(10,100,10):
            ty = 10+uh - int(pct/100*uh)
            pygame.draw.line(surf, (40,40,52), (x,ty), (x+bw,ty), 1)
        now = time.monotonic()
        if lvl > pk[0]: pk[0]=lvl; pk[1]=now
        age = now - pk[1]
        if age > 1.4: pk[0] = max(0.0, pk[0]-(age-1.4)*1.5)
        py = 10+uh - int(pk[0]*uh)
        if py > 10: pygame.draw.rect(surf,(235,235,235),(x,py-2,bw,3))
        surf.blit(_fnt(11).render(label, True, (85,85,105)),
                  (x+bw//2-4, h-16))

    _bar(gap, l, l_pk, "L")
    _bar(gap*2+bw, r, r_pk, "R")

# ── DRAW: Info card ────────────────────────────────────────────────────────────

BAND_NAMES = [
    (30,  80,  "Sub Bass"), (80,  250, "Bass"),  (250, 500, "Low Mid"),
    (500, 2000,"Mid"),      (2000,4000,"Hi Mid"), (4000,16000,"Treble"),
]

def _dominant_band(levels, sample_rate):
    n     = len(levels)
    lo    = math.log10(30.0);  hi = math.log10(16000.0)
    best  = 0;  best_name = "—"
    for band_lo, band_hi, name in BAND_NAMES:
        # Find bar range for this band
        i0 = int((math.log10(band_lo) - lo) / (hi - lo) * n)
        i1 = int((math.log10(band_hi) - lo) / (hi - lo) * n)
        i0 = max(0, i0);  i1 = min(n-1, i1)
        if i1 > i0:
            energy = float(np.mean(levels[i0:i1]))
            if energy > best: best = energy; best_name = name
    return best_name

def _fmt_ms(ms):
    s = int(ms / 1000); return f"{s//60}:{s%60:02d}"

def draw_info(surf, spotify: SpotifyClient, levels, sample_rate, energy, bass):
    w, h = surf.get_size()
    surf.fill((5, 8, 12))
    pygame.draw.rect(surf, (15, 30, 20), (0, 0, w, h), 1)

    track, artist, bpm, key, sp_energy, valence, album_art, prog_ms, dur_ms, playing = spotify.get()
    y = 4

    if track:
        # Album art thumbnail (left side)
        art_size = min(42, h - 8)
        if album_art:
            try:
                scaled_art = pygame.transform.smoothscale(album_art, (art_size, art_size))
                surf.blit(scaled_art, (4, y))
            except Exception: pass
        tx = art_size + 8
        avail = w - tx - 4
        surf.blit(_fnt(10).render(track[:18], True, (215, 240, 210)), (tx, y))
        surf.blit(_fnt(9).render(artist[:20], True, (90, 128, 105)), (tx, y + 12))
        if bpm:
            surf.blit(_fnt(8).render(f"{bpm}bpm  {key or ''}", True, (100, 150, 115)), (tx, y + 23))
        y += art_size + 5
        pygame.draw.line(surf, (22, 44, 28), (4, y), (w-4, y), 1); y += 3
        # Progress bar
        if dur_ms > 0:
            pct = max(0.0, min(1.0, prog_ms / dur_ms))
            bw  = max(1, int(pct * (w - 10)))
            pygame.draw.rect(surf, (14, 28, 18), (4, y, w-10, 5))
            pygame.draw.rect(surf, (50, 180, 100), (4, y, bw, 5))
            t_str = f"{_fmt_ms(prog_ms)} / {_fmt_ms(dur_ms)}"
            surf.blit(_fnt(8).render(t_str, True, (55, 95, 65)), (4, y+6)); y += 15
        # Energy bar
        if sp_energy is not None:
            bw = max(1, int(sp_energy * (w-10)))
            pygame.draw.rect(surf, (14, 32, 18), (4, y, w-10, 5))
            pygame.draw.rect(surf, _lerp(VU_G, VU_Y, sp_energy), (4, y, bw, 5))
            surf.blit(_fnt(8).render("ENERGY", True, (52, 82, 60)), (4, y+6)); y += 14
        # Mood bar
        if valence is not None:
            bw = max(1, int(valence * (w-10)))
            pygame.draw.rect(surf, (14, 24, 38), (4, y, w-10, 5))
            pygame.draw.rect(surf, _lerp((65, 65, 195), (255, 210, 55), valence), (4, y, bw, 5))
            surf.blit(_fnt(8).render("MOOD", True, (52, 60, 98)), (4, y+6))
    else:
        # No Spotify — live band energy bars
        nb    = max(1, len(levels))
        segs  = [("SUB",0,.12),("BASS",.12,.28),("MID",.28,.55),("HI",.55,1.0)]
        bar_h = max(8, (h - y - 16) // len(segs) - 2)
        for lbl, i0f, i1f in segs:
            i0, i1 = int(i0f*nb), max(int(i0f*nb)+1, int(i1f*nb))
            val = float(np.mean(levels[i0:i1])) if nb > 0 else 0.0
            bw  = max(1, int(val * (w - 26)))
            pygame.draw.rect(surf, (10, 20, 12), (24, y, w-28, bar_h))
            pygame.draw.rect(surf, _spec_color(i0, nb), (24, y, bw, bar_h))
            surf.blit(_fnt(8).render(lbl, True, (60, 92, 68)), (4, y + bar_h//2 - 4))
            y += bar_h + 2
        if not spotify._active:
            surf.blit(_fnt(8).render("SPOTIFY: AUTH NEEDED", True, (80, 60, 40)), (4, y+1))
        else:
            surf.blit(_fnt(8).render("NOTHING PLAYING", True, (50, 55, 50)), (4, y+1))

# ── DRAW: Lush ─────────────────────────────────────────────────────────────────

def draw_lush(surf, buf, levels, peaks, energy, bass):
    w, h = surf.get_size()
    th   = int(h * 0.35)
    bg   = (max(0,min(255,BG[0]+int(bass*18))),
            max(0,min(255,BG[1]+int(bass*4))),
            max(0,min(255,BG[2]+int(bass*60))))
    surf.fill(bg)
    top = surf.subsurface(pygame.Rect(0, 0, w, th))
    bot = surf.subsurface(pygame.Rect(0, th+1, w, h-th-1))
    top.fill(bg);  bot.fill(bg)
    draw_scope(top, buf)
    draw_mirror(bot, levels, peaks, energy=energy)
    div_col = _lerp(DIVIDER, (80,200,120), bass*0.7)
    pygame.draw.line(surf, div_col, (0,th), (w,th), 1)

# ── DRAW: Combo ────────────────────────────────────────────────────────────────

def draw_combo(surf, buf, levels, peaks, energy, horiz=False):
    w, h = surf.get_size()
    if not horiz:
        sp = int(h*0.38)
        t  = surf.subsurface(pygame.Rect(0,0,w,sp))
        b  = surf.subsurface(pygame.Rect(0,sp+1,w,h-sp-1))
        draw_scope(t, buf)
        draw_bars(b, levels, peaks, energy=energy)
    else:
        half = w//2
        draw_scope(surf.subsurface(pygame.Rect(0,0,half-1,h)), buf)
        draw_bars(surf.subsurface(pygame.Rect(half,0,w-half,h)), levels, peaks, energy=energy)
        pygame.draw.line(surf, DIVIDER, (half-1,0),(half-1,h), 1)
    pygame.draw.line(surf, DIVIDER,
                     (0,int(h*0.38)) if not horiz else (w//2,0),
                     (w,int(h*0.38)) if not horiz else (w//2,h), 1)

# ── DRAW: MEGA (all panels) ───────────────────────────────────────────────────

def draw_mega(surf, buf, levels, peaks, energy, bass,
              spotify, sample_rate, mega_wf_surf, lut, stereo,
              lv, rv, l_pk, r_pk, mega_liss_surf):
    w, h = surf.get_size()
    horiz = (w > h)

    if not horiz:
        segs = [("info",12),("scope",12),("wfall",16),("mirror",24),("liss",14),("stereo",8),("vu",9)]
        y = 0
        for name, pct in segs:
            sh = max(8, int(h * pct / 100))
            s = surf.subsurface(pygame.Rect(0, y, w, sh)); s.fill(BG)
            if   name == "info":   draw_info(s, spotify, levels, sample_rate, energy, bass)
            elif name == "scope":  draw_scope(s, buf)
            elif name == "wfall":  draw_waterfall(s, mega_wf_surf, levels, lut)
            elif name == "mirror": draw_mirror(s, levels, peaks, energy=energy)
            elif name == "liss":   draw_lissajous(s, mega_liss_surf, stereo)
            elif name == "stereo": draw_stereo_card(s, stereo)
            elif name == "vu":     draw_vu(s, lv, rv, l_pk, r_pk)
            y += sh
            if name != "vu":
                pygame.draw.line(surf, DIVIDER, (0, y), (w, y), 1)
    else:
        segs = [("info",14),("scope",13),("wfall",19),("mirror",23),("liss",13),("stereo",7),("vu",8)]
        x = 0
        for name, pct in segs:
            sw2 = max(8, int(w * pct / 100))
            s = surf.subsurface(pygame.Rect(x, 0, sw2, h)); s.fill(BG)
            if   name == "info":   draw_info(s, spotify, levels, sample_rate, energy, bass)
            elif name == "scope":  draw_scope(s, buf)
            elif name == "wfall":  draw_waterfall(s, mega_wf_surf, levels, lut)
            elif name == "mirror": draw_mirror(s, levels, peaks, energy=energy)
            elif name == "liss":   draw_lissajous(s, mega_liss_surf, stereo)
            elif name == "stereo": draw_stereo_card(s, stereo)
            elif name == "vu":     draw_vu(s, lv, rv, l_pk, r_pk)
            x += sw2
            if name != "vu":
                pygame.draw.line(surf, DIVIDER, (x, 0), (x, h), 1)

# ── DRAW: Stereo field card ────────────────────────────────────────────────────

def draw_stereo_card(surf, stereo):
    w, h = surf.get_size()
    surf.fill((4, 7, 10))
    pygame.draw.rect(surf, (12, 25, 18), (0, 0, w, h), 1)
    if stereo is None or len(stereo) < 2: return
    L, R = stereo[:, 0], stereo[:, 1]
    rms_l = min(1.0, float(np.sqrt(np.mean(L**2))) * 5)
    rms_r = min(1.0, float(np.sqrt(np.mean(R**2))) * 5)
    denom = max(1e-9, float(np.sqrt(np.mean(L**2) * np.mean(R**2))))
    corr  = float(np.mean(L * R)) / denom
    width = min(1.0, (1.0 - corr) * 0.5)
    y = 3
    surf.blit(_fnt(8).render("STEREO WIDTH", True, (50, 82, 65)), (4, y)); y += 9
    bw = max(1, int(width * (w - 10)))
    pygame.draw.rect(surf, (12, 22, 18), (4, y, w-10, 5))
    pygame.draw.rect(surf, _lerp((20, 20, 160), (0, 215, 195), width), (4, y, bw, 5)); y += 9
    # L/R balance needle
    balance = 0.5 + (rms_r - rms_l) / max(0.01, rms_r + rms_l) * 0.45
    cx = int(4 + balance * (w - 10))
    pygame.draw.line(surf, (18, 38, 28), (4, y+2), (w-6, y+2), 1)
    pygame.draw.line(surf, (50, 210, 135), (max(6,cx), y), (max(6,cx), y+5), 2)
    surf.blit(_fnt(7).render("L", True, (45,75,55)), (4, y+6))
    surf.blit(_fnt(7).render("R", True, (45,75,55)), (w-9, y+6))


# ── DRAW: VIBE (mirror + lissajous + scope + VU + info) ───────────────────────

def draw_vibe(surf, buf, levels, peaks, energy, bass,
              spotify, sample_rate, vibe_liss_surf, stereo, lv, rv, l_pk, r_pk):
    w, h = surf.get_size()
    horiz = w > h

    if not horiz:
        segs = [("info",14),("scope",12),("mirror",38),("liss",26),("vu",10)]
        y = 0
        for name, pct in segs:
            sh = max(8, int(h * pct / 100))
            s = surf.subsurface(pygame.Rect(0, y, w, sh)); s.fill(BG)
            if   name == "info":   draw_info(s, spotify, levels, sample_rate, energy, bass)
            elif name == "scope":  draw_scope(s, buf)
            elif name == "mirror": draw_mirror(s, levels, peaks, energy=energy)
            elif name == "liss":   draw_lissajous(s, vibe_liss_surf, stereo)
            elif name == "vu":     draw_vu(s, lv, rv, l_pk, r_pk)
            y += sh
            if name != "vu":
                pygame.draw.line(surf, DIVIDER, (0, y), (w, y), 1)
    else:
        segs = [("info",14),("scope",18),("mirror",32),("liss",24),("vu",12)]
        x = 0
        for name, pct in segs:
            sw2 = max(8, int(w * pct / 100))
            s = surf.subsurface(pygame.Rect(x, 0, sw2, h)); s.fill(BG)
            if   name == "info":   draw_info(s, spotify, levels, sample_rate, energy, bass)
            elif name == "scope":  draw_scope(s, buf)
            elif name == "mirror": draw_mirror(s, levels, peaks, energy=energy)
            elif name == "liss":   draw_lissajous(s, vibe_liss_surf, stereo)
            elif name == "vu":     draw_vu(s, lv, rv, l_pk, r_pk)
            x += sw2
            if name != "vu":
                pygame.draw.line(surf, DIVIDER, (x, 0), (x, h), 1)


# ── Controls printout ──────────────────────────────────────────────────────────

def print_controls(device_name, orientation, w, h, spotify_ok):
    sp = "connected" if spotify_ok else "not connected (set SPOTIPY_CLIENT_ID/SECRET)"
    print(f"""
  +================================================+
  |  APE Music Visualizer v3                       |
  |  Device  : {device_name[:36]:<36s}|
  |  Layout  : {orientation:<6s}  {w}x{h:<22}        |
  |  Spotify : {sp[:38]:<38s}|
  +------------------------------------------------+
  |  SPACE   next visual mode                      |
  |  O       cycle orientation (right/top/bottom)  |
  |  Q       quit                                  |
  +------------------------------------------------+
  |  Modes:                                        |
  |   SCOPE       green phosphor oscilloscope      |
  |   BARS        frequency bars (monstercat)      |
  |   MIRROR      symmetric bars from center       |
  |   WATERFALL   scrolling spectrogram            |
  |   LISSAJOUS   L vs R stereo phase scope        |
  |   LUSH        bass-reactive combo + glow       |
  |   COMBO       oscilloscope + spectrum split    |
  |   MEGA        all panels + info card           |
  |   VU          stereo L/R level meters          |
  +================================================+
""")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    group  = parser.add_mutually_exclusive_group()
    group.add_argument("--top",    action="store_true")
    group.add_argument("--bottom", action="store_true")
    args = parser.parse_args()

    ctypes.windll.user32.SetProcessDPIAware()
    sw, sh = _screen_size()

    ori = "top" if args.top else ("bottom" if args.bottom else "right")
    ORIENTATIONS = ["right", "top", "bottom"]

    def open_window(orientation):
        _unregister_appbar()
        wx, wy, ww, wh, edge, rc = _geometry(orientation, sw, sh)
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{wx},{wy}"
        # Destroy and recreate display — set_mode alone won't resize a NOFRAME window
        pygame.display.quit()
        pygame.display.init()
        surf = pygame.display.set_mode((ww, wh), pygame.NOFRAME)
        pygame.display.set_caption("APE Visualizer")
        pygame.event.pump()
        pygame.time.delay(100)
        hwnd = pygame.display.get_wm_info()["window"]
        _apply_window(hwnd, wx, wy, ww, wh)
        _register_appbar(hwnd, edge, rc)
        pygame.time.delay(50)
        _apply_window(hwnd, wx, wy, ww, wh)
        return surf, ww, wh

    pygame.init()
    surf, ww, wh = open_window(ori)

    # Pre-build persistent surfaces
    def make_surfaces(ww, wh):
        wf  = pygame.Surface((ww, wh)).convert(); wf.fill((0,0,0))
        ls  = pygame.Surface((ww, wh)).convert(); ls.fill((0,0,0))
        horiz = ww > wh
        # mega lissajous panel (matches draw_mega liss pct 14/13%)
        mlw = int(ww * 0.13) if horiz else ww
        mlh = wh if horiz else int(wh * 0.14)
        mls = pygame.Surface((max(1,mlw), max(1,mlh))).convert(); mls.fill((0,0,0))
        # mega waterfall panel (matches draw_mega wfall pct 19/16%)
        mww = int(ww * 0.19) if horiz else ww
        mwh = wh if horiz else int(wh * 0.16)
        mwf = pygame.Surface((max(1,mww), max(1,mwh))).convert(); mwf.fill((0,0,0))
        return wf, ls, mls, mwf

    wf_surf, liss_surf, mega_liss_surf, mega_wf_surf = make_surfaces(ww, wh)
    thermal_lut        = _build_thermal_lut()

    def n_bars_for(ww, wh, ori):
        return max(24, min(80, (ww if ori != "right" else ww) // 5))

    n    = n_bars_for(ww, wh, ori)
    smth = BallisticSmoother(n)
    pks  = PeakHolder(n)

    cap = AudioCapture();  cap.start()
    time.sleep(0.4)

    spotify = SpotifyClient()
    time.sleep(0.1)

    print_controls(cap.device_name, ori, ww, wh,
                   spotify._active if hasattr(spotify, "_active") else False)

    clock     = pygame.time.Clock()
    mode_idx  = 0
    l_pk      = [0.0, 0.0];  r_pk = [0.0, 0.0]
    fps_real  = float(FPS)
    frame     = 0

    running = True
    while running:
        dt = clock.tick(FPS)
        fps_real = 0.9 * fps_real + 0.1 * max(1, 1000.0 / max(dt, 1))
        frame   += 1

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_q:
                    running = False
                elif ev.key == pygame.K_SPACE:
                    mode_idx = (mode_idx + 1) % len(MODES)
                elif ev.key == pygame.K_o:
                    ori = ORIENTATIONS[(ORIENTATIONS.index(ori) + 1) % 3]
                    surf, ww, wh = open_window(ori)
                    wf_surf, liss_surf, mega_liss_surf, mega_wf_surf = make_surfaces(ww, wh)
                    n = n_bars_for(ww, wh, ori)
                    smth.reset(n);  pks.reset(n)
                    print(f"  >> Orientation: {ori}  ({ww}x{wh})")

        buf    = cap.get_mono()
        stereo = cap.get_stereo()
        raw    = compute_spectrum(buf, cap.sample_rate, n)
        if MONSTERCAT:
            raw = monstercat(raw)
        levels = smth.update(raw, fps_real)

        energy = _bar_energy(levels)
        bass   = _bass_energy(levels, n)

        mode = MODES[mode_idx]
        surf.fill(BG)
        horiz = (ww > wh)

        lv, rv = cap.get_stereo_rms()
        draw_mega(surf, buf, levels, pks, energy, bass,
                  spotify, cap.sample_rate, mega_wf_surf, thermal_lut, stereo,
                  lv, rv, l_pk, r_pk, mega_liss_surf)

        pygame.display.flip()

    cap.stop()
    _unregister_appbar()
    pygame.quit()


if __name__ == "__main__":
    main()
