# iRobot Braava 240 – BLE Protocol Reference

This document describes the Bluetooth Low Energy (BLE) protocol used by the iRobot Braava 240 (codename "Altadena"). All communication is fully local — no cloud service or iRobot account required.

The protocol was reverse-engineered from the robot's BLE interface.

## Overview

The Braava 240 uses a **two-layer GATT protocol**:

1. **Transport layer** — Manages data transfer via a command/status characteristic pair (cc2/cc3)
2. **Robot command layer** — The actual robot commands are assembled as packets, transferred in 20-byte chunks via a data characteristic (cc1), and then executed

This is NOT a simple write-command/read-notification pattern. Each command goes through a multi-step state machine involving block transfers and explicit status polling.

## BLE Service & Characteristics

### iRobot Service

**Service UUID:** `0bd51777-e7cb-469b-8e4d-2742f1ba77cc`

| Short Name | UUID | Properties | Purpose |
|------------|------|------------|---------|
| cc1 (Data) | `e7add780-b042-4876-aae1-112855353cc1` | Read, Write | Robot command packets in 20-byte chunks. Write uses `WRITE_TYPE_NO_RESPONSE` (1). |
| cc2 (Command) | `e7add780-b042-4876-aae1-112855353cc2` | Write | Transport commands. Write uses `WRITE_TYPE_DEFAULT` (2) with response. |
| cc3 (Status) | `e7add780-b042-4876-aae1-112855353cc3` | Read | Transport status. Read after each cc2 write. **No notifications.** |
| cc4 (Heartbeat) | `e7add780-b042-4876-aae1-112855353cc4` | Notify | Keepalive counter. The **only** characteristic that uses notifications. |

Heartbeat notifications arrive as 4 bytes: a little-endian uint32 counter that increments every ~2.5 s. Example sequence: `15 01 00 00` → `16 01 00 00` → `17 01 00 00` (counter 277, 278, 279).

### GATT Device Information Service (UUID 0x180A)

| Characteristic | UUID |
|----------------|------|
| Model Number | `00002a24-...` |
| Serial Number | `00002a25-...` |
| Firmware Revision | `00002a26-...` |
| Hardware Revision | `00002a27-...` |
| Software Revision | `00002a28-...` |
| Manufacturer Name | `00002a29-...` |

## Transport Layer

### Wire Format

Transport commands are written to **cc2** as a 4-byte little-endian int32:

```
value = (transport_cmd << 24) | (param & 0x00FFFFFF)
```

The response is read from **cc3** as 4 bytes:

```
Bytes 0–2: response_param (24-bit little-endian)
             param = resp[0] | (resp[1] << 8) | (resp[2] << 16)
Byte 3:    status (signed: if >= 128, subtract 256)
```

### Transport Commands

| ID | Name | Parameter | Description |
|----|------|-----------|-------------|
| 1 | RESET_STATE | `0x10000` | Reset the transport state machine. Response param is a firmware identifier (e.g. `0x240614`). |
| 4 | BLOCK_END | block checksum | Signal end of a data block |
| 5 | XFER_END | 0 | Signal end of a data transfer |
| 8 | SEND_CMD | packet size | Execute the transferred robot command |
| 12 | STAGE_DATA | `(offset << 8) \| count` | Stage response data for reading from cc1 |
| 13 | DATA_XFER_START | padded byte count | Begin a data transfer session |
| 14 | DATA_XFER_END | packet size | End a data transfer session |

### Status Codes

| Value | Name | Meaning |
|-------|------|---------|
| 0 | OK | Command succeeded |
| -1 (0xFF) | BUSY | Robot busy — poll cc3 again |
| -2 (0xFE) | IPCPEND | IPC pending — poll cc3 again |
| 3 | BADCMD | Bad command (normal for XFER_END, ignored) |

When status is BUSY or IPCPEND, poll cc3 at 50 ms intervals (max 60 retries ≈ 3 s timeout).

## Command State Machine

The full sequence to send a robot command and read its response:

```
1. RESET_STATE (param=0x10000)
   → Write to cc2, read cc3, check status=OK

2. DATA_XFER_START (param=padded_length)
   → Write to cc2, read cc3, check status=OK

3. Block Transfer
   For each 20-byte chunk of the padded command packet:
     → Write chunk to cc1 (WRITE_TYPE_NO_RESPONSE)
     → Wait 70 ms

4. BLOCK_END (param=block_checksum)
   → Write to cc2, read cc3, check status=OK
   → Wait 500 ms

5. XFER_END (param=0)
   → Write to cc2, read cc3 (status ignored — returns BADCMD)

6. SEND_CMD (param=packet_size)
   → Write to cc2, read cc3, check status=OK
   → Extract response size: response_param & 0xFFFF

7. If response_size > 0: Read Response
   For each 20-byte response chunk:
     → STAGE_DATA (param=(read_offset << 8) | chunk_size)
     → Write to cc2, read cc3, check status=OK
     → Read chunk from cc1

8. DATA_XFER_END (param=packet_size)
   → Write to cc2, read cc3 (status not checked)
```

### Annotated Example: GET_STATUS (0x12)

A complete transport sequence captured from a real device. The robot command packet is `12 03 15` (cmd=0x12, size=3, checksum=0x15), zero-padded to 20 bytes.

```
Step 1 — RESET_STATE
  cc2 ← 00 00 01 01       (cmd=1, param=0x010000)
  cc3 → 14 06 24 00       (param=0x240614, status=0 OK)

Step 2 — DATA_XFER_START (padded_length=20)
  cc2 ← 14 00 00 0d       (cmd=13, param=0x000014=20)
  cc3 → 00 00 00 00       (status=0 OK)

Step 3 — Block transfer (1 chunk × 20 bytes)
  cc1 ← 12 03 15 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00

Step 4 — BLOCK_END (checksum=0x2a)
  cc2 ← 2a 00 00 04       (cmd=4, param=0x00002a=42)
  cc3 → 00 00 00 00       (status=0 OK)
  [500 ms delay]

Step 5 — XFER_END
  cc2 ← 00 00 00 05       (cmd=5, param=0)
  cc3 → 00 00 00 03       (status=3 BADCMD — normal, ignored)

Step 6 — SEND_CMD (packet_size=3)
  cc2 ← 03 00 00 08       (cmd=8, param=0x000003=3)
  cc3 → 06 00 00 00       (param=0x000006=6, status=0 OK)
  → bytes_to_receive = 6

Step 7 — Read response (1 chunk × 6 bytes)
  STAGE_DATA (offset=0, count=6):
  cc2 ← 06 00 00 0c       (cmd=12, param=0x000006)
  cc3 → 00 00 00 00       (status=0 OK)
  cc1 → 12 06 18 00 00 00 (6 bytes read)

Step 8 — DATA_XFER_END
  cc2 ← 03 00 00 0e       (cmd=14, param=0x000003=3)
  cc3 → 00 00 00 00       (status=0 OK)

Result: 12 06 18 00 00 00
  → cmd=0x12, size=6, checksum=0x18
  → payload: 00 00 00 → runtime=0min, state=idle(0), mission=undefined(0)
```

### Timing

The following delays are implementation choices that have proven reliable in practice. They are not mandated by the protocol but help avoid transfer errors.

| Delay | Value | Purpose |
|-------|-------|---------|
| Between data chunks | 70 ms | Chunk transfer pacing |
| Between blocks | 500 ms | Block processing time |
| Busy-wait poll interval | 50 ms | Status polling |
| Max busy retries | 60 | ~3 s timeout |

## Robot Command Packet Format

Robot commands are assembled as byte packets before block transfer:

```
Byte 0:   Command ID (0x00–0x19)
Byte 1:   Total packet size (header + payload)
Byte 2:   Checksum
Bytes 3+: Payload (optional)
```

**Checksum calculation:**

```
checksum = SUM(all bytes except byte[2]) & 0xFF
```

> **Important:** The checksum uses SUM, not XOR.

For commands without payload, the total size is 3 (header only). The packet is zero-padded to the next 20-byte boundary before block transfer. The block checksum (for BLOCK_END) is the 24-bit sum of all padded bytes.

## Robot Commands

### Overview

| ID | Command | Payload | Response | Notes |
|----|---------|---------|----------|-------|
| 0x00 | NOP | — | — | No operation (transport layer test) |
| 0x01 | GET_WETNESS | — | 4 bytes | Query wetness levels for all pad types |
| 0x02 | SET_WETNESS | 2 bytes | — | Set wetness level per pad type |
| 0x03 | GET_VOLUME | — | 1 byte | Query speaker volume |
| 0x04 | SET_VOLUME | 1 byte | — | Set speaker volume |
| 0x05 | SET_NAME | 20 bytes | — | Set robot Bluetooth name |
| 0x06 | GET_BBK_DATA | 1 byte | 46/60/88 bytes | Lifetime statistics (3 groups) |
| 0x07 | GET_ROOM_CONFINE | — | 1 byte | Query room confinement setting |
| 0x08 | SET_ROOM_CONFINE | 1 byte | — | Enable/disable room confinement |
| 0x09 | REMOTE_CONTROL | 1 byte | — | Enable/disable remote control mode |
| 0x0A | JOYSTICK | 4 bytes | — | Manual joystick control (requires REMOTE_CONTROL) |
| 0x0B | SPRAY | 2 bytes | — | Trigger water spray (requires REMOTE_CONTROL) |
| 0x0C | VIBRATE | 1 byte | — | Continuous vibration on/off (requires REMOTE_CONTROL) |
| 0x0D | BEEP | — | — | Audible beep (requires REMOTE_CONTROL) |
| 0x0E | SPOT_CLEAN | — | — | Start spot cleaning (limited area) |
| 0x0F | GET_APP_DATA | — | 8 bytes | Query last mission data |
| 0x10 | START_CLEAN | — | — | Start full-room cleaning |
| 0x11 | STOP_CLEAN | — | — | Stop active cleaning |
| 0x12 | GET_STATUS | — | 3 bytes | Query robot state and mission status |
| 0x13 | GET_BATTERY | — | 11 bytes | Query battery level and voltages |
| 0x14 | GET_PAD_TYPE | — | 1 byte | Query attached pad type |
| 0x15 | POWER_OFF | — | — | Fully shut down the robot |
| 0x16 | GET_ROBOT_REGISTERED | — | 1 byte | Check app registration status |
| 0x17 | SET_ROBOT_REGISTERED | 1 byte | — | Set app registration status |
| 0x18 | GET_NAME | — | 20 bytes | Query robot Bluetooth name |
| 0x19 | FACTORY_RESET | — | — | Factory reset the robot |

### Command Details

#### GET_WETNESS (0x01)

**Response:** 4 bytes

| Offset | Field | Type | Values |
|--------|-------|------|--------|
| 0 | wet_level | byte | 0=Low, 1=Medium, 2=High |
| 1 | damp_level | byte | 0=Low, 1=Medium, 2=High |
| 2 | reusable_wet_level | byte | 0=Low, 1=Medium, 2=High |
| 3 | reusable_damp_level | byte | 0=Low, 1=Medium, 2=High |

Example response: `01 07 10 02 02 02 02`
→ cmd=0x01, size=7, payload: `02 02 02 02` → all levels = High (2)

#### SET_WETNESS (0x02)

**Payload:** 2 bytes

| Offset | Field | Type | Values |
|--------|-------|------|--------|
| 0 | pad_type | byte | 0=wet, 1=damp, 2=reusable_wet, 3=reusable_damp, 127=all |
| 1 | level | byte | 0=Low, 1=Medium, 2=High |

#### GET_VOLUME (0x03)

**Response:** 1 byte

| Offset | Field | Type | Range |
|--------|-------|------|-------|
| 0 | level | byte | 0–100 |

Example response: `03 04 2f 28`
→ cmd=0x03, size=4, payload: `28` → volume = 40

#### SET_VOLUME (0x04)

**Payload:** 1 byte

| Offset | Field | Type | Range |
|--------|-------|------|-------|
| 0 | level | byte | 0–100 |

#### SET_NAME (0x05)

**Payload:** 20 bytes

| Offset | Field | Type | Description |
|--------|-------|------|-------------|
| 0–19 | name | bytes | Null-terminated UTF-8 string, zero-padded to 20 bytes |

#### GET_BBK_DATA (0x06)

Lifetime statistics, queried in 3 groups. The group number is sent as a 1-byte payload.

##### Group 0: Mission Statistics

**Payload:** `[0x00]`
**Response:** 46 bytes — all fields are uint16 little-endian

| Offset | Field | Description |
|--------|-------|-------------|
| 0 | MINUTES | Mission duration |
| 2 | LEFT_WHEEL_CURRENT | Left wheel motor current |
| 4 | RIGHT_WHEEL_CURRENT | Right wheel motor current |
| 6 | PUMP_CURRENT | Water pump current |
| 8 | AGITATOR_CURRENT | Agitator motor current |
| 10 | N_LEFT_OVERCURRENT | Left motor overcurrent events |
| 12 | N_RIGHT_OVERCURRENT | Right motor overcurrent events |
| 14 | N_PUMP_OVERCURRENT | Pump overcurrent events |
| 16 | N_AGITATOR_OVERCURRENT | Agitator overcurrent events |
| 18 | N_STALL_BUMPS | Stall/bump events |
| 20 | N_RAMPS | Ramps detected |
| 22 | N_SLIPS | Slip events |
| 24 | OPERATING_MODE | Operating mode |
| 26 | ROOM_AREA | Room area |
| 28 | VOLUME_PUMPED | Volume of water pumped |
| 30–44 | UNUSED_A..H | Reserved (8 × uint16) |

##### Group 1: Lifetime Statistics (Part 1)

**Payload:** `[0x01]`
**Response:** 60 bytes (Java source) / 86 bytes (observed firmware) — mixed uint16/uint32 little-endian

The first 60 bytes match the Java source layout. The firmware appends additional undocumented bytes which do not affect the known fields.

| Offset | Field | Type | Description |
|--------|-------|------|-------------|
| 0 | N_WET | uint16 | Wet cleaning missions |
| 2 | RUNTIME_WET | uint32 | Wet cleaning total runtime (min) |
| 6 | N_WET_FAILED | uint16 | Failed wet missions |
| 8 | N_DAMP | uint16 | Damp cleaning missions |
| 10 | RUNTIME_DAMP | uint32 | Damp cleaning total runtime (min) |
| 14 | N_DAMP_FAILED | uint16 | Failed damp missions |
| 16 | N_DRY | uint16 | Dry cleaning missions |
| 18 | RUNTIME_DRY | uint32 | Dry cleaning total runtime (min) |
| 22 | N_DRY_FAILED | uint16 | Failed dry missions |
| 24 | N_MICRO | uint16 | Micro cleaning missions |
| 26 | RUNTIME_MICRO | uint32 | Micro cleaning total runtime (min) |
| 30 | N_MICRO_FAILED | uint16 | Failed micro missions |
| 32 | MISSION_AVG_MINUTES | uint16 | Average mission duration |
| 34 | N_RIGHT_BUMPER_CLICKS | uint32 | Right bumper click count |
| 38 | N_LEFT_BUMPER_CLICKS | uint32 | Left bumper click count |
| 42 | N_FRONT_CLIFFS | uint16 | Front cliff detections |
| 44 | N_REAR_CLIFFS | uint16 | Rear cliff detections |
| 46 | N_WHEEL_DROPS | uint16 | Wheel drop events |
| 48 | N_KIDNAPS | uint16 | Robot picked up events |
| 50 | N_OVERCURRENT | uint16 | Motor overcurrent events |
| 52 | N_BATTERY_OVERTEMPS | uint16 | Battery overtemperature events |
| 54 | N_MISSIONS_STARTED | uint16 | Total missions started |
| 56 | N_MISSIONS_COMPLETED | uint16 | Total missions completed |
| 58 | N_MISSIONS_FAILED | uint16 | Total missions failed |
| 60+ | (extra data) | — | Firmware returns 26 extra bytes (undocumented) |

Example response (89 bytes total, truncated):
```
06 59 ae
  05 00              N_WET = 5
  1b 00 00 00        RUNTIME_WET = 27 min
  03 00              N_WET_FAILED = 3
  01 00              N_DAMP = 1
  01 00 00 00        RUNTIME_DAMP = 1 min
  00 00              N_DAMP_FAILED = 0
  00 00              N_DRY = 0
  00 00 00 00        RUNTIME_DRY = 0 min
  00 00              N_DRY_FAILED = 0
  53 01              N_MICRO = 339
  1b 15 00 00        RUNTIME_MICRO = 5403 min
  48 00              N_MICRO_FAILED = 72
  0f 00              MISSION_AVG_MINUTES = 15
  5c 57 00 00        N_RIGHT_BUMPER_CLICKS = 22364
  69 56 00 00        N_LEFT_BUMPER_CLICKS = 22121
  18 03              N_FRONT_CLIFFS = 792
  7f 02              N_REAR_CLIFFS = 639
  0a 00              N_WHEEL_DROPS = 10
  ba 00              N_KIDNAPS = 186
  01 00              N_OVERCURRENT = 1
  01 00              N_BATTERY_OVERTEMPS = 1
  7d 01              N_MISSIONS_STARTED = 381
  0d 01              N_MISSIONS_COMPLETED = 269
  70 00              N_MISSIONS_FAILED = 112
  04 01 04 01 ...    (26 extra bytes, undocumented)
```

##### Group 2: Lifetime Statistics (Part 2)

**Payload:** `[0x02]`
**Response:** 88 bytes (Java source) / 100 bytes (observed firmware) — mixed uint16/uint32 little-endian

> **Firmware discrepancy:** The Braava 240 firmware returns a different field order than the decompiled Java source (ALRobotCommands.java). The Java source places ONTIME_MINUTES at offset 48 and RUNTIME_MINUTES at offset 52. The actual firmware places them at offsets 22 and 26, immediately after N_BLE_CONNECTIONS. The table below shows the **empirically verified** layout.

Verified fields:

| Offset | Field | Type | Description | Verified |
|--------|-------|------|-------------|----------|
| 0–18 | PANIC_ID_0..9 | 10 × uint16 | Last 10 panic error codes | Yes |
| 20 | N_BLE_CONNECTIONS | uint16 | Total BLE connections | Yes |
| 22 | ONTIME_MINUTES | uint32 | Total power-on time (min) | Yes |
| 26 | RUNTIME_MINUTES | uint32 | Total cleaning runtime (min) | Yes |
| 30–99 | (remaining fields) | mixed | Layout differs from Java source — not individually verified | No |

For reference, the Java source describes these additional fields (at different offsets than actual firmware): N_ATTEMPTED_UPDATES, N_VIRTUAL_WALL_MODE, PAUSE_ID_0..9, AMT_RESULTS, N_PAD_WARNINGS, N_PAD_ERRORS.

Example response (103 bytes total):
```
06 67 0d
  22 01              PANIC_ID_0 = 290
  22 01              PANIC_ID_1 = 290
  22 01 22 01 22 01  PANIC_ID_2..4 = 290
  22 01 22 01 22 01  PANIC_ID_5..7 = 290
  22 01              PANIC_ID_8 = 290
  13 00              PANIC_ID_9 = 19
  11 00              N_BLE_CONNECTIONS = 17
  d5 19 00 00        ONTIME_MINUTES = 6613 (~110.2 h)
  37 15 00 00        RUNTIME_MINUTES = 5431 (~90.5 h)
  00 00 00 00 26 00  (remaining fields...)
  05 33 03 33 ...
```

#### GET_ROOM_CONFINE (0x07)

**Response:** 1 byte

| Offset | Field | Values |
|--------|-------|--------|
| 0 | enabled | 0=off, 1=on |

Example response: `07 04 0b 00`
→ cmd=0x07, size=4, payload: `00` → room confinement off

#### SET_ROOM_CONFINE (0x08)

**Payload:** 1 byte

| Offset | Field | Type | Values |
|--------|-------|------|--------|
| 0 | enabled | byte | 0=off, 1=on |

#### REMOTE_CONTROL (0x09)

**Payload:** 1 byte

| Offset | Field | Type | Values |
|--------|-------|------|--------|
| 0 | enabled | byte | 0=off, 1=on |

Must be enabled before sending JOYSTICK, SPRAY, VIBRATE, or BEEP. Always disable after use.

#### JOYSTICK (0x0A)

**Payload:** 4 bytes

| Offset | Field | Type | Description |
|--------|-------|------|-------------|
| 0 | x | int16 LE | X-axis position (signed) |
| 2 | y | int16 LE | Y-axis position (signed) |

Known range: min=19000, max=46535.

Requires REMOTE_CONTROL mode.

#### SPRAY (0x0B)

**Payload:** 2 bytes

| Offset | Field | Type | Description |
|--------|-------|------|-------------|
| 0 | duration | uint16 LE | Spray duration in milliseconds |

Requires REMOTE_CONTROL mode.

#### VIBRATE (0x0C)

**Payload:** 1 byte

| Offset | Field | Type | Values |
|--------|-------|------|--------|
| 0 | enabled | byte | 0=off, 1=on |

Enables **continuous vibration** (not a toggle setting). Not persistent across power cycles. Requires REMOTE_CONTROL mode.

#### BEEP (0x0D)

No payload. Triggers an audible beep. Requires REMOTE_CONTROL mode.

#### SPOT_CLEAN (0x0E)

No payload. Starts a spot cleaning mission (limited area).

#### GET_APP_DATA (0x0F)

**Response:** 8 bytes

| Offset | Field | Type | Description |
|--------|-------|------|-------------|
| 0 | mission_number | byte | Mission identifier |
| 1 | runtime | byte | Mission runtime |
| 2 | pad_spot_virtual_bits | byte | Bitfield: pad type, spot mode, virtual wall flags |
| 3 | ending_reason | byte | Reason the mission ended |
| 4 | room_size | uint16 LE | Room area |
| 6 | unused | uint16 LE | Reserved |

#### START_CLEAN (0x10)

No payload. Starts a full-room cleaning mission.

#### STOP_CLEAN (0x11)

No payload. Stops the active cleaning mission.

#### GET_STATUS (0x12)

**Response:** 3 bytes

| Offset | Field | Type | Description |
|--------|-------|------|-------------|
| 0 | runtime | byte | Mission runtime in minutes |
| 1 | robot_state | byte | 0=idle, 1=cleaning, 2=success, 3=error |
| 2 | mission_status | byte | Mission status code (0–34) |

Robot state mapping:
- `0` → idle
- `1` → cleaning (mission in progress)
- `2` → idle (mission ended successfully, returned to home)
- `3` → error (mission ended with error — but see quirks)

Example response: `12 06 18 00 00 00`
→ cmd=0x12, size=6, payload: `00 00 00` → runtime=0 min, state=idle(0), mission=undefined(0)

#### GET_BATTERY (0x13)

**Response:** 11 bytes

| Offset | Field | Type | Description |
|--------|-------|------|-------------|
| 0 | level | byte | Battery percentage (unreliable — see quirks) |
| 1 | min_voltage | uint16 LE | Minimum voltage (mV) |
| 3 | max_voltage | uint16 LE | Maximum voltage (mV) |
| 5 | current_voltage | uint16 LE | Current voltage (mV) |
| 7 | max_charge | uint16 LE | Maximum charge capacity (mAh) |
| 9 | current_charge | uint16 LE | Current charge level (mAh) |

Divide voltage values by 1000 for volts. Calculate reliable battery percentage as:
```
battery_pct = min(100, round(current_charge / max_charge * 100))
```

Example response: `13 0e ec 03 80 0c 04 10 d5 0d 34 08 06 04`
→ cmd=0x13, size=14, payload (11 bytes):
  - level=3 (unreliable), min_v=0x0C80=3200 mV, max_v=0x1004=4100 mV
  - cur_v=0x0DD5=3541 mV (3.54 V), max_chg=0x0834=2100 mAh
  - cur_chg=0x0406=1030 mAh → battery = 1030/2100 = 49%

#### GET_PAD_TYPE (0x14)

**Response:** 1 byte

| Value | Pad Type |
|-------|----------|
| 0 | Invalid |
| 1 | Damp |
| 2 | Dry |
| 3 | Wet |
| 4 | Reusable Damp |
| 5 | Reusable Dry |
| 6 | Reusable Wet |
| 7 | Plate |
| 8 | All |
| 9 | No Pad |

Example response: `14 04 19 01`
→ cmd=0x14, size=4, payload: `01` → pad_type = Damp (1)

#### POWER_OFF (0x15)

No payload. Fully shuts down the robot.

#### GET_ROBOT_REGISTERED (0x16)

**Response:** 1 byte

| Offset | Field | Values |
|--------|-------|--------|
| 0 | registered | 0=not registered, 1=registered |

Internal registration status. No practical use for third-party integrations.

#### SET_ROBOT_REGISTERED (0x17)

**Payload:** 1 byte

| Offset | Field | Type | Values |
|--------|-------|------|--------|
| 0 | registered | byte | 0=unregister, 1=register |

#### GET_NAME (0x18)

**Response:** 20 bytes — null-terminated UTF-8 string, zero-padded

Example response: `18 17 96 57 69 73 63 68 69 00 00 00 00 00 00 00 00 00 00 00 00 00 00`
→ cmd=0x18, size=23, payload: `57 69 73 63 68 69 00 ...` → "Wischi"

#### FACTORY_RESET (0x19)

No payload. Performs a full factory reset.

## Known Quirks

| Issue | Behavior | Workaround |
|-------|----------|------------|
| Battery level byte unreliable | `level` (byte 0 of GET_BATTERY) always reports 5 | Calculate from `current_charge / max_charge` instead |
| XFER_END returns BADCMD | Status=3 is the normal response to XFER_END | Ignore the status code |
| User-initiated stop looks like error | After STOP_CLEAN: robot_state=3 (error) + mission_status=6 or 34 | Map this combination to idle, not error |
| Command serialization required | Concurrent BLE commands interleave and corrupt | Serialize all commands with a lock (asyncio.Lock) |
| VIBRATE is continuous | VIBRATE(1) starts non-stop vibration, not a momentary pulse | Not useful as a toggle setting |
| cc3 has no notifications | BlueZ correctly rejects start_notify on read-only characteristic | Always use explicit read after cc2 write |
| cc4 is the only notifier | Heartbeat keepalive counter; no data payloads | Subscribe for connection keepalive only |
| REMOTE_CONTROL mode | JOYSTICK, SPRAY, VIBRATE, BEEP require it | Enable before, disable after (try/finally) |
| BBK Group 2 field order | ONTIME/RUNTIME at firmware offsets 22/26, not 48/52 as in Java source | Use empirically verified offsets (see GET_BBK_DATA section) |
| BBK response sizes | Group 1: 86 bytes payload (not 60), Group 2: 100 bytes (not 88) | Extra bytes appended; parse only known offsets |

## Connection Timing

Observed timing from a real BLE connection at RSSI -78 dBm:

| Phase | Duration | Description |
|-------|----------|-------------|
| BLE connect | ~4.7 s | GATT connection establishment |
| Settle delay | 1.0 s | Wait for robot to be ready |
| Each robot command | ~2.0 s | Full transport cycle (8 steps) |
| Heartbeat interval | ~2.5 s | Notifications on cc4 |

A full initial sequence (connect + 9 commands) takes approximately 21 seconds. To reduce time-to-first-data, poll status/battery first before reading infrequent data (name, lifetime statistics).

## Source References

The protocol was reverse-engineered from the robot's BLE interface.
