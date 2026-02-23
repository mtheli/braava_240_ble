"""Constants for the iRobot Braava 240 BLE integration.

Protocol reverse-engineered from iRobot Home Android app (com.irobot.home v7.13.2).
The Braava 240 (codename "Altadena") uses a custom BLE GATT protocol with two layers:

  Transport layer (cc2/cc3):
      4-byte commands written to cc2, 4-byte status read from cc3.
      Format: little-endian int32 = (transport_cmd << 24) | (param & 0x00FFFFFF)

  Robot command layer (cc1):
      Robot commands [cmd_id, size, checksum, payload...] are transferred via cc1
      using a block-transfer protocol orchestrated by transport commands.
"""

DOMAIN = "braava_240_ble"

# ── BLE UUIDs ──────────────────────────────────────────────────────────────────
# iRobot Braava 240 service UUID (from ALBluetooth.java / BleAttributes.java)
SERVICE_UUID = "0bd51777-e7cb-469b-8e4d-2742f1ba77cc"

# Four GATT characteristics within the service:
CHAR_UUID_DATA      = "e7add780-b042-4876-aae1-112855353cc1"  # Data transfers (cc1)
CHAR_UUID_COMMAND   = "e7add780-b042-4876-aae1-112855353cc2"  # Transport commands (cc2)
CHAR_UUID_STATUS    = "e7add780-b042-4876-aae1-112855353cc3"  # Transport status reads (cc3)
CHAR_UUID_HEARTBEAT = "e7add780-b042-4876-aae1-112855353cc4"  # Heartbeat notifications (cc4)

# ── Transport-layer commands (from ALBtCmdDefs.java) ──────────────────────────
# Written to cc2 as 4-byte packets; status read back from cc3.
TCMD_RESET_STATE     = 1   # Reset transport state machine
TCMD_BLOCK_END       = 4   # End of a data block (param = checksum)
TCMD_XFER_END        = 5   # End of data transfer
TCMD_SEND_CMD        = 8   # Execute the transferred robot command
TCMD_STAGE_DATA      = 12  # Stage response data for reading (param = addr<<8 | count)
TCMD_DATA_XFER_START = 13  # Start a data transfer (param = total byte count)
TCMD_DATA_XFER_END   = 14  # Finish data transfer session

# Transport status codes (from ALBtCmdDefs.java)
TSTATUS_OK      =  0
TSTATUS_BUSY    = -1   # 0xFF unsigned – keep polling cc3
TSTATUS_IPCPEND = -2   # 0xFE unsigned – keep polling cc3

# ── Robot command IDs (from ALRobotCommands.java) ─────────────────────────────
# These are packed into command packets and sent via the transport layer.
CMD_NOP            = 0x00
CMD_GET_WETNESS    = 0x01  # Query wetness levels for all pad types (4-byte response)
CMD_SET_WETNESS    = 0x02  # Set wetness level (payload: [type, level])
CMD_GET_VOLUME     = 0x03  # Query current volume level (1-byte response)
CMD_SET_VOLUME     = 0x04  # Set volume level (1-byte payload)
CMD_REMOTE_CONTROL = 0x09  # Enable/disable remote control mode (payload: 1=on, 0=off)
CMD_BEEP           = 0x0D  # Trigger an audible beep (requires remote control mode)
CMD_SPOT_CLEAN     = 0x0E  # Start spot cleaning (limited area)
CMD_START_CLEAN    = 0x10  # Initiates a full-room cleaning mission
CMD_STOP_CLEAN   = 0x11  # Terminates an active cleaning mission
CMD_GET_STATUS   = 0x12  # Query robot state + mission status
CMD_GET_BATTERY  = 0x13  # Query battery level and voltages
CMD_GET_PAD_TYPE = 0x14  # Query attached cleaning pad type
CMD_POWER_OFF    = 0x15  # Power off the robot (ROBOT_CMD_OFF)

# ── Protocol message structure ────────────────────────────────────────────────
# Robot command format: [cmd_id, total_size, checksum, payload...]
# checksum = sum(all_bytes_except_byte[2]) & 0xFF
# total_size for no-payload commands = 3 (the 3-byte header itself).
CMD_HEADER_SIZE = 3
DATA_CHAR_CHUNK_SIZE = 20  # BLE data characteristic transfer chunk size

# ── BraavaRobotState enum (from BraavaRobotState.java) ───────────────────────
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

# ── BraavaMissionStatus enum – selected values (from BraavaMissionStatus.java) ─
MISSION_STATUS_UNDEFINED            = 0
MISSION_STATUS_SUCCESS              = 1
MISSION_STATUS_TERMINATED_BY_USER   = 6
MISSION_STATUS_TERMINATED_BY_ROBOT  = 7
MISSION_STATUS_DEAD_BATTERY         = 9
MISSION_STATUS_LOW_BATTERY          = 11
MISSION_STATUS_STUCK                = 20
MISSION_STATUS_IN_PROGRESS          = 26

# Human-readable labels for mission status codes
MISSION_STATUS_MAP: dict[int, str] = {
    0:  "undefined",
    1:  "success",
    2:  "start_refused_cliff",
    3:  "start_refused_bumped",
    4:  "start_refused_invalid_pad",
    5:  "start_refused_gyro_cal",
    6:  "terminated_by_user",
    7:  "terminated_by_robot",
    8:  "kidnap",
    9:  "dead_battery",
    10: "battery_over_temp",
    11: "low_battery",
    12: "timed_out",
    13: "watchdog_timed_out",
    14: "failed_to_relocalize",
    15: "failed_to_go_home",
    16: "max_coverage",
    17: "pad_id_error",
    18: "constant_cliff",
    19: "wheel_drop",
    20: "stuck",
    21: "trapped",
    22: "high_motor_current",
    23: "failed_to_make_progress",
    24: "out_of_bounds",
    25: "gyro_hardware_failure",
    26: "in_progress",
    27: "robot_reset",
    28: "unknown_cleaning_result",
    29: "unhandled_failure",
    34: "terminated_by_ble",  # observed after STOP_CLEAN via BLE
}

# Mission statuses that indicate a user-initiated stop (not a real error)
USER_STOP_MISSIONS = {6, 34}  # terminated_by_user, terminated_by_ble

# ── Pad type enum (from RobotPadCategory.java) ───────────────────────────
PAD_TYPE_MAP: dict[int, str] = {
    0: "invalid",
    1: "damp",
    2: "dry",
    3: "wet",
    4: "reusable_damp",
    5: "reusable_dry",
    6: "reusable_wet",
    7: "plate",
    8: "all",
    9: "no_pad",
}

# ── Wetness levels (from RobotPadWetnessLevel.java) ─────────────────────────
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

# Default wetness levels (from app "Restore Defaults")
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
POLL_INTERVAL_CLEANING = 5   # seconds – while mopping
POLL_INTERVAL_IDLE     = 15  # seconds – when docked/idle
