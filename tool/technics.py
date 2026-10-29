"""Control tool for Technics EAH-AZ100 earbuds over classic Bluetooth (RFCOMM). No phone app needed.

Protocol facts come from the decompiled Technics Audio Connect app and were confirmed against an HCI
capture of the real app and live reads (see ../docs/PROTOCOL.md).

Safety design:
  * Reads are limited to READ_IDS. The ONLY writes that exist are SET_OUTSIDE_CTRL (id 11, ANC / ambient
    mode + levels), SET_ADAPTIVE_ANC (id 104, payload 00/01) and SET_AMBIENT_MODE (id 34, payload 0000 =
    Transparent / 0101 = Attention) and SET_BUFFER_MODE (id 59, payload 00 Auto / 01 Music / 02 Video, the
    "Suppression of sound interruptions and delays" setting). There is no raw-bytes passthrough, so firmware, reset, power-off,
    key-map, LE Audio and codec commands cannot be sent by this program.
  * Every command waits for the earbuds' reply (6 s timeout). Writes are never retried.
  * Writes (of any kind) are spaced at least 0.5 s apart (the app's own slider throttle) and are read back
    and verified.
  * Nothing is written unless you pass --yes. The state before the first id-11 write is saved to baseline.json.

Usage:
    python technics.py selftest
    python technics.py status                 # model, ANC/ambient state, connection option, codec (read-only)
    python technics.py codec                  # is the stream LDAC? (read-only)
    python technics.py probe [--dry-run]      # raw read-only dump of the five reads
    python technics.py anc 60 [--yes]         # noise cancelling at level 60 (1-100), ambient level kept
    python technics.py ambient 70 [--yes]     # ambient sound at level 70, noise-cancel level kept
    python technics.py off [--yes]            # ANC and ambient off, levels kept
    python technics.py adaptive on|off [--yes]               # "Auto level" (adaptive ANC)
    python technics.py ambient-mode transparent|attention [--yes]   # sub-mode of Ambient
    python technics.py buffer-mode auto|music|video [--yes]  # live stream mode: Auto / suppress interruptions / reduce delay
    python technics.py noop --yes             # write back exactly what the earbuds report (sanity check)
    python technics.py restore [--previous] --yes
Common options: --addr XX:XX:XX:XX:XX:XX   --channel N (skip SDP)   --log FILE (raw TX/RX hex)
The earbuds are found by name among the PC's paired Bluetooth devices; --addr (or TECHNICS_ADDR) overrides that.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import socket
import sys
import time
import uuid
import winreg

# Airoha SPP service the app uses on the AZ100 (UuidTable.AIROHA_SPP_UUID in the decompiled app)
SPP_UUID = "00000000-0000-0000-0099-AABBCCDDEEFF"
DEFAULT_ADDR = os.environ.get("TECHNICS_ADDR")  # None: look the earbuds up among the paired devices
PAIRED_KEY = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"
PAIRED_NAME = "EAH-AZ100"
HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_FILE = os.path.join(HERE, "baseline.json")
PREVIOUS_FILE = os.path.join(HERE, "previous.json")

CMD_NEED_RESP, RESPONSE, INDICATION = 0x5A, 0x5B, 0x5D
TIMEOUT_S = 6.0  # the app's own response timeout
MIN_WRITE_GAP_S = 0.5  # the app throttles slider writes to one per 500 ms
IND_WAIT_S = 1.0  # the slower bud's battery indication arrived ~150 ms after its reply in the capture

GET_MODEL_ID, GET_OUTSIDE_CTRL, GET_A2DP_OPTION, GET_CODEC_INFO, GET_LE_AUDIO = 0, 10, 16, 18, 89
SET_OUTSIDE_CTRL = 11
GET_AMBIENT_MODE, SET_AMBIENT_MODE = 33, 34
GET_ADAPTIVE_ANC, SET_ADAPTIVE_ANC = 103, 104
SUB_TRANSPARENT, SUB_ATTENTION = 0, 1
# "Live stream mode" (MusicVideoBuffer in the app): GET has no payload, SET has one byte. The GET reply carries
# the value in the RSP itself (StageGetMusicVideoBuffer reads frame byte 7).
GET_BUFFER_MODE, SET_BUFFER_MODE = 58, 59
BUFFER_AUTO, BUFFER_MUSIC, BUFFER_VIDEO = 0, 1, 2
BUFFER_NAMES = {BUFFER_AUTO: "Auto", BUFFER_MUSIC: "Music (suppress interruptions)", BUFFER_VIDEO: "Video (reduce delay)"}
# The only payloads the app ever sends for id 34: (ambientMode, musicMode)
AMBIENT_SUB_PAYLOADS = {SUB_TRANSPARENT: b"\x00\x00", SUB_ATTENTION: b"\x01\x01"}
AMBIENT_SUB_NAMES = {SUB_TRANSPARENT: "Transparent", SUB_ATTENTION: "Attention"}
GET_CRADLE_BATTERY, GET_AGENT_CHANNEL, GET_EARBUD_BATTERY = 64, 3284, 3286  # the app polls all three itself
READ_IDS = {
    GET_MODEL_ID: "GET_MODEL_ID",
    GET_OUTSIDE_CTRL: "GET_OUTSIDE_CTRL",
    GET_AMBIENT_MODE: "GET_AMBIENT_MODE",
    GET_ADAPTIVE_ANC: "GET_ADAPTIVE_ANC",
    GET_A2DP_OPTION: "GET_A2DP_OPTION",
    GET_CODEC_INFO: "GET_CODEC_INFO",
    GET_LE_AUDIO: "GET_LE_AUDIO",
    GET_CRADLE_BATTERY: "GET_CRADLE_BATTERY",
    GET_AGENT_CHANNEL: "GET_AGENT_CHANNEL",
    GET_EARBUD_BATTERY: "GET_EARBUD_BATTERY",
    GET_BUFFER_MODE: "GET_BUFFER_MODE",
}
# The only payloads each read may carry. 3286 takes a role byte: 0 = agent bud, 1 = partner bud.
READ_PAYLOADS = {rid: {b""} for rid in READ_IDS}
READ_PAYLOADS[GET_EARBUD_BATTERY] = {b"\x00", b"\x01"}
AZ100_MODEL_IDS = range(0xA0, 0xA4)  # AZ100, AZ100E, AZ100G, AZ100P

MODE_OFF, MODE_ANC, MODE_AMBIENT = 0, 1, 2
MODE_NAMES = {MODE_OFF: "off", MODE_ANC: "noise cancelling", MODE_AMBIENT: "ambient"}
A2DP_OPTION_NAMES = {0: "connection priority (SBC)", 1: "sound quality priority (AAC)",
                     16: "connection priority, LDAC allowed", 17: "LDAC priority"}
CODEC_NAMES = {0: "unknown", 1: "SBC", 2: "AAC", 3: "aptX", 4: "aptX LL", 5: "aptX HD", 6: "LDAC",
               32: "USB audio", 33: "analog"}
SAMPLE_RATES = {0: None, 1: 8000, 2: 11025, 3: 12000, 4: 16000, 5: 22050, 6: 24000, 7: 32000, 8: 44100,
                9: 48000, 10: 64000, 11: 88200, 12: 96000}
CHANNEL_MODES = {0: "unknown", 1: "mono", 2: "dual channel", 3: "stereo"}


# ---------------------------------------------------------------- framing
def build_frame(race_id, payload=b"", ptype=CMD_NEED_RESP):
    """05 | type | len LE16 (= 2 + payload) | id LE16 | payload"""
    return bytes([0x05, ptype]) + (2 + len(payload)).to_bytes(2, "little") + race_id.to_bytes(2, "little") + payload


def build_read(race_id, payload=b""):
    if race_id not in READ_IDS:
        raise ValueError("refusing to send id %d: not on the read-only allowlist" % race_id)
    if bytes(payload) not in READ_PAYLOADS[race_id]:
        raise ValueError("refusing payload %s for read id %d" % (bytes(payload).hex(), race_id))
    return build_frame(race_id, bytes(payload))


def validate_outside(mode, nc, amb):
    for name, v in (("mode", mode), ("noise-cancel level", nc), ("ambient level", amb)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("%s must be an integer" % name)
    if mode not in MODE_NAMES:
        raise ValueError("mode must be 0 (off), 1 (noise cancelling) or 2 (ambient), got %r" % mode)
    for name, v in (("noise-cancel level", nc), ("ambient level", amb)):
        if not 1 <= v <= 100:
            raise ValueError("%s must be 1-100 (the app maps 0 to 1), got %r" % (name, v))


def build_set_outside(mode, nc, amb):
    """The only write frame this program can build: id 11, payload [mode, nc level, ambient level]."""
    validate_outside(mode, nc, amb)
    return build_frame(SET_OUTSIDE_CTRL, bytes([mode, nc, amb]))


def validate_adaptive(on):
    if on is not True and on is not False and not (type(on) is int and on in (0, 1)):
        raise ValueError("adaptive ANC must be True/False or 0/1, got %r" % (on,))
    return bool(on)


def validate_ambient_sub(sub):
    if isinstance(sub, bool) or not isinstance(sub, int):
        raise ValueError("ambient sub-mode must be an integer")
    if sub not in AMBIENT_SUB_PAYLOADS:
        raise ValueError("ambient sub-mode must be 0 (Transparent) or 1 (Attention), got %r" % (sub,))
    return sub


def validate_ambient_payload(payload):
    """Only the two payloads the official app ever sends for id 34."""
    if bytes(payload) not in AMBIENT_SUB_PAYLOADS.values():
        raise ValueError("refusing payload %s for id 34 (only 0000 and 0101 are allowed)" % bytes(payload).hex())
    return bytes(payload)


def build_set_adaptive(on):
    """Write frame: id 104, payload 01 (on) or 00 (off)."""
    return build_frame(SET_ADAPTIVE_ANC, bytes([1 if validate_adaptive(on) else 0]))


def build_set_ambient_sub(sub):
    """Write frame: id 34, payload 0000 (Transparent) or 0101 (Attention)."""
    return build_frame(SET_AMBIENT_MODE, validate_ambient_payload(AMBIENT_SUB_PAYLOADS[validate_ambient_sub(sub)]))


def validate_buffer_mode(v):
    if type(v) is not int:
        raise ValueError("buffer mode must be an integer, got %r" % (v,))
    if v not in BUFFER_NAMES:
        raise ValueError("buffer mode must be 0 (Auto), 1 (Music) or 2 (Video), got %r" % (v,))
    return v


def build_set_buffer_mode(v):
    """Write frame: id 59, one payload byte 0 (Auto) / 1 (Music) / 2 (Video)."""
    return build_frame(SET_BUFFER_MODE, bytes([validate_buffer_mode(v)]))


class Parser:
    """Reassembles frames from an RFCOMM byte stream (frames may be split or merged)."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        frames = []
        i = 0
        while i + 6 <= len(self.buf):
            if self.buf[i] in (0x05, 0x15) and self.buf[i + 1] in (0x5A, 0x5B, 0x5C, 0x5D):
                length = self.buf[i + 2] | (self.buf[i + 3] << 8)
                end = i + 4 + length
                if length >= 2 and length < 2000:
                    if end > len(self.buf):
                        break  # wait for the rest
                    rid = self.buf[i + 4] | (self.buf[i + 5] << 8)
                    frames.append((self.buf[i + 1], rid, bytes(self.buf[i + 6:end])))
                    i = end
                    continue
            i += 1
        del self.buf[:i]
        return frames


# ---------------------------------------------------------------- Winsock SDP lookup (no driver needed)
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]


class CSADDR_INFO(ctypes.Structure):
    _fields_ = [("LocalAddr", SOCKET_ADDRESS), ("RemoteAddr", SOCKET_ADDRESS),
                ("iSocketType", ctypes.c_int), ("iProtocol", ctypes.c_int)]


class WSAQUERYSETW(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("lpszServiceInstanceName", ctypes.c_wchar_p),
                ("lpServiceClassId", ctypes.POINTER(GUID)), ("lpVersion", ctypes.c_void_p),
                ("lpszComment", ctypes.c_wchar_p), ("dwNameSpace", wt.DWORD),
                ("lpNSProviderId", ctypes.c_void_p), ("lpszContext", ctypes.c_wchar_p),
                ("dwNumberOfProtocols", wt.DWORD), ("lpafpProtocols", ctypes.c_void_p),
                ("lpszQueryString", ctypes.c_wchar_p), ("dwNumberOfCsAddrs", wt.DWORD),
                ("lpcsaBuffer", ctypes.POINTER(CSADDR_INFO)), ("dwOutputFlags", wt.DWORD),
                ("lpBlob", ctypes.c_void_p)]


class SOCKADDR_BTH(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("addressFamily", ctypes.c_ushort), ("btAddr", ctypes.c_ulonglong),
                ("serviceClassId", GUID), ("port", ctypes.c_ulong)]


def _guid(text):
    u = uuid.UUID(text)
    return GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


def _bt_addr_int(addr):
    return int(addr.replace(":", ""), 16)


def find_paired_az100():
    """Address (XX:XX:...) of the one paired device whose Bluetooth name contains EAH-AZ100."""
    found = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PAIRED_KEY) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        name = bytes(winreg.QueryValueEx(k, "Name")[0]).rstrip(b"\0").decode("utf-8", "replace")
                except OSError:
                    continue
                if PAIRED_NAME in name and len(sub) == 12:
                    found.append(":".join(sub[j:j + 2] for j in range(0, 12, 2)).upper())
    except OSError:
        pass
    if len(found) != 1:
        raise OSError("%s paired AZ100 earbuds found; pass --addr" % ("no" if not found else "more than one"))
    return found[0]


def resolve_addr(addr):
    return addr.upper() if addr else find_paired_az100()


def find_rfcomm_channel(addr, service_uuid=SPP_UUID):
    """Ask Windows' SDP client which RFCOMM channel the device offers for service_uuid."""
    ws2 = ctypes.WinDLL("ws2_32", use_last_error=True)
    NS_BTH, LUP_RETURN_ALL = 16, 0x0FF0
    guid = _guid(service_uuid)
    handle = wt.HANDLE()
    ws2.WSALookupServiceBeginW.argtypes = [ctypes.POINTER(WSAQUERYSETW), wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    ws2.WSALookupServiceNextW.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    ws2.WSALookupServiceEnd.argtypes = [wt.HANDLE]
    # Verified on Windows 11: the device address must be written "(XX:XX:XX:XX:XX:XX)" and Begin must get
    # no flags (LUP_FLUSHCACHE is rejected with WSAEINVAL for this query); LUP_RETURN_ALL goes on Next.
    qs = WSAQUERYSETW()
    qs.dwSize = ctypes.sizeof(qs)
    qs.dwNameSpace = NS_BTH
    qs.lpServiceClassId = ctypes.pointer(guid)
    qs.lpszContext = "(%s)" % addr
    if ws2.WSALookupServiceBeginW(ctypes.byref(qs), 0, ctypes.byref(handle)) != 0:
        raise OSError("SDP lookup could not start (WSA error %d). Is the earbud connected?" % ctypes.get_last_error())
    try:
        size = wt.DWORD(4096)
        buf = ctypes.create_string_buffer(size.value)
        if ws2.WSALookupServiceNextW(handle, LUP_RETURN_ALL, ctypes.byref(size), buf) != 0:
            raise OSError("service %s not found on %s (WSA error %d)" % (service_uuid, addr, ctypes.get_last_error()))
        out = ctypes.cast(buf, ctypes.POINTER(WSAQUERYSETW)).contents
        if out.dwNumberOfCsAddrs < 1:
            raise OSError("SDP answered without an address record")
        sa = ctypes.cast(out.lpcsaBuffer[0].RemoteAddr.lpSockaddr, ctypes.POINTER(SOCKADDR_BTH)).contents
        if not sa.port:
            raise OSError("SDP record has no RFCOMM channel")
        return int(sa.port)
    finally:
        ws2.WSALookupServiceEnd(handle)


def open_rfcomm(addr, channel):
    ws2 = ctypes.WinDLL("ws2_32", use_last_error=True)
    sock = socket.socket(32, socket.SOCK_STREAM, 3)  # AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM
    sa = SOCKADDR_BTH()
    sa.addressFamily = 32
    sa.btAddr = _bt_addr_int(addr)
    sa.port = channel
    ws2.connect.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    if ws2.connect(sock.fileno(), ctypes.byref(sa), ctypes.sizeof(sa)) != 0:
        err = ctypes.get_last_error()
        sock.close()
        raise OSError("RFCOMM connect to %s channel %d failed (WSA error %d)" % (addr, channel, err))
    sock.settimeout(TIMEOUT_S)
    return sock


# ---------------------------------------------------------------- one command at a time
class WriteUnconfirmed(Exception):
    """A write was sent but no valid reply arrived. It is NOT resent; check the state with `status`."""


class Link:
    def __init__(self, sock, log=None, clock=time.monotonic, sleep=time.sleep):
        self.sock, self.parser, self.log = sock, Parser(), log
        self.clock, self.sleep = clock, sleep
        self.last_write_at = None

    def _note(self, tag, data):
        if self.log:
            self.log.write("%.3f %s %s\n" % (time.time(), tag, data.hex()))
            self.log.flush()

    def _exchange(self, frame, race_id, want_ind=False):
        """Send one frame; return (status, data, indication) for the matching reply.

        One command in flight, no retry. With want_ind the call also waits (briefly) for the 0x5D indication
        that some reads (the battery ones) use to deliver their data after the status-only reply."""
        self._note("TX", frame)
        self.sock.sendall(frame)
        now = time.monotonic  # reply timing always uses real time; self.clock only drives the write spacing
        deadline = now() + TIMEOUT_S
        rsp = ind = None
        quiet_until = None
        while now() < deadline:
            # ">=" matters: Windows' monotonic clock ticks only every ~15 ms, so right after the reply "now" equals
            # the deadline and a strict ">" would wait out a whole 250 ms receive timeout for nothing
            if rsp is not None and ((want_ind and ind is not None) or (quiet_until is not None and now() >= quiet_until)):
                break
            self.sock.settimeout(max(0.05, min(0.25, deadline - now())))
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("earbuds closed the connection")
            self._note("RX", chunk)
            for ptype, rid, payload in self.parser.feed(chunk):
                if rid == race_id and ptype == RESPONSE and rsp is None and payload:
                    rsp = payload
                    # only the battery reads deliver data in a follow-up indication; every other reply is complete
                    # the moment it arrives, so do not keep the caller waiting (this used to cost 0.3 s per read)
                    quiet_until = now() + (IND_WAIT_S if want_ind else 0.0)
                elif rid == race_id and ptype == INDICATION and ind is None:
                    ind = payload
        if rsp is None:
            raise TimeoutError("no reply to id %d within %.0f s (not retrying)" % (race_id, TIMEOUT_S))
        return rsp[0], rsp[1:], ind

    def read(self, race_id, payload=b""):
        status, data, _ = self._exchange(build_read(race_id, payload), race_id)
        return status, data

    def read_battery(self):
        """Left / right earbud and case battery in percent (None when not reported).

        Mapping is the app's own: GET_AGENT_CHANNEL data byte 1 means the agent bud is the right one;
        battery role 0 is the agent, role 1 the partner; the case level is the raw percentage."""
        result = {"left": None, "right": None, "case": None}
        status, data = self.read(GET_AGENT_CHANNEL)
        if status == 0 and data:
            agent_is_right = data[0] == 1
            for role in (0, 1):
                status, _, ind = self._exchange(build_read(GET_EARBUD_BATTERY, bytes([role])), GET_EARBUD_BATTERY, want_ind=True)
                if status != 0 or ind is None or len(ind) < 3 or ind[0] != 0:
                    continue  # that bud is not reporting (out of range or not present)
                level = min(ind[2], 100)
                is_right = agent_is_right if ind[1] == 0 else not agent_is_right
                result["right" if is_right else "left"] = level
        status, data = self.read(GET_CRADLE_BATTERY)
        if status == 0 and data and data[0] <= 100:
            result["case"] = data[0]
        return result

    def read_outside(self):
        status, data = self.read(GET_OUTSIDE_CTRL)
        if status != 0 or len(data) < 3:
            raise RuntimeError("could not read ANC/ambient state (status %d, %d bytes)" % (status, len(data)))
        return data[0], data[1], data[2]

    def read_adaptive(self):
        """Adaptive ANC ("Auto level") on/off."""
        status, data = self.read(GET_ADAPTIVE_ANC)
        if status != 0 or len(data) < 1 or data[0] not in (0, 1):
            raise RuntimeError("could not read adaptive ANC state (status %d, data %s)" % (status, data.hex()))
        return data[0] == 1

    def read_ambient_sub(self):
        """Ambient sub-mode: 0 Transparent, 1 Attention. Anything else is refused as unknown."""
        status, data = self.read(GET_AMBIENT_MODE)
        if status != 0 or len(data) < 2:
            raise RuntimeError("could not read ambient sub-mode (status %d, data %s)" % (status, data.hex()))
        for sub, payload in AMBIENT_SUB_PAYLOADS.items():
            if bytes(data[:2]) == payload:
                return sub
        raise RuntimeError("unexpected ambient sub-mode combination %s" % data[:2].hex())

    def read_buffer_mode(self):
        """Live stream mode: 0 Auto, 1 Music, 2 Video. The value is in the RSP (status, value)."""
        status, data = self.read(GET_BUFFER_MODE)
        if status != 0 or len(data) < 1 or data[0] not in BUFFER_NAMES:
            raise RuntimeError("could not read buffer mode (status %d, data %s)" % (status, data.hex()))
        return data[0]

    def _send_write(self, frame, race_id):
        """Shared write path: spacing across ALL writes, one in flight, never resent, status checked."""
        if self.last_write_at is not None:
            wait = MIN_WRITE_GAP_S - (self.clock() - self.last_write_at)
            if wait > 0:
                self.sleep(wait)
        self.last_write_at = self.clock()
        try:
            status, _, _ = self._exchange(frame, race_id)
        except TimeoutError as exc:
            raise WriteUnconfirmed(str(exc)) from None
        if status != 0:
            raise RuntimeError("earbuds rejected the write (status %d)" % status)
        self.sleep(0.05)  # the app reads back ~50-100 ms after each write

    def write_outside(self, mode, nc, amb):
        """Returns (state read back, matches request). Never retried."""
        frame = build_set_outside(mode, nc, amb)  # validates; raises before anything is sent
        self._send_write(frame, SET_OUTSIDE_CTRL)
        state = self.read_outside()
        return state, state == (mode, nc, amb)

    def write_adaptive(self, on):
        """Returns (state read back, matches request). Never retried. The reply can take ~1 s."""
        frame = build_set_adaptive(on)  # validates; raises before anything is sent
        self._send_write(frame, SET_ADAPTIVE_ANC)
        state = self.read_adaptive()
        return state, state == bool(on)

    def write_ambient_sub(self, sub):
        """Returns (state read back, matches request). Never retried."""
        frame = build_set_ambient_sub(sub)  # validates; raises before anything is sent
        self._send_write(frame, SET_AMBIENT_MODE)
        state = self.read_ambient_sub()
        return state, state == sub

    def write_buffer_mode(self, mode):
        """Returns (state read back, matches request). Never retried."""
        frame = build_set_buffer_mode(mode)  # validates; raises before anything is sent
        self._send_write(frame, SET_BUFFER_MODE)
        state = self.read_buffer_mode()
        return state, state == mode


class DeviceError(Exception):
    """Something the user can act on. kind: not_found | busy | wrong_device | timeout | closed."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class Session:
    """One persistent connection for the tray app: SDP, connect, model gate, then reads and the one write.

    All access goes through a lock so exactly one command is ever in flight."""

    def __init__(self, addr=DEFAULT_ADDR, channel=None, log=None):
        import threading
        self.addr, self.channel, self.log = addr.upper() if addr else None, channel, log
        self.sock = self.link = None
        self.lock = threading.RLock()

    @property
    def is_open(self):
        return self.link is not None

    def open(self):
        with self.lock:
            if self.link is not None:
                return
            try:
                self.addr = self.addr or find_paired_az100()
            except OSError as exc:
                raise DeviceError("not_found", "No AZ100 earbuds are paired with this PC. Pair them in Bluetooth settings.") from exc
            try:
                channel = self.channel or find_rfcomm_channel(self.addr)
            except OSError as exc:
                raise DeviceError("not_found", "The earbuds are not reachable. Connect them to this PC and try again.") from exc
            try:
                sock = open_rfcomm(self.addr, channel)
            except OSError as exc:
                raise DeviceError("busy", "Could not open the control channel. Close the Technics app on your phone.") from exc
            link = Link(sock, self.log)
            try:
                status, data = link.read(GET_MODEL_ID)
            except (TimeoutError, ConnectionError, OSError) as exc:
                sock.close()
                raise DeviceError("timeout", "The earbuds did not answer.") from exc
            model = (data[0] | data[1] << 8) if status == 0 and len(data) >= 2 else None
            if model not in AZ100_MODEL_IDS:
                sock.close()
                raise DeviceError("wrong_device", "These are not AZ100 earbuds. Nothing was sent.")
            self.sock, self.link = sock, link

    def close(self):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = self.link = None

    def _guard(self, fn):
        with self.lock:
            self.open()
            try:
                return fn(self.link)
            except WriteUnconfirmed:
                raise
            except (TimeoutError, ConnectionError, OSError) as exc:
                self.close()
                kind = "timeout" if isinstance(exc, TimeoutError) else "closed"
                raise DeviceError(kind, "Lost the connection to the earbuds.") from exc

    def outside(self):
        """Current mode and levels only (cheap enough to poll)."""
        return self._guard(lambda link: link.read_outside())

    def poll_state(self):
        """Mode, levels, Auto level and ambient sub-mode in ONE locked read, so no write can land between them
        and produce a picture that never existed on the earbuds. Optional reads that fail come back as None."""
        def read(link):
            mode, nc, amb = link.read_outside()
            try:
                adaptive = link.read_adaptive()
            except RuntimeError:
                adaptive = None
            try:
                sub = link.read_ambient_sub()
            except RuntimeError:
                sub = None
            return {"mode": mode, "nc": nc, "amb": amb, "adaptive": adaptive, "ambient_sub": sub}
        return self._guard(read)

    def adaptive(self):
        return self._guard(lambda link: link.read_adaptive())

    def ambient_sub(self):
        return self._guard(lambda link: link.read_ambient_sub())

    def buffer_mode(self):
        return self._guard(lambda link: link.read_buffer_mode())

    def snapshot(self):
        """Everything the flyout shows, read-only."""
        def read_all(link):
            mode, nc, amb = link.read_outside()
            status, data = link.read(GET_CODEC_INFO)
            codec = decode_codec(data) if status == 0 else None
            def optional(fn):  # a failed optional read must not fail the snapshot
                try:
                    return fn()
                except RuntimeError:
                    return None
            return {"mode": mode, "nc": nc, "amb": amb, "codec": codec, "battery": link.read_battery(),
                    "adaptive": optional(link.read_adaptive), "ambient_sub": optional(link.read_ambient_sub),
                    "buffer_mode": optional(link.read_buffer_mode)}
        return self._guard(read_all)

    def set_outside(self, mode, nc, amb):
        """The one write. Returns (state read back, matches request)."""
        return self._guard(lambda link: link.write_outside(mode, nc, amb))

    def set_adaptive(self, on):
        """Returns (state read back: bool, matches request)."""
        return self._guard(lambda link: link.write_adaptive(on))

    def set_ambient_sub(self, sub):
        """Returns (state read back: 0|1, matches request)."""
        return self._guard(lambda link: link.write_ambient_sub(sub))

    def set_buffer_mode(self, mode):
        """Returns (state read back: 0|1|2, matches request)."""
        return self._guard(lambda link: link.write_buffer_mode(mode))


# ---------------------------------------------------------------- decoding (from app code, verified on the wire)
def decode_codec(data):
    """Reply layout from StageGetCodecInfo: type, sample rate, channel mode, bitrate (3 bytes), ?, VBR flag."""
    if len(data) < 3:
        return None
    return {"codec": CODEC_NAMES.get(data[0], "code %d" % data[0]), "code": data[0],
            "rate_hz": SAMPLE_RATES.get(data[1]), "channels": CHANNEL_MODES.get(data[2], str(data[2])),
            "bitrate_raw": data[3:6].hex() if len(data) >= 6 else "", "vbr": data[7] if len(data) >= 8 else None}


def describe_codec(c):
    if not c:
        return "no codec data"
    rate = ("%g kHz" % (c["rate_hz"] / 1000)) if c["rate_hz"] else "rate unknown"
    return "%s, %s, %s" % (c["codec"], rate, c["channels"])


def describe(race_id, status, data):
    if status != 0:
        return "device returned status %d" % status
    if race_id == GET_MODEL_ID and len(data) >= 2:
        return "model id 0x%04X" % (data[0] | data[1] << 8)
    if race_id == GET_OUTSIDE_CTRL and len(data) >= 3:
        return "mode=%s  noise-cancel level=%d  ambient level=%d" % (MODE_NAMES.get(data[0], data[0]), data[1], data[2])
    if race_id == GET_A2DP_OPTION and data:
        return "connection option=%s" % A2DP_OPTION_NAMES.get(data[0], data[0])
    if race_id == GET_CODEC_INFO and data:
        return "codec in use: " + describe_codec(decode_codec(data))
    if race_id == GET_LE_AUDIO and data:
        return "LE Audio setting=%s  (0 = classic only, 1 = prefer LE Audio)" % data[0]
    return ""


def fmt_state(state):
    return "%s, noise-cancel level %d, ambient level %d" % (MODE_NAMES.get(state[0], state[0]), state[1], state[2])


# ---------------------------------------------------------------- CLI plumbing
def connect(args):
    addr = resolve_addr(args.addr)
    channel = args.channel or find_rfcomm_channel(addr)
    print("device %s  RFCOMM channel %d%s" % (addr, channel, " (given)" if args.channel else " (from SDP)"))
    log = open(args.log, "a", encoding="ascii") if args.log else None
    sock = open_rfcomm(addr, channel)
    link = Link(sock, log)
    status, data = link.read(GET_MODEL_ID)
    model = (data[0] | data[1] << 8) if status == 0 and len(data) >= 2 else None
    if model not in AZ100_MODEL_IDS:
        sock.close()
        raise SystemExit("STOP: this is not an AZ100 (model id %s, expected 0xA0-0xA3). Nothing else was sent." %
                         ("0x%04X" % model if model is not None else "unreadable"))
    print("model id 0x%04X (AZ100 family) - ok" % model)
    return sock, link, log


def close(sock, log):
    sock.close()
    if log:
        log.close()


def save_state(path, state):
    with open(path, "w", encoding="ascii") as f:
        json.dump({"mode": state[0], "nc": state[1], "amb": state[2], "saved": time.strftime("%Y-%m-%d %H:%M:%S")}, f)


def load_state(path):
    with open(path, encoding="ascii") as f:
        d = json.load(f)
    return d["mode"], d["nc"], d["amb"]


def cmd_probe(args):
    order = [GET_MODEL_ID, GET_OUTSIDE_CTRL, GET_A2DP_OPTION, GET_CODEC_INFO, GET_LE_AUDIO]
    if args.dry_run:
        for rid in order:
            print("%-18s -> %s" % (READ_IDS[rid], build_read(rid).hex(" ")))
        print("dry run: nothing sent")
        return 0
    sock, link, log = connect(args)
    try:
        for rid in order[1:]:
            status, data = link.read(rid)
            print("%-18s %s   raw=%s" % (READ_IDS[rid], describe(rid, status, data), data.hex()))
            time.sleep(0.1)
    finally:
        close(sock, log)
    return 0


def cmd_status(args):
    sock, link, log = connect(args)
    try:
        state = link.read_outside()
        print("ANC / ambient : " + fmt_state(state))
        for label, fn, fmt in (("Auto level", link.read_adaptive, lambda v: "on" if v else "off"),
                               ("Ambient mode", link.read_ambient_sub, AMBIENT_SUB_NAMES.get),
                               ("Delay mode", link.read_buffer_mode, BUFFER_NAMES.get)):
            try:
                text = fmt(fn())
            except RuntimeError:
                text = "unknown"
            print("%-14s: %s" % (label, text))
        batt = link.read_battery()
        print("Battery       : " + "  ".join("%s %s" % (k, "%d%%" % v if v is not None else "unknown") for k, v in batt.items()))
        for rid in (GET_A2DP_OPTION, GET_CODEC_INFO, GET_LE_AUDIO):
            status, data = link.read(rid)
            print("%-14s: %s" % ({GET_A2DP_OPTION: "Connection", GET_CODEC_INFO: "Codec", GET_LE_AUDIO: "LE Audio"}[rid],
                                 describe(rid, status, data)))
            time.sleep(0.1)
    finally:
        close(sock, log)
    return 0


def cmd_codec(args):
    sock, link, log = connect(args)
    try:
        status, data = link.read(GET_CODEC_INFO)
        c = decode_codec(data) if status == 0 else None
        print("codec reported by the earbuds: " + describe_codec(c) + "   raw=" + data.hex())
        status, opt = link.read(GET_A2DP_OPTION)
        print("connection option setting    : " + describe(GET_A2DP_OPTION, status, opt).split("=", 1)[-1])
        if c and c["code"] == 6:
            print("=> The earbuds are receiving LDAC.")
        elif c and c["code"]:
            print("=> The earbuds are receiving %s, NOT LDAC." % c["codec"])
        else:
            print("=> No codec reported (nothing streaming?).")
        print("Note: this is the codec of whichever device is streaming to the earbuds right now. To attribute it to\n"
              "this PC: turn the phone's Bluetooth off, play audio on the PC, then run this again.")
    finally:
        close(sock, log)
    return 0


def apply_outside(args, pick):
    """pick(current_state) -> (mode, nc, amb). Reads first, writes only with --yes."""
    sock, link, log = connect(args)
    try:
        cur = link.read_outside()
        want = pick(cur)
        validate_outside(*want)
        print("now   : " + fmt_state(cur))
        print("target: " + fmt_state(want))
        print("frame : " + build_set_outside(*want).hex(" "))
        if not args.yes:
            print("not sent (add --yes to apply).")
            return 0
        if not os.path.exists(BASELINE_FILE):
            save_state(BASELINE_FILE, cur)
            print("saved starting state to baseline.json (restore with: technics.py restore --yes)")
        save_state(PREVIOUS_FILE, cur)
        try:
            state, ok = link.write_outside(*want)
        except WriteUnconfirmed as exc:
            print("WRITE UNCONFIRMED: %s\nNot resending. Run `status` to see what the earbuds are actually doing." % exc)
            return 3
        print("read back: " + fmt_state(state))
        print("verified." if ok else "MISMATCH: the earbuds report a different state than requested.")
        return 0 if ok else 4
    finally:
        close(sock, log)


def apply_simple(args, read, write, want, fmt, frame):
    """Shared CLI path for the two simple writes (no baseline/previous saving)."""
    sock, link, log = connect(args)
    try:
        cur = read(link)
        print("now   : " + fmt(cur))
        print("target: " + fmt(want))
        print("frame : " + frame.hex(" "))
        if not args.yes:
            print("not sent (add --yes to apply).")
            return 0
        try:
            state, ok = write(link)
        except WriteUnconfirmed as exc:
            print("WRITE UNCONFIRMED: %s\nNot resending. Run `status` to see what the earbuds are actually doing." % exc)
            return 3
        print("read back: " + fmt(state))
        print("verified." if ok else "MISMATCH: the earbuds report a different state than requested.")
        return 0 if ok else 4
    finally:
        close(sock, log)


def cmd_adaptive(args):
    want = args.state == "on"
    return apply_simple(args, lambda l: l.read_adaptive(), lambda l: l.write_adaptive(want), want,
                        lambda v: "Auto level " + ("on" if v else "off"), build_set_adaptive(want))


def cmd_ambient_mode(args):
    want = {"transparent": SUB_TRANSPARENT, "attention": SUB_ATTENTION}[args.sub]
    return apply_simple(args, lambda l: l.read_ambient_sub(), lambda l: l.write_ambient_sub(want), want,
                        lambda v: "ambient mode " + AMBIENT_SUB_NAMES[v], build_set_ambient_sub(want))


def cmd_buffer_mode(args):
    want = {"auto": BUFFER_AUTO, "music": BUFFER_MUSIC, "video": BUFFER_VIDEO}[args.mode]
    return apply_simple(args, lambda l: l.read_buffer_mode(), lambda l: l.write_buffer_mode(want), want,
                        lambda v: "delay mode " + BUFFER_NAMES[v], build_set_buffer_mode(want))


def level_arg(text):
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("level must be a whole number 1-100")
    if not 1 <= v <= 100:
        raise argparse.ArgumentTypeError("level must be 1-100")
    return v


def cmd_anc(args):
    return apply_outside(args, lambda cur: (MODE_ANC, args.level, cur[2]))


def cmd_ambient(args):
    return apply_outside(args, lambda cur: (MODE_AMBIENT, cur[1], args.level))


def cmd_off(args):
    return apply_outside(args, lambda cur: (MODE_OFF, cur[1], cur[2]))


def cmd_noop(args):
    return apply_outside(args, lambda cur: cur)


def cmd_restore(args):
    path = PREVIOUS_FILE if args.previous else BASELINE_FILE
    if not os.path.exists(path):
        raise SystemExit("no saved state at %s" % path)
    saved = load_state(path)
    return apply_outside(args, lambda cur: saved)


# ---------------------------------------------------------------- offline self test (fake earbud, no hardware)
def cmd_selftest(_):
    import threading
    global TIMEOUT_S

    # --- framing vectors taken from the real app's traffic (an HCI capture, not included)
    assert build_frame(0) == bytes.fromhex("055a02000000"), "GET_MODEL_ID frame"
    assert build_frame(10) == bytes.fromhex("055a02000a00"), "GET_OUTSIDE_CTRL frame"
    assert build_set_outside(1, 0x35, 0x4C).hex() == "055a05000b0001354c", "SET_OUTSIDE_CTRL frame (NC 53/76)"
    assert build_set_outside(0, 100, 76).hex() == "055a05000b0000644c", "off keeps levels"
    assert build_set_outside(2, 100, 0x4F).hex() == "055a05000b0002644f", "ambient frame"
    p = Parser()
    stream = bytes.fromhex("055b09000000" "00a10000faf901" "055b06000a00" "0001354c")
    frames = p.feed(stream[:7]) + p.feed(stream[7:])  # split mid-frame
    assert frames[0] == (0x5B, 0, bytes.fromhex("00a10000faf901")), frames
    assert frames[1] == (0x5B, 10, bytes.fromhex("0001354c")), frames
    assert describe(GET_MODEL_ID, 0, bytes.fromhex("a10000faf901")) == "model id 0x00A1"
    assert describe(GET_OUTSIDE_CTRL, 0, bytes.fromhex("01354c")).startswith("mode=noise cancelling  noise-cancel level=53")
    c = decode_codec(bytes.fromhex("060c030000000000"))  # bytes seen live from the AZ100
    assert (c["codec"], c["rate_hz"], c["channels"]) == ("LDAC", 96000, "stereo"), c

    assert build_frame(103).hex() == "055a02006700" and build_read(GET_ADAPTIVE_ANC).hex() == "055a02006700"
    assert build_read(GET_AMBIENT_MODE).hex() == "055a02002100"
    assert build_set_adaptive(True).hex() == "055a0300680001" and build_set_adaptive(1).hex() == "055a0300680001"
    assert build_set_adaptive(False).hex() == "055a0300680000" and build_set_adaptive(0).hex() == "055a0300680000"
    assert build_set_ambient_sub(SUB_TRANSPARENT).hex() == "055a040022000000"
    assert build_set_ambient_sub(SUB_ATTENTION).hex() == "055a040022000101"
    for bad in (2, -1, 255, None, "1", "on", 1.0):
        try:
            build_set_adaptive(bad)
        except ValueError:
            continue
        raise AssertionError("adaptive validation let %r through" % (bad,))
    for bad in (2, -1, True, False, None, "0", 1.0):
        try:
            build_set_ambient_sub(bad)
        except ValueError:
            continue
        raise AssertionError("ambient sub validation let %r through" % (bad,))
    for bad in (b"\x00\x01", b"\x01\x00", b"\x00", b"\x02\x02", b"\x00\x00\x00", b""):
        try:
            validate_ambient_payload(bad)
        except ValueError:
            continue
        raise AssertionError("ambient payload %s let through" % bad.hex())

    assert (GET_BUFFER_MODE, SET_BUFFER_MODE, BUFFER_AUTO, BUFFER_MUSIC, BUFFER_VIDEO) == (58, 59, 0, 1, 2)
    assert build_read(GET_BUFFER_MODE).hex() == "055a02003a00" and build_frame(58).hex() == "055a02003a00"
    assert build_set_buffer_mode(BUFFER_AUTO).hex() == "055a03003b0000"
    assert build_set_buffer_mode(BUFFER_MUSIC).hex() == "055a03003b0001"
    assert build_set_buffer_mode(BUFFER_VIDEO).hex() == "055a03003b0002"
    for bad in (3, -1, True, False, None, "1", 1.0, 2.0, b"\x01", 255):
        for fn in (validate_buffer_mode, build_set_buffer_mode):
            try:
                fn(bad)
            except ValueError:
                continue
            raise AssertionError("buffer validation let %r through" % (bad,))
    for payload in (b"\x00", b"\x01", b"\x02"):
        try:
            build_read(GET_BUFFER_MODE, payload)
        except ValueError:
            continue
        raise AssertionError("buffer GET accepted a payload")

    # --- read allowlist and write validation: nothing dangerous can be built
    for bad in (11, 17, 59, 65, 66, 90, 104, 7168):
        try:
            build_read(bad)
        except ValueError:
            continue
        raise AssertionError("read allowlist let id %d through" % bad)
    for args in ((3, 50, 50), (-1, 50, 50), (1, 0, 50), (1, 101, 50), (1, 50, 0), (1, 50, 101), (True, 50, 50),
                 (1, 50.5, 50), (1, "50", 50)):
        try:
            build_set_outside(*args)
        except ValueError:
            continue
        raise AssertionError("validation let %r through" % (args,))
    assert not hasattr(Link, "write_raw") and not hasattr(Link, "send_raw")

    # --- Link against a fake earbud over a local socket pair
    class FakeBud(threading.Thread):
        def __init__(self, sock, state, answer_set=True, apply_set=True, set_status=0, agent_right=0,
                     batt=(80, 70), partner_present=True, case=90, adaptive=0, ambient_sub=(0, 0),
                     read_status=0, bad_sub=None, buffer=0, buffer_read_status=0, buffer_bad=None):
            super().__init__(daemon=True)
            self.adaptive, self.ambient_sub, self.read_status, self.bad_sub = adaptive, list(ambient_sub), read_status, bad_sub
            self.sock, self.state, self.answer_set, self.apply_set, self.set_status = sock, list(state), answer_set, apply_set, set_status
            self.agent_right, self.batt, self.partner_present, self.case = agent_right, batt, partner_present, case
            self.buffer, self.buffer_read_status, self.buffer_bad = buffer, buffer_read_status, buffer_bad
            self.seen = []

        def run(self):
            parser = Parser()
            while True:
                try:
                    data = self.sock.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                for ptype, rid, payload in parser.feed(data):
                    self.seen.append((rid, payload))
                    if rid == 0:
                        self.sock.sendall(build_frame(0, bytes.fromhex("00a10000faf901"), RESPONSE))
                    elif rid == 10:
                        self.sock.sendall(build_frame(10, bytes([0] + self.state), RESPONSE))
                    elif rid == 11 and self.answer_set:
                        if self.apply_set and self.set_status == 0:
                            self.state = list(payload)
                        self.sock.sendall(build_frame(11, bytes([self.set_status]), RESPONSE))
                    elif rid == 18:
                        self.sock.sendall(build_frame(18, bytes.fromhex("00060c030000000000"), RESPONSE))
                    elif rid == 103:
                        self.sock.sendall(build_frame(103, bytes([self.read_status, self.adaptive]), RESPONSE))
                    elif rid == 104 and self.answer_set:
                        if self.apply_set and self.set_status == 0:
                            self.adaptive = payload[0]
                        self.sock.sendall(build_frame(104, bytes([self.set_status]), RESPONSE))
                    elif rid == 33:
                        sub = self.bad_sub or self.ambient_sub
                        self.sock.sendall(build_frame(33, bytes([self.read_status] + list(sub)), RESPONSE))
                    elif rid == 34 and self.answer_set:
                        if self.apply_set and self.set_status == 0:
                            self.ambient_sub = list(payload)
                        self.sock.sendall(build_frame(34, bytes([self.set_status]), RESPONSE))
                    elif rid == 58:
                        val = self.buffer if self.buffer_bad is None else self.buffer_bad
                        self.sock.sendall(build_frame(58, bytes([self.buffer_read_status, val]), RESPONSE))
                    elif rid == 59 and self.answer_set:
                        if self.apply_set and self.set_status == 0:
                            self.buffer = payload[0]
                        self.sock.sendall(build_frame(59, bytes([self.set_status]), RESPONSE))
                    elif rid == GET_AGENT_CHANNEL:
                        self.sock.sendall(build_frame(rid, bytes([0, self.agent_right]), RESPONSE))
                    elif rid == GET_EARBUD_BATTERY:
                        role = payload[0]
                        if role == 1 and not self.partner_present:
                            self.sock.sendall(build_frame(rid, bytes([2]), RESPONSE))  # status 2: no such bud
                        else:
                            self.sock.sendall(build_frame(rid, bytes([0]), RESPONSE))
                            self.sock.sendall(build_frame(rid, bytes([0, role, self.batt[role]]), INDICATION))
                    elif rid == GET_CRADLE_BATTERY:
                        self.sock.sendall(build_frame(rid, bytes([0, self.case]), RESPONSE))

    class FakeTime:
        def __init__(self):
            self.t = 100.0
            self.slept = []

        def clock(self):
            return self.t

        def sleep(self, s):
            self.slept.append(s)
            self.t += s

    def make(real_clock=False, **kw):
        a, b = socket.socketpair()
        bud = FakeBud(b, kw.pop("state", (2, 100, 100)), **kw)
        bud.start()
        ft = FakeTime()
        # a frozen fake clock can never reach the reply timeout, so timeout tests use the real one
        link = Link(a, clock=time.monotonic, sleep=ft.sleep) if real_clock else Link(a, clock=ft.clock, sleep=ft.sleep)
        return a, b, bud, link, ft

    # normal write: sent exactly once, read back, verified
    a, b, bud, link, ft = make()
    TIMEOUT_S = 1.0
    state, ok = link.write_outside(1, 60, 100)
    assert ok and state == (1, 60, 100), (state, ok)
    assert [s for s in bud.seen if s[0] == 11] == [(11, bytes([1, 60, 100]))], bud.seen
    # second write immediately after: the 0.5 s spacing must be enforced
    ft.slept.clear()
    link.write_outside(1, 61, 100)
    assert any(s >= 0.4 for s in ft.slept), ft.slept
    a.close(); b.close()

    # earbuds report a different state than requested -> mismatch is reported, not hidden
    a, b, bud, link, ft = make(apply_set=False)
    state, ok = link.write_outside(1, 60, 100)
    assert not ok and state == (2, 100, 100), (state, ok)
    a.close(); b.close()

    # non-zero status -> error, no read-back claims
    a, b, bud, link, ft = make(set_status=1)
    try:
        link.write_outside(1, 60, 100)
        raise AssertionError("rejected write was treated as success")
    except RuntimeError:
        pass
    a.close(); b.close()

    # no reply to the write -> WriteUnconfirmed and the SET is NOT resent
    a, b, bud, link, ft = make(answer_set=False)
    TIMEOUT_S = 0.6
    try:
        link.write_outside(1, 60, 100)
        raise AssertionError("missing reply was treated as success")
    except WriteUnconfirmed:
        pass
    time.sleep(0.2)
    assert len([s for s in bud.seen if s[0] == 11]) == 1, "write was resent"
    a.close(); b.close()

    # battery: agent is the LEFT bud (reply byte 0): role 0 = left, role 1 = right
    a, b, bud, link, ft = make(agent_right=0, batt=(80, 70), case=90)
    assert link.read_battery() == {"left": 80, "right": 70, "case": 90}, "agent-left mapping"
    a.close(); b.close()
    # agent is the RIGHT bud: roles swap
    a, b, bud, link, ft = make(agent_right=1, batt=(80, 70), case=55)
    assert link.read_battery() == {"left": 70, "right": 80, "case": 55}, "agent-right mapping"
    a.close(); b.close()
    # partner bud absent (status 2) and a nonsense case value are reported as unknown, not as 0
    a, b, bud, link, ft = make(agent_right=0, partner_present=False, case=255)
    assert link.read_battery() == {"left": 80, "right": None, "case": None}, "absent partner / bad case"
    a.close(); b.close()
    # reads only: the battery path sent nothing but allowlisted frames
    a, b, bud, link, ft = make()
    link.read_battery()
    time.sleep(0.1)
    assert {rid for rid, _ in bud.seen} <= set(READ_IDS), bud.seen
    a.close(); b.close()
    for rid, payload in ((GET_EARBUD_BATTERY, b"\x02"), (GET_EARBUD_BATTERY, b""), (GET_MODEL_ID, b"\x00"),
                         (GET_CRADLE_BATTERY, b"\x01")):
        try:
            build_read(rid, payload)
        except ValueError:
            continue
        raise AssertionError("read payload guard let %d/%s through" % (rid, payload.hex()))
    # frames exactly as the real app sent them (an HCI capture, not included)
    assert build_read(GET_AGENT_CHANNEL).hex() == "055a0200d40c", "agent channel frame"
    assert build_read(GET_EARBUD_BATTERY, b"\x00").hex() == "055a0300d60c00", "battery role 0 frame"
    assert build_read(GET_EARBUD_BATTERY, b"\x01").hex() == "055a0300d60c01", "battery role 1 frame"

    # invalid values never reach the wire
    a, b, bud, link, ft = make()
    TIMEOUT_S = 1.0
    try:
        link.write_outside(1, 0, 100)
        raise AssertionError("invalid level was sent")
    except ValueError:
        pass
    time.sleep(0.1)
    assert not [s for s in bud.seen if s[0] == 11], "invalid write reached the device"
    a.close(); b.close()

    # --- adaptive ANC and ambient sub-mode: reads, writes, mismatch, rejection, no reply
    TIMEOUT_S = 1.0
    a, b, bud, link, ft = make(adaptive=0, ambient_sub=(0, 0))
    assert link.read_adaptive() is False and link.read_ambient_sub() == SUB_TRANSPARENT
    assert link.write_adaptive(True) == (True, True)
    assert link.write_adaptive(0) == (False, True)
    assert link.write_ambient_sub(SUB_ATTENTION) == (SUB_ATTENTION, True)
    assert link.write_ambient_sub(0) == (SUB_TRANSPARENT, True)
    assert (104, b"\x01") in bud.seen and (104, b"\x00") in bud.seen
    assert (34, b"\x01\x01") in bud.seen and (34, b"\x00\x00") in bud.seen
    # spacing across mixed writes: outside, adaptive, ambient back to back
    ft.slept.clear()
    link.write_outside(1, 60, 100)
    link.write_adaptive(True)
    link.write_ambient_sub(SUB_ATTENTION)
    assert len([s for s in ft.slept if s >= 0.4]) == 3, ft.slept
    time.sleep(0.1)
    assert {rid for rid, _ in bud.seen if rid not in READ_IDS} <= {SET_OUTSIDE_CTRL, SET_ADAPTIVE_ANC, SET_AMBIENT_MODE}, bud.seen
    # invalid values never reach the wire
    n = len(bud.seen)
    for fn, arg in ((link.write_adaptive, 2), (link.write_adaptive, "on"), (link.write_ambient_sub, 2),
                    (link.write_ambient_sub, True)):
        try:
            fn(arg)
            raise AssertionError("invalid value sent")
        except ValueError:
            pass
    time.sleep(0.1)
    assert len(bud.seen) == n, "invalid write reached the device"
    a.close(); b.close()

    # snapshot tolerates failing optional reads, and reports them as None
    for kw, want in (({}, (False, SUB_TRANSPARENT)), ({"read_status": 1}, (None, None)),
                     ({"bad_sub": (1, 0)}, (False, None))):
        a, b, bud, link, ft = make(**kw)
        sess = Session()
        sess.link, sess.sock = link, a
        snap = sess.snapshot()
        assert (snap["adaptive"], snap["ambient_sub"]) == want, (kw, snap)
        assert snap["mode"] == 2 and snap["codec"]["codec"] == "LDAC", snap
        a.close(); b.close()

    for setter, wid in ((lambda l: l.write_adaptive(True), 104), (lambda l: l.write_ambient_sub(SUB_ATTENTION), 34)):
        # mismatch is reported
        a, b, bud, link, ft = make(apply_set=False)
        state, ok = setter(link)
        assert not ok, "mismatch hidden"
        a.close(); b.close()
        # rejected status raises
        a, b, bud, link, ft = make(set_status=1)
        try:
            setter(link)
            raise AssertionError("rejected write was treated as success")
        except RuntimeError:
            pass
        a.close(); b.close()
        # no reply: WriteUnconfirmed, never resent
        a, b, bud, link, ft = make(answer_set=False)
        TIMEOUT_S = 0.6
        try:
            setter(link)
            raise AssertionError("missing reply was treated as success")
        except WriteUnconfirmed:
            pass
        time.sleep(0.2)
        assert len([s for s in bud.seen if s[0] == wid]) == 1, "write was resent"
        a.close(); b.close()
        TIMEOUT_S = 1.0

    # --- buffer mode (live stream mode): reads, writes, spacing, mismatch, rejection, no reply
    TIMEOUT_S = 1.0
    a, b, bud, link, ft = make(buffer=0)
    assert link.read_buffer_mode() == BUFFER_AUTO
    assert link.write_buffer_mode(BUFFER_VIDEO) == (BUFFER_VIDEO, True)
    assert link.write_buffer_mode(BUFFER_MUSIC) == (BUFFER_MUSIC, True)
    assert link.write_buffer_mode(0) == (BUFFER_AUTO, True)
    assert [p for r, p in bud.seen if r == 59] == [b"\x02", b"\x01", b"\x00"], bud.seen
    ft.slept.clear()
    link.write_outside(1, 60, 100)
    link.write_adaptive(True)
    link.write_ambient_sub(SUB_ATTENTION)
    link.write_buffer_mode(BUFFER_VIDEO)
    assert len([s for s in ft.slept if s >= 0.4]) == 4, ft.slept
    time.sleep(0.1)
    assert {rid for rid, _ in bud.seen if rid not in READ_IDS} <= {11, 104, 34, 59}, bud.seen
    n = len(bud.seen)
    for bad in (3, -1, True, None, "1", 1.0):
        try:
            link.write_buffer_mode(bad)
            raise AssertionError("invalid buffer mode sent")
        except ValueError:
            pass
    time.sleep(0.1)
    assert len(bud.seen) == n, "invalid write reached the device"
    a.close(); b.close()
    # bad reads: rejected status, value outside 0..2, short reply
    for kw in ({"buffer_read_status": 1}, {"buffer_bad": 3}, {"buffer_bad": 255}):
        a, b, bud, link, ft = make(**kw)
        try:
            link.read_buffer_mode()
            raise AssertionError("bad buffer read accepted: %r" % (kw,))
        except RuntimeError:
            pass
        a.close(); b.close()
    a, b = socket.socketpair()
    link = Link(a)
    b.sendall(build_frame(58, b"\x00", RESPONSE))  # status only, no value
    try:
        link.read_buffer_mode()
        raise AssertionError("short buffer reply accepted")
    except RuntimeError:
        pass
    a.close(); b.close()
    # snapshot carries buffer_mode and tolerates a failing read
    for kw, want in (({"buffer": 2}, 2), ({"buffer_read_status": 1}, None), ({"buffer_bad": 9}, None)):
        a, b, bud, link, ft = make(**kw)
        sess = Session()
        sess.link, sess.sock = link, a
        snap = sess.snapshot()
        assert snap["buffer_mode"] == want and snap["mode"] == 2, (kw, snap)
        a.close(); b.close()
    # mismatch, rejected status, no reply
    a, b, bud, link, ft = make(apply_set=False)
    state, ok = link.write_buffer_mode(BUFFER_VIDEO)
    assert not ok and state == BUFFER_AUTO, (state, ok)
    a.close(); b.close()
    a, b, bud, link, ft = make(set_status=1)
    try:
        link.write_buffer_mode(BUFFER_VIDEO)
        raise AssertionError("rejected write was treated as success")
    except RuntimeError:
        pass
    a.close(); b.close()
    a, b, bud, link, ft = make(answer_set=False)
    TIMEOUT_S = 0.6
    try:
        link.write_buffer_mode(BUFFER_VIDEO)
        raise AssertionError("missing reply was treated as success")
    except WriteUnconfirmed:
        pass
    time.sleep(0.2)
    assert len([s for s in bud.seen if s[0] == 59]) == 1, "write was resent"
    a.close(); b.close()

    TIMEOUT_S = 6.0
    print("selftest ok")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, writes=False):
        p.add_argument("--addr", default=DEFAULT_ADDR, help="earbuds address (default: found among paired devices)")
        p.add_argument("--channel", type=int, help="RFCOMM channel (skips the SDP lookup; debug only)")
        p.add_argument("--log", help="append raw TX/RX hex to this file")
        if writes:
            p.add_argument("--yes", action="store_true", help="actually send the write")
        return p

    sub.add_parser("selftest")
    pr = common(sub.add_parser("probe"))
    pr.add_argument("--dry-run", action="store_true")
    common(sub.add_parser("status"))
    common(sub.add_parser("codec"))
    common(sub.add_parser("off"), True)
    common(sub.add_parser("noop"), True)
    pa = common(sub.add_parser("anc"), True)
    pa.add_argument("level", type=level_arg)
    pm = common(sub.add_parser("ambient"), True)
    pm.add_argument("level", type=level_arg)
    pad = common(sub.add_parser("adaptive"), True)
    pad.add_argument("state", choices=("on", "off"))
    pam = common(sub.add_parser("ambient-mode"), True)
    pam.add_argument("sub", choices=("transparent", "attention"))
    pbm = common(sub.add_parser("buffer-mode"), True)
    pbm.add_argument("mode", choices=("auto", "music", "video"))
    prs = common(sub.add_parser("restore"), True)
    prs.add_argument("--previous", action="store_true", help="restore the state before the last write instead of the baseline")
    args = ap.parse_args()
    return {"selftest": cmd_selftest, "probe": cmd_probe, "status": cmd_status, "codec": cmd_codec, "anc": cmd_anc,
            "ambient": cmd_ambient, "off": cmd_off, "noop": cmd_noop, "restore": cmd_restore,
            "adaptive": cmd_adaptive, "ambient-mode": cmd_ambient_mode, "buffer-mode": cmd_buffer_mode}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
