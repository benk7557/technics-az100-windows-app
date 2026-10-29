"""Movie mode: lower-latency Bluetooth audio for video, one switch.

Movie  = driver codec LDAC -> SBC (via the elevated helper task), reconnect the audio profile, earbuds "Reduce sound delay" = Video.
Music  = the saved driver settings are restored, reconnect, earbuds delay mode back to Auto.
The elevated part is only ever the two fixed scheduled tasks installed by admin\\setup_movie_helper.ps1; this file starts them by
name and reads the driver's registry (read-only). It never writes the registry itself.
"""
import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
import winreg

DRV = r"SYSTEM\CurrentControlSet\Services\AltA2DP\Parameters\Devices"
DATA_DIR = r"C:\ProgramData\TechnicsAZ100Tool"
RESULT = os.path.join(DATA_DIR, "last_result.json")
DEVICE_FILE = os.path.join(DATA_DIR, "device.txt")   # the earbuds the helper was set up for (written by the setup script)
TASKS = {"movie": "TechnicsAZ100-MovieMode-Movie", "music": "TechnicsAZ100-MovieMode-Music"}
CODEC_NAMES = {1: "SBC", 2: "AAC", 4: "LDAC"}
A2DP_SINK = uuid.UUID("0000110B-0000-1000-8000-00805F9B34FB")
EXPECTED_S = 40
STEPS = 5
NO_WINDOW = 0x08000000


class SYSTEMTIME(ctypes.Structure):
    _fields_ = [(n, wt.WORD) for n in ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMilliseconds")]


class BTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("Address", ctypes.c_ulonglong), ("ulClassofDevice", wt.ULONG), ("fConnected", wt.BOOL),
                ("fRemembered", wt.BOOL), ("fAuthenticated", wt.BOOL), ("stLastSeen", SYSTEMTIME), ("stLastUsed", SYSTEMTIME),
                ("szName", wt.WCHAR * 248)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _setup_addr():
    """XX:XX:XX:XX:XX:XX from DEVICE_FILE, or None before setup."""
    try:
        with open(DEVICE_FILE, encoding="ascii") as f:
            addr = f.read().strip().upper()
    except (OSError, UnicodeDecodeError):
        return None
    return addr if re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", addr) else None


def _reg(addr, branch, name):
    dev = "0000" + addr.replace(":", "").lower()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{DRV}\\{branch}\\{dev}") as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _set_a2dp(addr, enable):
    """Enable or disable the A2DP sink service of the earbuds (BluetoothSetServiceState); returns the Win32 result code."""
    bth = ctypes.WinDLL("bthprops.cpl", use_last_error=True)
    bth.BluetoothSetServiceState.argtypes = [wt.HANDLE, ctypes.POINTER(BTH_DEVICE_INFO), ctypes.POINTER(GUID), wt.DWORD]
    bth.BluetoothSetServiceState.restype = wt.DWORD
    info = BTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(info)
    info.Address = int(addr.replace(":", ""), 16)
    guid = GUID(A2DP_SINK.time_low, A2DP_SINK.time_mid, A2DP_SINK.time_hi_version, (ctypes.c_ubyte * 8)(*A2DP_SINK.bytes[8:]))
    return bth.BluetoothSetServiceState(None, ctypes.byref(info), ctypes.byref(guid), 1 if enable else 0)


def _endpoint_active():
    """True when a Technics render endpoint is active (DeviceState low byte 1; stale ghosts stay 4/8)."""
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    return False
                i += 1
                try:
                    with winreg.OpenKey(root, sub + r"\Properties") as p:
                        names = []
                        for n in ("{a45c254e-df1c-4efd-8020-67d146a850e0},2", "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"):
                            try:
                                names.append(str(winreg.QueryValueEx(p, n)[0]))
                            except OSError:
                                pass
                    if any("Technics" in n or "AZ100" in n for n in names):
                        with winreg.OpenKey(root, sub) as e:
                            if winreg.QueryValueEx(e, "DeviceState")[0] & 0xFF == 1:
                                return True
                except OSError:
                    continue
    except OSError:
        return False


def _task_exists(name):
    r = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True, creationflags=NO_WINDOW)
    return r.returncode == 0


class MovieMode:
    """One instance per tray app. `device` is the app's earbuds session (has .lock and .set_buffer_mode)."""

    def __init__(self, device):
        self.device = device
        self._lock = threading.Lock()
        self._busy = False
        self._target = None
        self._phase = ""
        self._step = 0
        self._t0 = 0.0
        self._error = None
        self._supported = None
        self._addr = None

    def _supported_now(self):
        if self._supported is None or not self._supported:       # re-check while unsupported so setup takes effect at once
            self._addr = _setup_addr()
            self._supported = bool(self._addr) and all(_task_exists(t) for t in TASKS.values())
        return self._supported

    def status(self):
        with self._lock:
            busy, target, phase, step, t0, err = self._busy, self._target, self._phase, self._step, self._t0, self._error
        supported = self._supported_now()
        cur = nxt = delay = None
        if supported:
            cur = _reg(self._addr, "Current", "Codec")
            nxt = _reg(self._addr, "Next", "Codec")
            delay = _reg(self._addr, "Current", "Delay")
        codec = CODEC_NAMES.get(cur) if cur else None
        live = cur if (cur and _reg(self._addr, "Current", "Opened") == 1) else nxt   # what is actually playing wins over what is saved
        active = None if live is None else (live == 1)
        return {"supported": supported, "active": active, "codec": codec,
                "delay_ms": round(delay / 10) if delay else None, "busy": busy, "target": target, "phase": phase,
                "step": step, "steps": STEPS, "elapsed_s": round(time.monotonic() - t0, 1) if busy else 0,
                "expected_s": EXPECTED_S, "error": err}

    def start(self, on):
        if not isinstance(on, bool):
            return {"error": {"kind": "invalid", "message": "movie mode must be true or false"}}
        if not self._supported_now():
            return {"error": {"kind": "closed", "message": "Movie mode is not set up yet."}}
        with self._lock:
            if self._busy:
                return {"error": {"kind": "busy", "message": "Movie mode is already switching."}}
            self._busy, self._target, self._error = True, "movie" if on else "music", None
            self._phase, self._step, self._t0 = "Starting", 0, time.monotonic()
        threading.Thread(target=self._run, args=(on,), daemon=True, name="movie-mode").start()
        return {"started": True}

    def _progress(self, step, phase):
        with self._lock:
            self._step, self._phase = step, phase

    def _run(self, on):
        profile = "movie" if on else "music"
        reenable_needed = False
        try:
            self._progress(1, "Applying settings")
            self._run_task(profile)
            self._progress(2, "Disconnecting audio")
            reenable_needed = True
            if _set_a2dp(self._addr, False) != 0:
                raise RuntimeError("Windows would not release the audio connection.")
            time.sleep(0.3)
            self._progress(3, "Reconnecting audio")
            for attempt in range(6):                               # Windows sometimes refuses once; one refusal must not strand the earbuds
                rc = _set_a2dp(self._addr, True)
                if rc == 0:
                    break
                logging.warning("A2DP enable refused (rc=%s), attempt %d", rc, attempt + 1)
                time.sleep(2)
            else:
                raise RuntimeError("Windows would not restore the audio connection. Reconnect the earbuds in Bluetooth settings.")
            reenable_needed = False
            self._progress(4, "Waiting for Windows audio")
            self._wait_audio()
            self._progress(5, "Setting earbud delay")
            try:
                self.device.set_buffer_mode(2 if on else 0)
            except Exception as exc:                               # the audio already switched; say so rather than fail it
                logging.warning("buffer mode not set: %s", exc)
                with self._lock:
                    self._error = "Audio switched, but the earbuds' delay setting could not be changed."
        except Exception as exc:
            logging.exception("movie mode failed")
            with self._lock:
                self._error = str(exc)
        finally:
            if reenable_needed:                                    # never leave the earbuds' audio profile disabled
                for _ in range(6):
                    if _set_a2dp(self._addr, True) == 0:
                        break
                    time.sleep(2)
            with self._lock:
                self._busy, self._phase = False, ""

    def _run_task(self, profile):
        before = os.path.getmtime(RESULT) if os.path.exists(RESULT) else 0
        r = subprocess.run(["schtasks", "/Run", "/TN", TASKS[profile]], capture_output=True, creationflags=NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError("Could not start the settings helper.")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if os.path.exists(RESULT) and os.path.getmtime(RESULT) > before:
                with open(RESULT, encoding="utf-8-sig") as f:
                    res = json.load(f)
                if not res.get("ok"):
                    raise RuntimeError("Settings helper refused: " + str(res.get("message", "unknown reason")))
                return
            time.sleep(0.25)
        raise RuntimeError("The settings helper did not finish in time.")

    def _wait_audio(self):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if _reg(self._addr, "Current", "Opened") == 1 and _endpoint_active():
                time.sleep(0.5)
                return
            time.sleep(0.25)
        raise RuntimeError("The audio device did not come back within 60 s.")
