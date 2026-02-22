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
    CMD_GET_PAD_TYPE,
    CMD_GET_STATUS,
    CMD_HEADER_SIZE,
    DATA_CHAR_CHUNK_SIZE,
    MISSION_STATUS_MAP,
    PAD_TYPE_MAP,
    ROBOT_STATE_MAP,
    ROBOT_STATE_MISSION_ERROR,
    USER_STOP_MISSIONS,
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
