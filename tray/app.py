"""Tray app for the Technics EAH-AZ100: left-click opens the flyout, right-click opens the menu.

Run:  pythonw tray\\app.py            (normal)
      python  tray\\app.py --demo     (fake earbuds, to try the UI without hardware)
      python  tray\\app.py --show     (open the flyout straight away)
The Bluetooth work is tool/technics.py; this file is only the tray, the window and the bridge.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import sys
import threading
import time
import winreg

try:  # per-monitor DPI so window placement is in real pixels (must happen before the window exists)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import webview
import pystray
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tool"))
import technics as T  # noqa: E402
from movie import MovieMode  # noqa: E402

TITLE = "Technics AZ100 Flyout"
CSS_W, CSS_H, MARGIN = 344, 577, 12      # flyout size and gap from the taskbar, in CSS pixels
CLOSE_AFTER_HIDE_S = 30                      # drop the control channel this long after the flyout closes
RUN_KEY, RUN_NAME = r"Software\Microsoft\Windows\CurrentVersion\Run", "TechnicsAZ100Tray"

logging.basicConfig(filename=os.path.join(HERE, "app.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
user32 = ctypes.WinDLL("user32", use_last_error=True)
dwm = ctypes.WinDLL("dwmapi")
# Handles are pointer-sized: without these signatures ctypes truncates them to 32 bits and calls silently misfire.
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetDpiForWindow.argtypes = [wt.HWND]
user32.GetDpiForWindow.restype = wt.UINT
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
dwm.DwmExtendFrameIntoClientArea.argtypes = [wt.HWND, ctypes.c_void_p]
HWND_TOPMOST = wt.HWND(-1)
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.RegisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostThreadMessageW.restype = wt.BOOL
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.restype = wt.DWORD

SETTINGS_PATH = os.path.join(HERE, "settings.json")
DEFAULT_SETTINGS = {"cycle_hotkey": "ctrl+alt+e"}
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
HOTKEY_ID = 0x7A01
NOTIFY_SHOW_S = 1.5


def cycle_label(state, adaptive, sub):
    """Toast text for a hotkey cycle. It names Auto level and the Ambient sub-mode when known, and omits what it does
    not know instead of guessing. Attention pauses playback on the earbuds, so the toast says so."""
    mode = state[0]
    if mode == T.MODE_ANC:
        return "Noise cancelling, auto" if adaptive else "Noise cancelling %d" % state[1]
    if mode == T.MODE_AMBIENT:
        tail = {0: ", transparent", 1: ", attention (pauses audio)"}.get(sub, "")
        return "Ambient %d%s" % (state[2], tail)
    return "Off"
SUB_NAMES = {0: "transparent", 1: "attention"}
KEY_NAMES = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
             "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
             "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}


def parse_hotkey(text):
    """'ctrl+alt+e' -> (modifiers, vk, 'Ctrl+Alt+E'). Raises ValueError if it cannot be understood."""
    if not isinstance(text, str):
        raise ValueError("hotkey must be text")
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("need at least one modifier and a key")
    *mods, key = parts
    flags, shown = 0, []
    for m in mods:
        if m not in MOD_NAMES:
            raise ValueError("unknown modifier %r" % m)
        if not flags & MOD_NAMES[m]:
            flags |= MOD_NAMES[m]
            shown.append("Ctrl" if m == "control" else m.capitalize())
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key in KEY_NAMES:
        vk = KEY_NAMES[key]
    else:
        raise ValueError("unknown key %r" % key)
    return flags, vk, "+".join(shown + [key.upper() if len(key) <= 3 else key.capitalize()])


def load_settings():
    data = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update(loaded)
    except FileNotFoundError:
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SETTINGS, f, indent=2)
                f.write("\n")
        except OSError:
            logging.exception("could not create settings.json")
    except Exception:
        logging.exception("could not read settings.json; using defaults")
    return data


# ---------------------------------------------------------------- device backends
class DemoDevice:
    """In-memory stand-in so the UI can be exercised without earbuds."""

    def __init__(self):
        self.lock = threading.RLock()   # same shape as technics.Session.lock so the hotkey can hold it across a read-modify-write
        self.state = [T.MODE_ANC, 60, 40]
        self.fail = False
        self._adaptive, self._sub = False, 0

    def adaptive(self):
        time.sleep(0.1)
        return self._adaptive

    def ambient_sub(self):
        time.sleep(0.1)
        return self._sub

    def set_adaptive(self, on):
        time.sleep(0.15)
        self._adaptive = bool(on)
        return self._adaptive, True

    def set_ambient_sub(self, sub):
        time.sleep(0.15)
        self._sub = int(sub)
        return self._sub, True

    def set_buffer_mode(self, mode):
        time.sleep(0.15)
        return int(mode), True

    def snapshot(self):
        time.sleep(0.4)
        if self.fail:
            raise T.DeviceError("not_found", "The earbuds are not reachable. Connect them to this PC and try again.")
        return {"mode": self.state[0], "nc": self.state[1], "amb": self.state[2],
                "codec": {"codec": "LDAC", "code": 6, "rate_hz": 96000, "channels": "stereo"},
                "battery": {"left": 100, "case": 90, "right": 100},
                "adaptive": self._adaptive, "ambient_sub": self._sub}

    def outside(self):
        return tuple(self.state)

    def poll_state(self):
        time.sleep(0.1)
        return {"mode": self.state[0], "nc": self.state[1], "amb": self.state[2],
                "adaptive": self._adaptive, "ambient_sub": self._sub}

    def set_outside(self, mode, nc, amb):
        T.validate_outside(mode, nc, amb)
        time.sleep(0.15)
        self.state = [mode, nc, amb]
        return tuple(self.state), True

    def close(self):
        pass


class DemoMovie:
    """Fake Movie mode for --demo: walks the same phases without touching the driver or Bluetooth."""
    PHASES = ["Applying settings", "Disconnecting audio", "Reconnecting audio", "Waiting for Windows audio", "Setting earbud delay"]

    def __init__(self):
        self.on, self.t0, self.target = False, None, None

    def status(self):
        busy = self.t0 is not None and time.monotonic() - self.t0 < 10
        if self.t0 is not None and not busy:
            self.on, self.t0 = self.target == "movie", None
        el = time.monotonic() - self.t0 if busy else 0
        step = min(5, 1 + int(el / 2)) if busy else 0
        return {"supported": True, "active": self.on, "codec": "SBC" if self.on else "LDAC", "delay_ms": 150 if self.on else 350,
                "busy": busy, "target": self.target if busy else None, "phase": self.PHASES[step - 1] if busy else "",
                "step": step, "steps": 5, "elapsed_s": round(el, 1), "expected_s": 10, "error": None}

    def start(self, on):
        self.target, self.t0 = "movie" if on else "music", time.monotonic()
        return {"started": True}


def wrap(fn):
    """Run a device call and turn every failure into a plain {error: {kind, message}} for the page."""
    try:
        return fn()
    except T.DeviceError as exc:
        return {"error": {"kind": exc.kind, "message": str(exc)}}
    except T.WriteUnconfirmed:
        return {"error": {"kind": "unconfirmed", "message": "No reply from the earbuds. Not resent."}}
    except ValueError as exc:
        return {"error": {"kind": "invalid", "message": str(exc)}}
    except Exception:
        logging.exception("unexpected error")
        return {"error": {"kind": "closed", "message": "Unexpected error. Details are in app.log."}}


class Api:
    """Exposed to the page. Public methods only; everything goes through the one device session."""

    def __init__(self, app):
        self._app = app

    def snapshot(self):
        return self._app.note(wrap(lambda: self._app.device.snapshot()))

    def poll(self):
        return self._app.note(wrap(lambda: self._app.device.poll_state()))

    def set_adaptive(self, on):
        def run():
            if isinstance(on, bool) or (isinstance(on, int) and on in (0, 1)):
                flag = bool(on)
            else:
                raise ValueError("adaptive must be true or false")
            state, ok = self._app.device.set_adaptive(flag)
            return {"ok": bool(ok), "adaptive": bool(state)}
        return self._app.note(wrap(run))

    def set_ambient_sub(self, sub):
        def run():
            if isinstance(sub, bool) or not isinstance(sub, int) or sub not in (0, 1):
                raise ValueError("ambient sub-mode must be 0 or 1")
            state, ok = self._app.device.set_ambient_sub(sub)
            return {"ok": bool(ok), "ambient_sub": int(state)}
        return self._app.note(wrap(run))

    def apply(self, mode, nc, amb):
        def run():
            state, ok = self._app.device.set_outside(int(mode), int(nc), int(amb))
            return {"ok": bool(ok), "mode": state[0], "nc": state[1], "amb": state[2]}
        return self._app.note(wrap(run))

    def movie_status(self):
        return wrap(lambda: self._app.movie.status())

    def movie_set(self, on):
        return wrap(lambda: self._app.movie.start(on))

    def hide(self):
        self._app.hide()

    def hotkey(self):
        """Label of the global shortcut for the flyout to mention, or None when it is not registered."""
        return self._app.hotkey_label if self._app.hotkey_ok else None

    def hide_by_blur(self):
        self._app.hide(by_blur=True)


# ---------------------------------------------------------------- window placement and native styling
def _work_area():
    rect = wt.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return rect


def anchor(hwnd):
    """Bottom-right above the taskbar (the tray's usual spot); follows a top or left taskbar too."""
    scale = (user32.GetDpiForWindow(hwnd) or 96) / 96
    w, h, m = round(CSS_W * scale), round(CSS_H * scale), round(MARGIN * scale)
    wa = _work_area()
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    x = wa.right - w - m
    y = wa.bottom - h - m
    if wa.top > 0:        # taskbar on top
        y = wa.top + m
    elif wa.left > 0:     # taskbar on the left
        x, y = wa.left + m, wa.bottom - h - m
    return x, y, w, h


def style_window(hwnd):
    """Rounded corners and a DWM shadow on the frameless window; no taskbar button."""
    try:
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)  # DWMWA_WINDOW_CORNER_PREFERENCE = round
        margins = (ctypes.c_int * 4)(1, 1, 1, 1)
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))          # gives a borderless window its shadow
        ex = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, (ex | 0x80) & ~0x40000)               # tool window: out of Alt+Tab and taskbar
    except Exception:
        logging.exception("styling failed")


# ---------------------------------------------------------------- tray icon
def taskbar_is_light():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def make_icon(light, dim=False):
    """The audiogram marks for left and right (X and O), in the taskbar's own contrast colour."""
    base = (20, 24, 31) if light else (255, 255, 255)
    col = base + (110 if dim else 255,)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.line([(3, 18), (30, 46)], fill=col, width=8)      # heavy strokes so both marks hold at 16 px
    d.line([(30, 18), (3, 46)], fill=col, width=8)
    d.ellipse([34, 17, 61, 47], outline=col, width=8)
    return img


def apps_use_light():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
    except OSError:
        return True


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            exe = sys.executable
            pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, '"%s" "%s"' % (pyw if os.path.exists(pyw) else exe, os.path.abspath(__file__)))
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


# ---------------------------------------------------------------- the app
class App:
    def __init__(self, demo, addr):
        # TECHNICS_WIRE_LOG=1 writes every raw frame to tray\wire.log (debugging only)
        wire = open(os.path.join(HERE, "wire.log"), "a", encoding="ascii") if os.environ.get("TECHNICS_WIRE_LOG") else None
        self.device = DemoDevice() if demo else T.Session(addr=addr, log=wire)
        self.movie = DemoMovie() if demo else MovieMode(self.device)
        self.window = None
        self.icon = None
        self.visible = False
        self.styled = False
        self.last_blur_hide = 0.0
        self.close_timer = None
        self.light = taskbar_is_light()
        self.connected = None
        self.tip = "Technics EAH-AZ100"
        self.state = None
        self.adaptive = None
        self.sub = None
        self.hotkey_ok = None          # None = not tried, True = registered, False = unavailable
        self.hotkey_label = None
        self.hotkey_tid = None
        self.cycling = threading.Lock()
        self.unreach_notified = False

    def note(self, res):
        """Keep the tray icon and tooltip in step with what the earbuds last said."""
        try:
            if "error" in res:
                self.connected = False
            else:
                self.connected = True
                if "mode" in res:
                    self.state = (res["mode"], res["nc"], res["amb"])
                if res.get("adaptive") is not None:
                    self.adaptive = res["adaptive"]
                if res.get("ambient_sub") is not None:
                    self.sub = res["ambient_sub"]
            self.build_tip()
            self.refresh_icon()
        except Exception:
            logging.exception("tray update failed")
        return res

    def build_tip(self):
        if self.connected is False or self.state is None:
            tip = "EAH-AZ100: not connected"
        else:
            mode, nc, amb = self.state
            name = T.MODE_NAMES.get(mode, "?")
            if mode == T.MODE_ANC and self.adaptive:
                tip = "EAH-AZ100: %s, auto" % name.capitalize()
            elif mode == T.MODE_AMBIENT and self.sub in SUB_NAMES:
                tip = "EAH-AZ100: %s, %s" % (name.capitalize(), SUB_NAMES[self.sub])
            else:
                level = {T.MODE_ANC: nc, T.MODE_AMBIENT: amb}.get(mode)
                tip = "EAH-AZ100: %s%s" % (name, " %d" % level if level else "")
        if self.hotkey_ok is False:
            tip += "\nShortcut unavailable"
        self.tip = tip

    def refresh_icon(self):
        if self.icon:
            self.icon.icon = make_icon(self.light, dim=self.connected is False)
            self.icon.title = self.tip

    def watch_taskbar_theme(self):
        while True:
            time.sleep(10)
            light = taskbar_is_light()
            if light != self.light:
                self.light = light
                self.refresh_icon()

    def hwnd(self):
        return user32.FindWindowW(None, TITLE)

    def show(self):
        if self.close_timer:
            self.close_timer.cancel()
        hwnd = self.hwnd()
        if not hwnd:
            return
        if not self.styled:
            style_window(hwnd)
            self.styled = True
        x, y, w, h = anchor(hwnd)
        self.window.show()
        ok = user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, 0x0040)  # SWP_SHOWWINDOW
        logging.info("placed at %s,%s size %sx%s ok=%s err=%s", x, y, w, h, ok, ctypes.get_last_error())
        user32.SetForegroundWindow(hwnd)
        self.visible = True
        self.window.evaluate_js("window.__onShow && window.__onShow()")

    def hide(self, by_blur=False):
        if not self.visible:
            return
        self.visible = False
        if by_blur:
            self.last_blur_hide = time.monotonic()
        try:
            self.window.evaluate_js("window.__onHide && window.__onHide()")
        except Exception:
            pass
        self.window.hide()
        self.schedule_close()

    def schedule_close(self):
        """Release the earbuds' control channel a little after the last use (flyout hidden or hotkey cycle done)."""
        if self.close_timer:
            self.close_timer.cancel()
        self.close_timer = threading.Timer(CLOSE_AFTER_HIDE_S, self._close_if_idle)
        self.close_timer.daemon = True
        self.close_timer.start()

    def _close_if_idle(self):
        if not self.visible and not self.cycling.locked():
            self.device.close()

    def toggle(self, *_):
        if self.visible:
            self.hide()
        elif time.monotonic() - self.last_blur_hide > 0.35:  # the click that just closed it by blur must not reopen it
            self.show()

    def quit(self, *_):
        logging.info("quit")
        self.stop_hotkey()
        try:
            self.device.close()
        finally:
            if self.icon:
                self.icon.stop()
            self.window.destroy()

    # ---- global hotkey: cycles Off -> Noise cancelling -> Ambient -> Off
    def start_hotkey(self):
        text = load_settings().get("cycle_hotkey")
        try:
            mods, vk, label = parse_hotkey(text)
        except ValueError as exc:
            logging.warning("cycle_hotkey %r unusable: %s", text, exc)
            self.hotkey_ok = False
            self.build_tip()
            self.refresh_icon()
            return
        self.hotkey_label = label
        ready = threading.Event()
        threading.Thread(target=self.hotkey_loop, args=(mods, vk, label, ready), daemon=True).start()
        ready.wait(3)

    def hotkey_loop(self, mods, vk, label, ready):
        try:
            if not user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
                logging.warning("hotkey %s unavailable (RegisterHotKey error %s)", label, ctypes.get_last_error())
                self.hotkey_ok = False
                self.build_tip()
                self.refresh_icon()
                return
            self.hotkey_tid = kernel32.GetCurrentThreadId()
            self.hotkey_ok = True
            logging.info("hotkey %s registered", label)
        finally:
            ready.set()
        msg = wt.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    logging.info("hotkey pressed")
                    threading.Thread(target=self.cycle, daemon=True).start()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop_hotkey(self):
        if self.hotkey_tid:
            user32.PostThreadMessageW(self.hotkey_tid, WM_QUIT, 0, 0)

    def cycle(self):
        if not self.cycling.acquire(blocking=False):   # one cycle at a time, no backlog
            logging.info("hotkey ignored: a cycle is still running")
            return
        try:
            # read-modify-write under ONE lock hold: a slider write from the flyout cannot slip in between the read
            # and the write and then be overwritten with the old levels
            with self.device.lock:
                mode, nc, amb = self.device.outside()
                nxt = {T.MODE_OFF: T.MODE_ANC, T.MODE_ANC: T.MODE_AMBIENT}.get(mode, T.MODE_OFF)
                state, ok = self.device.set_outside(nxt, nc, amb)
                try:
                    extra = self.device.poll_state()     # Auto level and sub-mode, so the toast tells the whole truth
                except Exception:
                    extra = {}
            logging.info("cycle %s -> %s ok=%s", (mode, nc, amb), tuple(state), ok)
            adaptive, sub = extra.get("adaptive"), extra.get("ambient_sub")
            self.note({"mode": state[0], "nc": state[1], "amb": state[2], "adaptive": adaptive, "ambient_sub": sub})
            self.toast(cycle_label(state, adaptive, sub))
            logging.info("cycle: toast sent")
            if self.visible:
                try:
                    self.window.evaluate_js("window.__onRemote && window.__onRemote()")
                except Exception:
                    logging.exception("remote refresh failed")
                logging.info("cycle: remote refresh done")
        except (T.DeviceError, T.WriteUnconfirmed, OSError) as exc:
            logging.info("cycle failed: %s", exc)
            self.note({"error": {"kind": getattr(exc, "kind", "closed"), "message": str(exc)}})
            self.toast("Earbuds not reachable")     # every failed press says so: silence would look like a dead key
        except Exception:
            logging.exception("cycle failed")
        finally:
            self.cycling.release()
            if not self.visible:       # hotkey-only use: do not hold the control channel open forever
                self.schedule_close()

    def toast(self, message):
        """Quiet feedback: a tray notification that is taken back after a moment (best effort)."""
        try:
            if not self.icon:
                return
            self.icon.notify(message, "EAH-AZ100")
            t = threading.Timer(NOTIFY_SHOW_S, self._unnotify)
            t.daemon = True
            t.start()
        except Exception:
            logging.exception("notify failed")

    def _unnotify(self):
        try:
            self.icon.remove_notification()
        except Exception:
            pass

    def on_started(self, show_now):
        """Runs on a worker thread once the (hidden) window exists."""
        menu = pystray.Menu(
            pystray.MenuItem("Open", self.toggle, default=True),
            pystray.MenuItem("Start with Windows", lambda *_: set_autostart(not autostart_enabled()),
                             checked=lambda *_: autostart_enabled()),
            pystray.MenuItem(lambda _: "Cycle mode: %s" % (self.hotkey_label if self.hotkey_ok else "unavailable"),
                             None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.quit),
        )
        self.icon = pystray.Icon("technics-az100", make_icon(self.light), self.tip, menu)
        threading.Thread(target=self.watch_taskbar_theme, daemon=True).start()
        self.start_hotkey()
        if show_now:
            threading.Timer(1.0, self.show).start()
        self.icon.run()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fake earbuds for trying the UI")
    ap.add_argument("--show", action="store_true", help="open the flyout at start")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--addr", default=T.DEFAULT_ADDR)
    args = ap.parse_args()

    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\TechnicsAZ100TrayApp")
    if ctypes.windll.kernel32.GetLastError() == 183 and not args.demo:  # ERROR_ALREADY_EXISTS
        return 0

    app = App(args.demo, args.addr)
    app.window = webview.create_window(
        TITLE, os.path.join(HERE, "ui", "index.html"), js_api=Api(app), width=CSS_W, height=CSS_H,
        frameless=True, easy_drag=False, resizable=False, on_top=True, hidden=True, shadow=True,
        text_select=False, background_color="#fbfbfa" if apps_use_light() else "#15181d")  # no white flash in dark mode
    logging.info("start demo=%s", args.demo)
    webview.start(app.on_started, args.show, debug=args.debug)
    del mutex
    return 0


if __name__ == "__main__":
    sys.exit(main())
