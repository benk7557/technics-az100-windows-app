# Technics EAH-AZ100 control protocol (static analysis of Technics Audio Connect 5.0.1)

Source: static analysis of the Technics Audio Connect Android app (com.panasonic.technicsaudioconnect, versionCode 104)
with JADX 1.5.6, done for interoperability. The app and its decompiled code are NOT included in this repository.
The early sections were written from reading code; later sections record what was verified on real earbuds.
This is a working log, kept in the order things were found.

## Chipset / stack
Airoha SDK (`com.airoha.*`) using Airoha's RACE command protocol, plus Panasonic custom RACE IDs
(`com.airoha.libbase.RaceCommand.constant.RaceIdPana`). AZ100 = `Constants.UnitVersion.VERSION4`
-> `DeviceMmiAiroha4` (extends 3 extends 2).

## Transport (DeviceMmiAiroha3.setConnection, line ~2186)
- Classic connection: RFCOMM socket to SPP UUID `00000000-0000-0000-0099-AABBCCDDEEFF`
- LE Audio connection: GATT, service `5052494D-2DAB-0341-6972-6F6861424C45`,
  TX `43484152-2DAB-3241-6972-6F6861424C45`, RX `43484152-2DAB-3141-6972-6F6861424C45`
  (TX/RX naming is from the app's side; confirm direction on a live device)

## Frame (RacePacket.getRaw)
`05 | type | len_lo len_hi | id_lo id_hi | payload...`
- type `0x5A` (90) = command that wants a response; response type `0x5B` (91)
- len = 2 (id) + len(payload), little-endian; id little-endian
- Response: byte[6] = status (0 = ok), data starts at byte[7] (GetOutsideCtrl/GetLeAudio read bytes 7..9)
  (indexes are into the full frame incl. the 05 header)

## Commands (RaceIdPana)
| Function | Get | Set | Set payload |
|---|---|---|---|
| Outside ctrl (ANC/ambient) | 10 | 11 | `[mode, ncLevel, ambientLevel]` mode 1=ANC, 2=ambient, 0=unset |
| NC adjust | 56 | 57 | `[level, setMode]` level 20..40 = -6.0..+4.0 dB step 0.5 (32 = 0), setMode 0=temp 1=execute 2=save |
| Ambient mode | 33 | 34 | transparent(0)/attention(1) |
| LE Audio | 89 | 90 | `[0=off, 1=on]` (LE_AUDIO_SETTING_OFF/ON) |
| A2DP codec option | 16 | 17 | 0 conn priority, 1 quality priority, 16 conn prio LDAC only, 17 LDAC priority |
| Codec info | 18 | - | |
| Multipoint | 50 | 51 | 0 off, 1 on, 2 triple |
| Adaptive ANC | 103 | 104 | |

## Resolved by deeper reading
- No auth/handshake. On connect the app only runs reads: getModelId, checkAgentChannel,
  checkPartnerExistence, then getOutsideCtrl/getCodecInfo/getA2DP/getAmbientMode/... (DeviceMmiAiroha2.getTopData/getMediumData).
  CORRECTION: RequestInitialize (id 65) is NOT a handshake. It is the FACTORY RESET, sent only from
  DeviceInfoFragmentZui.showInitializeDialog ("factory default settings"). NEVER SEND ID 65.
  (Found wrong by the blind validation agent; verified in code.)
- The app also auto-sends one SET at every connect: setDolbyDevice (ID 101, 1 byte). Our tool must not send it.
- Model gate: GetModelId (ID 0) bytes 0-1 LE must be 0xA0..0xA3 (AZ100/E/G/P) before any other command.
- Response matching is by ID only (type ignored): send one command at a time; 0x5D indications with the same ID
  (e.g. ID 10 on touch-mode change) can be mistaken for replies.
- AZ100 NC-adjust (ID 57) valid level bytes are 24..40 (-4.0..+4.0 dB), not 20..40; do not use it anyway.
- After LE Audio / codec change the link drops, may return on the other transport (SPP vs GATT) and a different address; app waits up to 40 s.
- Both buds must be present (isPartnerExist) for LE Audio / codec changes.
- Codec option values accepted: 0, 1, 16, 17 only.
- Levels: UI sends 1..100 (0 is coerced to 1; max ANC is sent as 100). Slider writes are throttled to 1 per 500 ms.
- Get commands carry no payload. Responses: byte[6]=status, data from byte[7] (OutsideCtrl: mode, nc, ambient).
- Device also pushes unsolicited indications (type 0x5D / 93), e.g. id 10 when mode changes by touch.
- LE_AUDIO set is one byte and the app confirms it with dialogs, then expects the link to drop and the user to
  re-pair (LeAudioSettingFragmentZui.showChangingDialog). App only offers it when both buds are present (isPartnerExist).
- Command timeout in the app: 6000 ms (AirohaMmiMgr.TIMEOUT_RACE_CMD_NOT_RSP).

## Safety rules for the tool (do NOT cross)
Allowlist only: GET 10, 16, 18, 33, 56, 89, 91; SET 11 (levels 1..100, mode 0/1/2) and later 90 / 17 after confirmation.
Never implement: ID 65 (factory reset), 66 (power off), 75 (demo mode), 26/27/24 (key remap), 93 (safe max volume),
72 FixVPSettings, 84 (clear charge error), 107 (pairing status), 101 (setDolbyDevice), PEQ saves, wearing-test (79 etc.),
any RACE flash/NVkey/FOTA ID (1794..1807, 7168..7195), FOTA (libfota*), 48 FixOutsideCtrl, SET 57 with setMode 2 (SAVE_PARAM), demo mode, power-off request,
reset, key remap, anything under com.airoha.libfota*/libmmi Leaudio BIS stages, or raw/unknown RACE IDs.
Rate limit >= 500 ms between writes, one command in flight, 6 s timeout, no auto-retry of writes.

## Three-way review result (validator + full-app bottom-up A + full-app top-down B, all blind)
Agreed by all: wire format, transports, AZ100 = VERSION4/DeviceMmiAiroha4, ANC/ambient = 10/11, LE Audio = 89/90,
codec = 16/17 {0,1,16,17}, 65 = factory reset, 66 = power off, connect path is reads (+ one write: 101 setDolbyDevice),
FOTA only on explicit user taps, raw-command UI is dead code, test mode is hard-coded off.
A and B each list 57/104/34/22/3286/3284/3328/7175 as "safe"; we deliberately exclude them from v1 (optional, persistence unknown,
7175/3284/3328 are Airoha-native, 7175 sits in the FOTA ID range).
Disputes settled in code: AWS API client is dead (A right); app DOES SHA-256-check downloaded firmware, DeviceFotaConstants.checkSum (B right).
ID COLLISION (verified RaceId.java): Panasonic id 10/11 equal stock Airoha ids 10 (SET_IR_ONOFF) / 11 (GET_BOX_BATTERY);
6,7,8,9,14 collide too. Our tool must ONLY talk to a device whose GetModelId (id 0) returns 0xA0..0xA3, and never to other Airoha devices.

## v1 tool scope (final)
Phase 1 read-only: id 0 (model gate), 10, 16, 18, 89.
Phase 2 write: id 11 only, mode 0/1/2, levels 1..100, >=500 ms apart, read-back with id 10 after each write.
Phase 3 (separate explicit confirmation each time, both buds out of case): id 17 {0,1}, id 90 {0,1}. Expect link drop + re-pair.
Everything else is blocked in code (allowlist), no raw-hex passthrough.

## LIVE CAPTURE VERIFIED (Android HCI snoop log, 2026-10-20, capture files not included)
Unit under test: GetModelId reply data `00 a1 00 00 fa f9 01` -> model byte 0xA1 (AZ100E variant), fw "JNZ1ZE 1.040".
Transport confirmed: classic RFCOMM, DLCI 0x2a (= channel 21) carries the RACE frames. (A second DLCI 0x2e carries something else; ignore.)
Channel number may differ per pairing/SDP: on Windows resolve by SDP UUID 00000000-0000-0000-0099-AABBCCDDEEFF, never hard-code 21.
Frame format, IDs, payloads and reply layouts all matched the decompiled code byte for byte:
- SET outside ctrl  `05 5a 05 00 0b 00 | mode nc amb` ; reply RSP `...00` (status only, ~30-70 ms)
  mode 00=off 01=NC 02=ambient. Off keeps the previous levels (`00 64 4c`). NC level observed 1..100, ambient 1..100.
- GET outside ctrl `05 5a 02 00 0a 00` ; reply RSP data = `00 | mode nc amb` (status then 3 bytes)
- The app follows every SET 11 with a GET 10 (~50-100 ms later) and throttles slider SETs to ~500 ms.
- SET adaptive ANC (104) `01`/`00`, reply took ~0.9 s (slower than outside ctrl); GET 103 reply `00 | value`.
- SET ambient mode (34) payload `[ambientMode, musicMode]`: `00 00` transparent+play, `01 01` attention+stop; GET 33 reply `00 | a m`.
- Some GETs answer with a status-only RSP and then deliver the data in a separate 0x5D indication (keymap 25, lang 37, demo 74,
  usage 76, charge error 83, fw 7175, battery 3286). Outside ctrl / adaptive / ambient / model id return data in the RSP itself.
- Connect burst (reads only, plus SET 101 dolby): 0, 3284, 3328, 2305, 101, NV read 2560 x3, 240, 103, 3286, 7175, 67, 240, 25, 37, 74, 76, 83 ...
- Opening the Connection-mode screen and the LE Audio screen sent NO writes (only GetBisState 8712 reads). Looking is safe; changing is not tested.
- LE Audio screen warning (app string): "Changing these settings will clear all pairing information stored in the earbuds. Pair the earbuds again."
  => treat id 90 as destructive to pairings (all of them, incl. other phones/PC). Keep it last, behind its own confirmation.
Video<->log clock: video_seconds = log_t_rel + 8.2 (anchored on the first NC tap).

## TOOL STATUS (tool/technics.py) - phase 1 read-only, VERIFIED ON HARDWARE 2026-10-20
`python -I tool/technics.py probe` : SDP lookup -> RFCOMM channel 21 (Winsock; Begin needs flags=0 and context "(XX:XX:..)"),
connects, reads id 0/10/16/18/89 only (allowlist in code; selftest proves writes like 11/17/65/66/90/104/7168 are refused).
Live result on the user's AZ100E with phone app closed: model 0x00A1, outside ctrl mode=ambient nc=100 amb=100 (raw 026464),
A2DP option 0x11 (LDAC priority), codec 06 0c 03 ... (LDAC, 96 kHz), LE Audio setting 0.
The earbuds accepted the PC's RFCOMM connection while the phone was connected over A2DP (phone app closed).
Reply formats for 16 / 18 / 89 now seen on the wire: RSP data = status(00) then value bytes, as the app code predicted.
PHASE 2 BUILT (offline-tested against a fake earbud, NOT yet run on hardware): commands status, codec, anc N, ambient N, off, noop,
restore [--previous]; writes only id 11, need --yes, levels 1..100, >=0.5 s apart, read-back verified, never resent on timeout,
baseline.json saved before the first write. codec reply layout (StageGetCodecInfo): type, sample-rate code, channel mode, 3 bitrate bytes, ?, VBR.
Live test order: status -> codec (phone BT off, audio playing on PC) -> noop --yes -> anc 60 --yes -> restore --yes.

## PHASE 2 LIVE RESULTS (2026-10-20, user's AZ100E, fw JNZ1ZE 1.040) - all passed
status/codec/noop/anc 60/restore; every mode + extremes (1,100); 13-write slider sweep (0 mismatches, ~1.1 s/write incl. read-back);
4 reconnects incl. after 60 s idle (3.4-4.5 s each). ~25 writes total, 0 errors/timeouts/rejections.
LDAC attribution: with phone Bluetooth off and audio playing on the PC, codec = LDAC 96 kHz stereo -> the Alternative A2DP driver delivers LDAC.
PERSISTENCE: NC 37 / ambient 63 survived putting the buds in the case and taking them out -> writes are persistent in practice.
(Does not distinguish flash from RAM kept alive by the case; either way: do not script continuous high-rate writes.)
LE Audio / codec switching (ids 90 / 17): OUT OF SCOPE for now (id 90 wipes ALL pairings; no capture of either write exists yet).

## BATTERY READS (added for the tray app; decoded from app code + capture, offline-tested, live check pending)
GET_AGENT_CHANNEL id 3284 (no payload) -> RSP [status, v]; v==1 means the agent bud is the RIGHT one (capture: v=0, agent = left).
GET_EARBUD_BATTERY id 3286 payload [role] (0 agent, 1 partner) -> RSP [status] then IND(0x5D) [status, role, percent] (~2-150 ms later).
  role 0 = agent, role 1 = partner; left/right follows the agent mapping above. A non-zero RSP status means that bud is not reporting.
GET_CRADLE_BATTERY id 64 (no payload) -> RSP [status, percent 0..100]; other values = unknown.
All three are polled by the official app itself (connect + every 10 min). The tool allowlists exactly these (id, payload) pairs.

## TRAY APP (tray/, built with Impeccable direction "Audiogram Chart"; visual spec in docs/DESIGN.md)
Python + pystray + pywebview (WebView2). Left-click toggles the flyout, right-click menu (Open, Start with Windows, Quit).
One persistent Session while the flyout is open (closes 30 s after it hides); writes 500 ms apart, trailing value wins, read-back verified.
Tested with --demo (fake earbuds): dark mode in the real window, click and drag, hide on outside click; light mode, Off, loading and error
states in a browser with emulated colour scheme. Needs the earbuds connected to this PC for the real run.

## v2 ADDITIONS, VERIFIED ON HARDWARE (2026-10-21)
New writes (the ONLY writes the tool can send are now: id 11 [mode,nc,amb]; id 104 [0|1]; id 34 [0,0]|[1,1]):
- Auto level = Adaptive ANC. GET 103 `05 5a 02 00 67 00` -> RSP [status, 0|1]. SET 104 `05 5a 03 00 68 00 <0|1>`. Verified on/off with read-back.
- Ambient sub-mode. GET 33 `05 5a 02 00 21 00` -> RSP [status, a, m]. SET 34 `05 5a 04 00 22 00 <a> <m>`: Transparent = 00 00, Attention = 01 01
  (the official app only ever sends these two; Attention also stops playback when switching into Ambient, per its own text). Verified both ways.
Tray app v2: compact battery plot, option band (Auto level switch in Noise cancelling, Transparent/Attention in Ambient), one serialized write queue,
global hotkey Ctrl+Alt+E (cycles Off -> Noise cancelling -> Ambient, settings in tray/settings.json), idle channel release 30 s after last use.
Bugs found by live testing and fixed:
- Windows monotonic clock ticks ~15 ms: a strict ">" deadline test made every read wait a full 250 ms receive timeout (now ">="; connect + full state read ~0.3 s).
- Stale-read race: a refresh that began before a write could overwrite the screen after it (UI showed a state the earbuds were not in).
  Fix: epoch counter in app.js discards reads older than the last local change/write; Session.poll_state() reads everything in ONE locked call.
- Hotkey-only use would have held the earbuds' RFCOMM channel open forever (now released 30 s after the last cycle).
Debug aid: set TECHNICS_WIRE_LOG=1 before starting tray/app.py to log every raw frame to tray/wire.log.
Regression result: 7 rapid UI clicks (1.2 s apart) -> 7 writes, correct bytes; final screen matched an independent CLI read.

## DELAY MODE ("Suppression of sound interruptions and delays" / Live stream mode), VERIFIED ON HARDWARE (2026-10-21)
GET id 58 `05 5a 02 00 3a 00` -> RSP [status, value]; SET id 59 `05 5a 03 00 3b 00 <v>`; v: 0 Auto, 1 Music (suppress interruptions), 2 Video (reduce delay).
Official app text: the "reduce delay" option is only effective when NOT using LDAC. Staged live test: read (Auto) -> same-value write -> Video -> back to Auto,
all accepted with read-back, no link drop, codec unchanged (still LDAC 96 kHz). Writes now allowed by the tool: id 11, 104, 34, 59 only.
Alternative A2DP Driver (PC side) facts: AltA2dpConfig.exe (GUI, normal user) + AltA2dpSVC.exe (LocalSystem) + AltA2DP.sys; per-device data under
HKLM\SYSTEM\CurrentControlSet\Services\AltA2DP\Parameters\Devices (Users = read-only, so the GUI reaches the driver through the service). Its status showed LDAC 96 kHz/24-bit/660 kbps
(SQ) and a "delayed Bluetooth packet transmission" warning = radio congestion. The user rejected GUI (UI Automation) control of it.

## Open questions (need live device)
- Whether Windows can open the custom-UUID RFCOMM service (WinRT RfcommDeviceService)
- Exact semantics of GET_LE_AUDIO / A2DP option response bytes
