"""BLE packet parser for the iRobot Braava 240.

Protocol notes (from ALRobotCommands.java / ALBtCmdDefs.java):

Robot command packet format:
    Byte 0: command ID
    Byte 1: total message size (header + payload)
    Byte 2: checksum = sum(all bytes except byte[2]) & 0xFF
    Byte 3+: payload (if any)

For no-payload commands total_size = 3 (just the 3-byte header).

These robot command packets are NOT written directly to BLE characteristics.
They are transferred via the transport layer (see coordinator.py).

Response packets received from the robot follow the same format.
"""

import logging

from .const import (
    CMD_GET_BATTERY,
    CMD_GET_NAME,
    CMD_GET_PAD_TYPE,
    CMD_GET_ROOM_CONFINE,
    CMD_GET_STATUS,
    CMD_GET_VOLUME,
    CMD_GET_WETNESS,
    CMD_HEADER_SIZE,
    DATA_CHAR_CHUNK_SIZE,
    MISSION_STATUS_MAP,
    PAD_TYPE_MAP,
    ROBOT_STATE_MAP,
    ROBOT_STATE_MISSION_ERROR,
    USER_STOP_MISSIONS,
    WETNESS_LEVEL_MAP,
)

_LOGGER = logging.getLogger(__name__)


# ── Command building ──────────────────────────────────────────────────────────

def build_robot_packet(cmd_id: int, payload: bytes = b"") -> bytes:
    """Build a robot command packet: [cmd_id, size, checksum, payload...].

    checksum = sum(all bytes except byte[2]) & 0xFF.
    Returns the raw packet (NOT padded).
    """
    size = CMD_HEADER_SIZE + len(payload)
    packet = bytearray([cmd_id, size, 0]) + bytearray(payload)
    # Checksum: sum of all bytes except index 2
    chk = sum(packet[i] for i in range(len(packet)) if i != 2) & 0xFF
    packet[2] = chk
    return bytes(packet)


def pad_to_chunk_boundary(data: bytes) -> bytes:
    """Pad data to the next 20-byte boundary (BLE data char chunk size)."""
    remainder = len(data) % DATA_CHAR_CHUNK_SIZE
    if remainder == 0:
        return data
    return data + b"\x00" * (DATA_CHAR_CHUNK_SIZE - remainder)


# ── Response parsing ──────────────────────────────────────────────────────────

def parse_response(data: bytes) -> dict | None:
    """Parse a robot command response received via the transport layer.

    The response has the standard packet format:
        [cmd_id, total_size, checksum, payload...]
    where total_size == len(data).

    Returns a dict with a "type" key ("status" or "battery"), or None.
    """
    if not data or len(data) < CMD_HEADER_SIZE:
        _LOGGER.debug("Response too short: %d bytes", len(data) if data else 0)
        return None

    cmd = data[0]
    size = data[1]

    # Validate size matches actual data length
    if size != len(data):
        _LOGGER.debug(
            "Response size mismatch: header says %d, got %d bytes: %s",
            size, len(data), data.hex(" "),
        )

    # Validate checksum
    expected_chk = sum(data[i] for i in range(len(data)) if i != 2) & 0xFF
    if data[2] != expected_chk:
        _LOGGER.warning(
            "Response checksum mismatch: got 0x%02x, expected 0x%02x for: %s",
            data[2], expected_chk, data.hex(" "),
        )

    # Payload starts after the 3-byte header
    payload_offset = CMD_HEADER_SIZE

    if cmd == CMD_GET_STATUS:
        return _parse_status(data, payload_offset)
    if cmd == CMD_GET_BATTERY:
        return _parse_battery(data, payload_offset)
    if cmd == CMD_GET_PAD_TYPE:
        return _parse_pad_type(data, payload_offset)
    if cmd == CMD_GET_VOLUME:
        return _parse_volume(data, payload_offset)
    if cmd == CMD_GET_WETNESS:
        return _parse_wetness(data, payload_offset)
    if cmd == CMD_GET_NAME:
        return _parse_name(data, payload_offset)
    if cmd == CMD_GET_ROOM_CONFINE:
        return _parse_room_confine(data, payload_offset)

    _LOGGER.debug(
        "Unknown response CMD 0x%02x (%d bytes): %s",
        cmd, len(data), data.hex(" "),
    )
    return None


def _parse_status(data: bytes, offset: int) -> dict | None:
    """Parse GET_STATUS response payload (3 bytes).

    Payload layout (from ALRobotCommands.java GET_STATUS_RESPONSE):
        Byte 0: runtime     – mission runtime in minutes
        Byte 1: robot_state – BraavaRobotState enum value (0–3)
        Byte 2: mission_status – BraavaMissionStatus enum value (0–29)
    """
    if len(data) < offset + 3:
        _LOGGER.debug(
            "GET_STATUS response too short: %d bytes (need offset %d + 3)",
            len(data), offset,
        )
        return None

    runtime        = data[offset]
    robot_state    = data[offset + 1]
    mission_status = data[offset + 2]

    state_str  = ROBOT_STATE_MAP.get(robot_state, f"unknown_{robot_state}")
    status_str = MISSION_STATUS_MAP.get(mission_status, f"unknown_{mission_status}")

    # User-initiated stop reports state=error(3) + terminated mission status.
    # This isn't a real error – the robot just stopped. Treat as idle.
    if robot_state == ROBOT_STATE_MISSION_ERROR and mission_status in USER_STOP_MISSIONS:
        robot_state = 0
        state_str = "idle"

    _LOGGER.debug(
        "Status: runtime=%dmin state=%s(%d) mission=%s(%d)",
        runtime, state_str, robot_state, status_str, mission_status,
    )

    return {
        "type":             "status",
        "runtime_minutes":  runtime,
        "robot_state":      robot_state,
        "robot_state_str":  state_str,
        "mission_status":   mission_status,
        "mission_status_str": status_str,
    }


def _parse_battery(data: bytes, offset: int) -> dict | None:
    """Parse GET_BATTERY response payload (11 bytes).

    Payload layout (from ALRobotCommands.java GET_BATTERY_RESPONSE):
        Byte 0:     level          – battery percentage (0–100)
        Bytes 1-2:  min_voltage    – uint16 LE, mV
        Bytes 3-4:  max_voltage    – uint16 LE, mV
        Bytes 5-6:  current_voltage – uint16 LE, mV
        Bytes 7-8:  max_charge     – uint16 LE, mAh
        Bytes 9-10: current_charge – uint16 LE, mAh
    """
    if len(data) < offset + 1:
        _LOGGER.debug("GET_BATTERY response too short: %d bytes", len(data))
        return None

    raw_level = data[offset]
    result: dict = {"type": "battery", "battery_level": raw_level}

    if len(data) >= offset + 7:
        result["min_voltage"]      = int.from_bytes(data[offset + 1: offset + 3], "little") / 1000.0
        result["max_voltage"]      = int.from_bytes(data[offset + 3: offset + 5], "little") / 1000.0
        result["current_voltage"]  = int.from_bytes(data[offset + 5: offset + 7], "little") / 1000.0

    if len(data) >= offset + 11:
        result["max_charge"]     = int.from_bytes(data[offset + 7: offset + 9],  "little")
        result["current_charge"] = int.from_bytes(data[offset + 9: offset + 11], "little")

    # The raw level byte from the robot is unreliable (often reports 5 regardless
    # of actual charge).  Compute battery percentage from charge values instead.
    max_chg = result.get("max_charge", 0)
    cur_chg = result.get("current_charge", 0)
    if max_chg > 0 and cur_chg >= 0:
        result["battery_level"] = min(100, round(cur_chg / max_chg * 100))

    _LOGGER.debug(
        "Battery: %d%% (%.2fV) [raw_level=%d, charge=%d/%d mAh]",
        result["battery_level"],
        result.get("current_voltage", 0.0),
        raw_level,
        cur_chg,
        max_chg,
    )
    return result


def _parse_pad_type(data: bytes, offset: int) -> dict | None:
    """Parse GET_PAD_TYPE response payload (1 byte).

    Payload layout (from ALRobotCommands.java GET_PAD_TYPE_RESPONSE):
        Byte 0: pad_type – RobotPadCategory enum value (0–9)
    """
    if len(data) < offset + 1:
        _LOGGER.debug("GET_PAD_TYPE response too short: %d bytes", len(data))
        return None

    pad_type = data[offset]
    pad_str = PAD_TYPE_MAP.get(pad_type, f"unknown_{pad_type}")

    _LOGGER.debug("Pad type: %s(%d)", pad_str, pad_type)

    return {
        "type":         "pad_type",
        "pad_type":     pad_type,
        "pad_type_str": pad_str,
    }


def _parse_volume(data: bytes, offset: int) -> dict | None:
    """Parse GET_VOLUME response payload (1 byte).

    Payload layout (from ALRobotCommands.java GET_VOLUME_RESPONSE):
        Byte 0: level – volume level
    """
    if len(data) < offset + 1:
        _LOGGER.debug("GET_VOLUME response too short: %d bytes", len(data))
        return None

    level = data[offset]
    _LOGGER.debug("Volume: %d", level)

    return {
        "type":   "volume",
        "volume": level,
    }


def _parse_wetness(data: bytes, offset: int) -> dict | None:
    """Parse GET_WETNESS response payload (4 bytes).

    Payload layout (from ALRobotCommands.java GET_WETNESS_RESPONSE):
        Byte 0: wet_level           – disposable wet pad
        Byte 1: damp_level          – disposable damp pad
        Byte 2: reusable_wet_level  – reusable wet pad
        Byte 3: reusable_damp_level – reusable damp pad
    """
    if len(data) < offset + 4:
        _LOGGER.debug("GET_WETNESS response too short: %d bytes", len(data))
        return None

    wet   = data[offset]
    damp  = data[offset + 1]
    r_wet = data[offset + 2]
    r_damp = data[offset + 3]

    _LOGGER.debug(
        "Wetness: wet=%d damp=%d reusable_wet=%d reusable_damp=%d",
        wet, damp, r_wet, r_damp,
    )

    return {
        "type": "wetness",
        "wetness_wet":            wet,
        "wetness_damp":           damp,
        "wetness_reusable_wet":   r_wet,
        "wetness_reusable_damp":  r_damp,
        "wetness_wet_str":           WETNESS_LEVEL_MAP.get(wet, f"unknown_{wet}"),
        "wetness_damp_str":          WETNESS_LEVEL_MAP.get(damp, f"unknown_{damp}"),
        "wetness_reusable_wet_str":  WETNESS_LEVEL_MAP.get(r_wet, f"unknown_{r_wet}"),
        "wetness_reusable_damp_str": WETNESS_LEVEL_MAP.get(r_damp, f"unknown_{r_damp}"),
    }


def _parse_name(data: bytes, offset: int) -> dict | None:
    """Parse GET_NAME response payload (up to 20 bytes).

    Payload layout (from ALRobotCommands.java GET_NAME_RESPONSE):
        Bytes 0-19: robot name as null-terminated UTF-8 string
    """
    if len(data) < offset + 1:
        _LOGGER.debug("GET_NAME response too short: %d bytes", len(data))
        return None

    raw = data[offset:]
    # Strip null terminator and trailing padding
    name = bytes(raw).split(b"\x00", 1)[0].decode("utf-8", errors="replace")

    _LOGGER.debug("Name: %s", name)

    return {
        "type": "name",
        "robot_name": name,
    }


def _parse_room_confine(data: bytes, offset: int) -> dict | None:
    """Parse GET_ROOM_CONFINE response payload (1 byte).

    Payload layout (from ALRobotCommands.java GET_ROOM_CONFINE_RESPONSE):
        Byte 0: confinement – 0=off, 1=on
    """
    if len(data) < offset + 1:
        _LOGGER.debug("GET_ROOM_CONFINE response too short: %d bytes", len(data))
        return None

    value = data[offset]
    _LOGGER.debug("Room confine: %d", value)

    return {
        "type": "room_confine",
        "room_confine": bool(value),
    }


def parse_bbk_life1(data: bytes, offset: int = CMD_HEADER_SIZE) -> dict | None:
    """Parse GET_BBK_DATA group 1 response (60 bytes payload).

    Lifetime statistics part 1 — mixed uint16/uint32 LE fields.
    We extract only the fields needed for sensors.

    Offset 32: MISSION_AVG_MINUTES (uint16)
    Offset 54: N_MISSIONS_STARTED  (uint16)
    Offset 56: N_MISSIONS_COMPLETED (uint16)
    Offset 58: N_MISSIONS_FAILED   (uint16)
    """
    if len(data) < offset + 60:
        _LOGGER.debug("BBK life1 response too short: %d bytes", len(data))
        return None

    avg_min   = int.from_bytes(data[offset + 32: offset + 34], "little")
    started   = int.from_bytes(data[offset + 54: offset + 56], "little")
    completed = int.from_bytes(data[offset + 56: offset + 58], "little")
    failed    = int.from_bytes(data[offset + 58: offset + 60], "little")

    _LOGGER.debug(
        "BBK life1: missions=%d/%d/%d avg=%dmin",
        started, completed, failed, avg_min,
    )

    return {
        "type": "bbk_life1",
        "total_missions": started,
        "successful_missions": completed,
        "failed_missions": failed,
        "average_mission_minutes": avg_min,
    }


def parse_bbk_life2(data: bytes, offset: int = CMD_HEADER_SIZE) -> dict | None:
    """Parse GET_BBK_DATA group 2 response.

    Lifetime statistics part 2 — mixed uint16/uint32 LE fields.
    We extract only the fields needed for sensors.

    The Braava 240 firmware returns a 100-byte payload (103 total) rather
    than the 88 bytes described in the Java source.  The field order also
    differs: ONTIME and RUNTIME appear right after N_BLE_CONNECTIONS
    instead of after AMT_RESULTS.

    Payload layout (empirically verified):
        Offset  0: PANIC_ID_0..9        (10 × uint16 = 20 bytes)
        Offset 20: N_BLE_CONNECTIONS     (uint16)
        Offset 22: ONTIME_MINUTES        (uint32)
        Offset 26: RUNTIME_MINUTES       (uint32)
    """
    if len(data) < offset + 30:
        _LOGGER.debug("BBK life2 response too short: %d bytes", len(data))
        return None

    ontime  = int.from_bytes(data[offset + 22: offset + 26], "little")
    runtime = int.from_bytes(data[offset + 26: offset + 30], "little")

    _LOGGER.debug("BBK life2: ontime=%dmin runtime=%dmin", ontime, runtime)

    return {
        "type": "bbk_life2",
        "total_ontime_minutes": ontime,
        "total_cleaning_minutes": runtime,
    }
