"""Constants for the iRobot Braava 240 BLE integration.

The Braava 240 uses a custom BLE GATT protocol with two layers:

  Transport layer (cc2/cc3):
      4-byte commands written to cc2, 4-byte status read from cc3.
      Format: little-endian int32 = (transport_cmd << 24) | (param & 0x00FFFFFF)

  Robot command layer (cc1):
      Robot commands [cmd_id, size, checksum, payload...] are transferred via cc1
      using a block-transfer protocol orchestrated by transport commands.
"""

DOMAIN = "braava_240_ble"

# ── BLE UUIDs ──────────────────────────────────────────────────────────────────
# iRobot Braava 240 service UUID
SERVICE_UUID = "0bd51777-e7cb-469b-8e4d-2742f1ba77cc"

# Four GATT characteristics within the service:
CHAR_UUID_DATA      = "e7add780-b042-4876-aae1-112855353cc1"  # Data transfers (cc1)
CHAR_UUID_COMMAND   = "e7add780-b042-4876-aae1-112855353cc2"  # Transport commands (cc2)
CHAR_UUID_STATUS    = "e7add780-b042-4876-aae1-112855353cc3"  # Transport status reads (cc3)
CHAR_UUID_HEARTBEAT = "e7add780-b042-4876-aae1-112855353cc4"  # Heartbeat notifications (cc4)

# ── Transport-layer commands ──────────────────────────────────────────────────
# Written to cc2 as 4-byte packets; status read back from cc3.
TCMD_SET_CLIENT_OS   = 0   # Client platform (param 1 = iOS, 2 = Android), sent once after connecting
TCMD_CLIENT_ANDROID  = 2
TCMD_RESET_STATE     = 1   # Reset transport state machine
TCMD_BLOCK_END       = 4   # End of a data block (param = checksum)
TCMD_GET_SW_INFO     = 10  # Robot software version: param = major<<16 | minor<<8 | image id
TCMD_XFER_END        = 5   # End of data transfer
TCMD_SEND_CMD        = 8   # Execute the transferred robot command
TCMD_STAGE_DATA      = 12  # Stage response data for reading (param = addr<<8 | count)
TCMD_DATA_XFER_START = 13  # Start a data transfer (param = total byte count)
TCMD_DATA_XFER_END   = 14  # Finish data transfer session

# Transport status codes
TSTATUS_OK      =  0
TSTATUS_BUSY    = -1   # 0xFF unsigned – keep polling cc3
TSTATUS_IPCPEND = -2   # 0xFE unsigned – keep polling cc3

# ── Robot command IDs ─────────────────────────────────────────────────────────
# These are packed into command packets and sent via the transport layer.
CMD_NOP            = 0x00
CMD_GET_WETNESS    = 0x01  # Query wetness levels for all pad types (4-byte response)
CMD_SET_WETNESS    = 0x02  # Set wetness level (payload: [type, level])
CMD_GET_VOLUME     = 0x03  # Query current volume level (1-byte response)
CMD_SET_VOLUME     = 0x04  # Set volume level (1-byte payload)
CMD_SET_NAME       = 0x05  # Set robot name (payload: 20-byte null-terminated string)
CMD_GET_BBK_DATA   = 0x06  # Lifetime statistics (payload: group 0/1/2, response: 46/60/88 bytes)
CMD_GET_ROOM_CONFINE = 0x07  # Query room confinement setting (1-byte response: 0/1)
CMD_SET_ROOM_CONFINE = 0x08  # Set room confinement (payload: 0=off, 1=on)
CMD_REMOTE_CONTROL = 0x09  # Enable/disable remote control mode (payload: 1=on, 0=off)
CMD_VIBRATE        = 0x0C  # Enable/disable vibration (payload: 0=off, 1=on, not persistent) – unused: triggers continuous vibration, requires remote control mode
CMD_BEEP           = 0x0D  # Trigger an audible beep (requires remote control mode)
CMD_SPOT_CLEAN     = 0x0E  # Start spot cleaning (limited area)
CMD_START_CLEAN    = 0x10  # Initiates a full-room cleaning mission
CMD_STOP_CLEAN   = 0x11  # Terminates an active cleaning mission
CMD_GET_STATUS   = 0x12  # Query robot state + mission status
CMD_GET_BATTERY  = 0x13  # Query battery level and voltages
CMD_GET_PAD_TYPE = 0x14  # Query attached cleaning pad type
CMD_POWER_OFF    = 0x15  # Power off the robot (ROBOT_CMD_OFF)
CMD_GET_NAME     = 0x18  # Query robot name (20-byte null-terminated string response)

# ── Protocol message structure ────────────────────────────────────────────────
# Robot command format: [cmd_id, total_size, checksum, payload...]
# checksum = sum(all_bytes_except_byte[2]) & 0xFF
# total_size for no-payload commands = 3 (the 3-byte header itself).
CMD_HEADER_SIZE = 3
DATA_CHAR_CHUNK_SIZE = 20  # BLE data characteristic transfer chunk size

# ── Robot state ───────────────────────────────────────────────────────────────
ROBOT_STATE_IDLE                = 0  # Idle
ROBOT_STATE_MISSION_IN_PROGRESS = 1  # MissionInProgress
ROBOT_STATE_MISSION_SUCCESS     = 2  # MissionEndedSuccessfully
ROBOT_STATE_MISSION_ERROR       = 3  # MissionEndedWithError

ROBOT_STATE_MAP: dict[int, str] = {
    ROBOT_STATE_IDLE:                "idle",
    ROBOT_STATE_MISSION_IN_PROGRESS: "cleaning",
    ROBOT_STATE_MISSION_SUCCESS:     "idle",   # Back at home after success
    ROBOT_STATE_MISSION_ERROR:       "error",
}

# ── Mission status – selected values ──────────────────────────────────────────
MISSION_STATUS_UNDEFINED            = 0
MISSION_STATUS_IN_PROGRESS          = 1
MISSION_STATUS_DEAD_BATTERY         = 3
MISSION_STATUS_SUCCESS              = 17
MISSION_STATUS_START_REFUSED_INVALID_PAD = 21
MISSION_STATUS_LOW_BATTERY          = 29
MISSION_STATUS_TERMINATED_BY_USER   = 34
MISSION_STATUS_TERMINATED_BY_ROBOT  = 36
MISSION_STATUS_STUCK                = 42

# Raw mission status byte of GET_STATUS → name. The codes are not contiguous
# (5–15, 22–27 and 35 are not used).
MISSION_STATUS_MAP: dict[int, str] = {
    0:  "undefined",
    1:  "in_progress",
    2:  "robot_reset",
    3:  "dead_battery",
    4:  "battery_over_temp",
    16: "unknown_cleaning_result",
    17: "success",
    18: "unhandled_failure",
    19: "start_refused_cliff",
    20: "start_refused_bumped",
    21: "start_refused_invalid_pad",
    28: "start_refused_gyro_cal",
    29: "low_battery",
    30: "timed_out",
    31: "watchdog_timed_out",
    32: "failed_to_relocalize",
    33: "failed_to_go_home",
    34: "terminated_by_user",
    36: "terminated_by_robot",
    37: "max_coverage",
    38: "pad_id_error",
    39: "kidnap",
    40: "constant_cliff",
    41: "wheel_drop",
    42: "stuck",
    43: "trapped",
    44: "high_motor_current",
    45: "failed_to_make_progress",
    46: "out_of_bounds",
    47: "gyro_hardware_failure",
}

# Mission statuses that mean a pad problem (the pad type is read again)
PAD_ERROR_MISSIONS = {MISSION_STATUS_START_REFUSED_INVALID_PAD, 38}

# Mission ends that are not errors, although the robot may report them with
# robot_state=error(3): a stop by the user or by the robot, and a job that
# ended because the area was too large or the way home was not found.
NON_ERROR_END_MISSIONS = {
    MISSION_STATUS_TERMINATED_BY_USER,
    MISSION_STATUS_TERMINATED_BY_ROBOT,
    33,  # failed_to_go_home
    37,  # max_coverage
}

# ── Pad types ────────────────────────────────────────────────────────────
# Raw pad type byte of GET_PAD_TYPE → name. 6 is not used; 3 and 4 are both
# dry pads (verified with a dry pad: the robot reports 4).
PAD_TYPE_MAP: dict[int, str] = {
    0: "no_pad",
    1: "reusable_wet",
    2: "wet",
    3: "dry",
    4: "dry",
    5: "damp",
    7: "reusable_damp",
    8: "reusable_dry",
}

# ── Wetness levels ──────────────────────────────────────────────────────────
WETNESS_LEVEL_LOW      = 0  # Damp
WETNESS_LEVEL_MEDIUM   = 1  # Moderate
WETNESS_LEVEL_HIGH     = 2  # Wet

WETNESS_LEVEL_MAP: dict[int, str] = {
    WETNESS_LEVEL_LOW:    "low",
    WETNESS_LEVEL_MEDIUM: "medium",
    WETNESS_LEVEL_HIGH:   "high",
}

WETNESS_LEVEL_REVERSE: dict[str, int] = {v: k for k, v in WETNESS_LEVEL_MAP.items()}

# Wetness pad type selectors for SET_WETNESS command
WETNESS_TYPE_WET            = 0    # Disposable wet pad
WETNESS_TYPE_DAMP           = 1    # Disposable damp pad
WETNESS_TYPE_REUSABLE_WET   = 2    # Reusable wet pad
WETNESS_TYPE_REUSABLE_DAMP  = 3    # Reusable damp pad
WETNESS_TYPE_ALL            = 127  # All pad types

# Factory default wetness levels
WETNESS_DEFAULTS: dict[int, int] = {
    WETNESS_TYPE_WET:           WETNESS_LEVEL_MEDIUM,
    WETNESS_TYPE_DAMP:          WETNESS_LEVEL_MEDIUM,
    WETNESS_TYPE_REUSABLE_WET:  WETNESS_LEVEL_MEDIUM,
    WETNESS_TYPE_REUSABLE_DAMP: WETNESS_LEVEL_MEDIUM,
}

# ── Standard BLE GATT Device Information Service (UUID 0x180A) ────────────
GATT_SERIAL_NUMBER    = "00002a25-0000-1000-8000-00805f9b34fb"
GATT_FIRMWARE_REV     = "00002a26-0000-1000-8000-00805f9b34fb"
GATT_HARDWARE_REV     = "00002a27-0000-1000-8000-00805f9b34fb"
GATT_SOFTWARE_REV     = "00002a28-0000-1000-8000-00805f9b34fb"
GATT_MANUFACTURER     = "00002a29-0000-1000-8000-00805f9b34fb"
GATT_MODEL_NUMBER     = "00002a24-0000-1000-8000-00805f9b34fb"

# ── Device identification ─────────────────────────────────────────────────────
# iRobot manufacturer ID in BLE advertisement (0x0600 = 1536)
IROBOT_MANUFACTURER_ID = 1536

# Name prefixes the Braava 240 uses when advertising
BRAAVA_NAME_PREFIXES = ("Altadena", "iRobot Braav")

# ── Polling ───────────────────────────────────────────────────────────────────
# Polling cadence: the mission status every 5 s in every state, the pad type
# every 30 s, the battery every 60 s. Wetness, volume and room confinement are
# read once after connecting (and after we set them).
POLL_INTERVAL_STATUS  = 5   # seconds
POLL_INTERVAL_PAD     = 30  # seconds
POLL_INTERVAL_BATTERY = 60  # seconds
