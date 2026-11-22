"""Data update coordinator for the iRobot Braava 240 BLE integration.

Maintains a persistent BLE connection to the robot.  Periodically sends
GET_STATUS and GET_BATTERY commands and collects responses via the
robot's transport protocol.

The Braava 240 uses a two-layer protocol:

  Transport layer (cc2 write → cc3 read):
      4-byte commands are written to the command characteristic (cc2)
      with write-type DEFAULT (acknowledged).  After each write, the
      status characteristic (cc3) is READ (not notified!) to get a
      4-byte response: [param_low, param_mid, param_high, status_byte].

  Robot command layer (cc1):
      Robot commands are packed as [cmd_id, size, checksum, payload...],
      transferred to the robot via cc1 in 20-byte blocks, then executed
      with the SEND_CMD transport command.  Response data is read back
      from cc1 using STAGE_DATA + read.

  Heartbeat (cc4):
      Only cc4 uses BLE notifications – a keepalive counter.

Protocol flow for each robot command:
  1. RESET_STATE   → write cc2, read cc3
  2. DATA_XFER_START (with padded length)  → write cc2, read cc3
  3. Write command bytes to cc1 in 20-byte chunks
  4. BLOCK_END (with block checksum)  → write cc2, read cc3
  5. XFER_END  → write cc2, read cc3
  6. SEND_CMD (with cmd header size) → write cc2, read cc3 → get bytes_to_receive
  7. For each 20-byte chunk of response: STAGE_DATA → write cc2, read cc3; read cc1
  8. DATA_XFER_END → write cc2
"""

import asyncio
import time
import logging
from collections.abc import Callable

from bleak import BleakClient
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothCallbackMatcher
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CHAR_UUID_COMMAND,
    CHAR_UUID_DATA,
    CHAR_UUID_HEARTBEAT,
    CHAR_UUID_STATUS,
    CMD_BEEP,
    CMD_GET_BBK_DATA,
    CMD_GET_BATTERY,
    CMD_GET_PAD_TYPE,
    CMD_GET_STATUS,
    CMD_GET_NAME,
    CMD_GET_ROOM_CONFINE,
    CMD_GET_VOLUME,
    CMD_GET_WETNESS,
    CMD_REMOTE_CONTROL,
    CMD_SET_NAME,
    CMD_SET_ROOM_CONFINE,
    CMD_SET_VOLUME,
    CMD_SET_WETNESS,
    CMD_POWER_OFF,
    WETNESS_DEFAULTS,
    CMD_SPOT_CLEAN,
    CMD_START_CLEAN,
    CMD_STOP_CLEAN,
    DATA_CHAR_CHUNK_SIZE,
    DOMAIN,
    GATT_FIRMWARE_REV,
    GATT_HARDWARE_REV,
    GATT_MODEL_NUMBER,
    GATT_SERIAL_NUMBER,
    GATT_SOFTWARE_REV,
    POLL_INTERVAL_BATTERY,
    POLL_INTERVAL_PAD,
    POLL_INTERVAL_STATUS,
    ROBOT_STATE_MISSION_IN_PROGRESS,
    TCMD_BLOCK_END,
    TCMD_CLIENT_ANDROID,
    TCMD_GET_SW_INFO,
    TCMD_SET_CLIENT_OS,
    TCMD_DATA_XFER_END,
    TCMD_DATA_XFER_START,
    TCMD_RESET_STATE,
    TCMD_SEND_CMD,
    TCMD_STAGE_DATA,
    TCMD_XFER_END,
    PAD_ERROR_MISSIONS,
    TSTATUS_BUSY,
    TSTATUS_IPCPEND,
    TSTATUS_OK,
)
from .parser import (
    build_robot_packet,
    pad_to_chunk_boundary,
    parse_bbk_life1,
    parse_bbk_life2,
    parse_response,
)

_LOGGER = logging.getLogger(__name__)

_BACKOFF_MIN = 10   # seconds – minimum reconnect back-off
_BACKOFF_MAX = 300  # seconds – maximum reconnect back-off

# Transport protocol timing
_XFER_CHUNK_DELAY = 0.07   # seconds between data chunks (XFER_DELAY_INITIAL)
_BLOCK_END_DELAY  = 0.5    # seconds after block end (DELAY_BETWEEN_BLOCKS)
# Waiting on the status characteristic: BUSY (0xFF) is re-read every 10 ms up
# to 100 times; any other pending status (IPCPEND, 0xFE) is re-read after 1 s,
# at most 3 times, then the command fails.
_BUSY_POLL_DELAY      = 0.01
_BUSY_MAX_RETRIES     = 100
_PENDING_POLL_DELAY   = 1.0
_PENDING_MAX_RETRIES  = 3
# Announce the client platform after connecting. Measured on a real robot
# (software 4.63): a GET_STATUS took 2.75 s (median) without it and 1.98 s
# with it, the lifetime statistics 4.7–4.9 s and 2.3 s.
_SEND_CLIENT_OS = True

# Whole-command timeout. The lifetime statistics answer with up to 100 bytes
# in many staged reads and need longer over BLE (group 2 took more than 5 s).
_COMMAND_TIMEOUT      = 5.0
_BBK_COMMAND_TIMEOUT  = 20.0


# Commands that only read: everything else is a control or set command.
_READ_COMMANDS = frozenset({
    CMD_GET_STATUS, CMD_GET_BATTERY, CMD_GET_PAD_TYPE, CMD_GET_VOLUME,
    CMD_GET_WETNESS, CMD_GET_ROOM_CONFINE, CMD_GET_NAME, CMD_GET_BBK_DATA,
})


class BraavaProtocolError(HomeAssistantError):
    """Raised when the Braava transport protocol returns an error."""


class BraavaDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator for persistent BLE connection to the Braava 240."""

    def __init__(
        self,
        hass,
        address: str,
        sw_version: str | None = None,
        hw_version: str | None = None,
        serial_number: str | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Braava 240",
            update_interval=None,  # live loop pushes data; no HA-driven polling
        )
        self.address = address

        # Latest parsed data from the robot
        self.data: dict = {}

        # Hardware info – pre-populated from config entry data (read during config flow)
        # or read from GATT Device Information Service on first connect as fallback
        self.device_info_read = sw_version is not None or hw_version is not None
        self.sw_version: str | None = sw_version
        # Robot software version from GET_SW_INFO (e.g. "4.63").
        self.robot_sw_version: str | None = None
        self.hw_version: str | None = hw_version
        self.serial_number: str | None = serial_number
        self.model_number: str | None = None

        # Cleaning mode selection ("normal" or "spot")
        self.cleaning_mode: str = "normal"

        # BLE client
        self._client: BleakClient | None = None
        self._connected = False

        # Background monitoring task
        self._live_task: asyncio.Task | None = None
        self._connection_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()  # serializes robot command transactions
        self._control_waiting = 0  # control/set commands waiting for the lock

        # Reads that are not part of the regular poll (name, BBK stats).
        # Deferred until after the first _poll() so that status/battery
        # entities appear without waiting.  An entry stays in the set until
        # its read succeeds: during a mission the robot often answers BUSY,
        # so a failed read is retried once the robot is idle again.
        self._pending_reads: set[str] = set()
        # Settings to read back on the next poll (after connect or a write).
        self._settings_dirty = False
        # Monotonic time of the last successful periodic read, per command.
        self._last_read: dict[int, float] = {}
        self._first_read_attempt = False
        self._last_robot_state: int | None = None

        # Event signalled when the robot advertises (for fast reconnect)
        self._device_available = asyncio.Event()
        # Set after a control command: the live loop polls the status at once
        # instead of a second, parallel poll from the command.
        self._poll_now = asyncio.Event()
        self._unsub_bluetooth: Callable | None = None

        # Log an outage once, not on every reconnect attempt
        self._unavailable_logged = False

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @callback
    def _on_bluetooth_advertisement(self, service_info, change) -> None:
        """Wake the monitoring loop when the robot appears in range."""
        _LOGGER.debug("BLE advertisement from %s", self.address)
        self._device_available.set()

    def start_live_monitoring(self) -> None:
        """Start the persistent background monitoring loop."""
        if self._live_task is None or self._live_task.done():
            self._live_task = self.hass.loop.create_task(self._monitoring_loop())

        if self._unsub_bluetooth is None:
            self._unsub_bluetooth = bluetooth.async_register_callback(
                self.hass,
                self._on_bluetooth_advertisement,
                BluetoothCallbackMatcher(address=self.address, connectable=True),
                bluetooth.BluetoothScanningMode.ACTIVE,
            )

    async def async_shutdown(self) -> None:
        """Cleanly shut down on integration unload."""
        _LOGGER.info("Shutting down Braava 240 coordinator (%s)", self.address)
        if self._unsub_bluetooth:
            self._unsub_bluetooth()
            self._unsub_bluetooth = None
        if self._live_task:
            self._live_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._live_task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        await self._disconnect()

    # ── Monitoring loop ────────────────────────────────────────────────────────

    async def _monitoring_loop(self) -> None:
        """Continuous loop: connect → poll → handle errors → reconnect."""
        backoff = _BACKOFF_MIN

        while True:
            try:
                # ── Ensure connection ──────────────────────────────────────────
                if not (self._client and self._client.is_connected):
                    service_info = bluetooth.async_last_service_info(
                        self.hass, self.address, connectable=True
                    )

                    if not service_info:
                        self._log_unavailable(
                            "Braava 240 not in range – waiting for advertisement (back-off %ds)",
                            backoff,
                        )
                        self._device_available.clear()
                        try:
                            await asyncio.wait_for(
                                self._device_available.wait(), timeout=backoff
                            )
                        except asyncio.TimeoutError:
                            pass
                        backoff = min(backoff * 2, _BACKOFF_MAX)
                        continue

                    backoff = _BACKOFF_MIN
                    async with self._connection_lock:
                        await self._connect(service_info.device)
                    if self._unavailable_logged:
                        _LOGGER.info("Braava 240 is available again")
                        self._unavailable_logged = False

                # ── Poll robot ─────────────────────────────────────────────────
                await self._poll()

                robot_state = self.data.get("robot_state")

                # Lifetime stats change when a mission ends – read them again
                if (
                    self._last_robot_state == ROBOT_STATE_MISSION_IN_PROGRESS
                    and robot_state is not None
                    and robot_state != ROBOT_STATE_MISSION_IN_PROGRESS
                ):
                    self._pending_reads |= {"bbk1", "bbk2"}
                self._last_robot_state = robot_state

                # ── Deferred reads: right after connect, then retried while idle
                # A control command waits for its status: postpone the slow
                # deferred reads (several seconds each) to the next cycle.
                if self._pending_reads and not self._poll_now.is_set() and (
                    self._first_read_attempt
                    or robot_state != ROBOT_STATE_MISSION_IN_PROGRESS
                ):
                    self._first_read_attempt = False
                    await self._run_pending_reads()
                    # Push the new data so name/BBK sensors appear immediately
                    self.async_set_updated_data(self.data)

                try:
                    await asyncio.wait_for(
                        self._poll_now.wait(), timeout=POLL_INTERVAL_STATUS
                    )
                except asyncio.TimeoutError:
                    pass
                self._poll_now.clear()

            except asyncio.CancelledError:
                raise
            except Exception as err:
                self._log_unavailable(
                    "Braava 240 monitoring error: %s – reconnecting in %ds", err, backoff
                )
                await self._disconnect()
                self._device_available.clear()
                try:
                    await asyncio.wait_for(
                        self._device_available.wait(), timeout=backoff
                    )
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, _BACKOFF_MAX)

    def _log_unavailable(self, msg: str, *args) -> None:
        """Log the first failure of an outage at info level, repeats at debug."""
        if self._unavailable_logged:
            _LOGGER.debug(msg, *args)
            return
        _LOGGER.info(msg + " (repeats are logged at debug level)", *args)
        self._unavailable_logged = True

    # ── Connection management ──────────────────────────────────────────────────

    async def _connect(self, ble_device) -> None:
        """Establish BLE GATT connection and set up heartbeat notifications."""
        if self._client:
            await self._disconnect()

        _LOGGER.debug("Connecting to Braava 240 (%s)", self.address)

        self._client = await establish_connection(
            BleakClient,
            ble_device,
            "Braava 240",
            timeout=20,
        )

        # Let the robot settle before we start throwing commands at it
        await asyncio.sleep(1.0)

        # Disconnection callback – update entities immediately
        def on_disconnect(client: BleakClient) -> None:
            _LOGGER.info("Braava 240 disconnected")
            self._connected = False
            self.async_set_updated_data(self.data or {})

        self._client.set_disconnected_callback(on_disconnect)

        # Heartbeat characteristic (cc4): the ONLY characteristic that uses notifications
        def on_heartbeat_notification(sender, data: bytearray) -> None:
            _LOGGER.debug("Heartbeat: %s", bytes(data).hex(" "))

        try:
            await self._client.start_notify(CHAR_UUID_HEARTBEAT, on_heartbeat_notification)
        except Exception as err:
            _LOGGER.debug("Heartbeat notifications not available: %s", err)

        if _SEND_CLIENT_OS:
            await self._set_client_os()

        self._connected = True
        _LOGGER.info("Connected to Braava 240 (%s)", self.address)

        # Read GATT Device Information Service (once per integration lifecycle)
        if not self.device_info_read:
            await self._read_gatt_device_info()

        # Robot name + lifetime stats are read after the first _poll() cycle
        # so that status/battery entities appear without the extra delay.
        self._pending_reads = {"sw_info", "name", "bbk1", "bbk2"}
        self._first_read_attempt = True
        # Read battery, pad type and the settings on the first poll.
        self._last_read = {}
        self._settings_dirty = True

    async def _read_gatt_device_info(self) -> None:
        """Read standard GATT Device Information Service characteristics."""
        gatt_reads = [
            (GATT_SERIAL_NUMBER, "serial_number"),
            (GATT_FIRMWARE_REV,  "sw_version"),
            (GATT_HARDWARE_REV,  "hw_version"),
            (GATT_MODEL_NUMBER,  "model_number"),
            (GATT_SOFTWARE_REV,  "sw_version"),  # fallback if firmware_rev absent
        ]
        for uuid, attr in gatt_reads:
            if getattr(self, attr) is not None:
                continue
            try:
                raw = await self._client.read_gatt_char(uuid)
                value = bytes(raw).decode("utf-8", errors="replace").strip("\x00 ")
                if value:
                    setattr(self, attr, value)
                    _LOGGER.info("GATT %s: %s", attr, value)
            except Exception:
                _LOGGER.debug("GATT char %s not available", uuid)

        self.device_info_read = True
        _LOGGER.info(
            "Device info: serial=%s sw=%s hw=%s model=%s",
            self.serial_number, self.sw_version, self.hw_version, self.model_number,
        )

    async def _run_pending_reads(self) -> None:
        """Run the outstanding non-poll reads; keep failed ones for a retry."""
        readers = {
            "sw_info": self._read_sw_info,
            "name": self._read_robot_name,
            "bbk1": self._read_bbk_life1,
            "bbk2": self._read_bbk_life2,
        }
        for key in [k for k in readers if k in self._pending_reads]:
            if self._poll_now.is_set() or self._control_waiting:
                break  # a control command goes first
            if await readers[key]():
                self._pending_reads.discard(key)

    async def _set_client_os(self) -> None:
        """Announce the client platform (transport command 0, 2 = Android)
        right after enabling the heartbeat notifications."""
        try:
            async with self._command_lock, asyncio.timeout(_COMMAND_TIMEOUT):
                _, status = await self._transport_cmd(
                    TCMD_SET_CLIENT_OS, TCMD_CLIENT_ANDROID, pending_retries=2, retry_any=True
                )
            _LOGGER.debug("SET_CLIENT_OS sent: status=%d", status)
        except (BraavaProtocolError, TimeoutError) as err:
            _LOGGER.debug("SET_CLIENT_OS failed: %s", err)

    async def _read_sw_info(self) -> bool:
        """Read the robot software version (GET_SW_INFO). The Device
        Information "firmware" (e.g. 1.4.0) is the BLE module's; the robot
        software reports e.g. 4.63. Returns True on success."""
        try:
            async with self._command_lock, asyncio.timeout(_COMMAND_TIMEOUT * 2):
                _, status = await self._transport_cmd(TCMD_RESET_STATE, 0x10000)
                if status != TSTATUS_OK:
                    raise BraavaProtocolError(f"RESET_STATE failed: status={status}")
                # A non-zero answer is re-read after 1 s, up to 9 times.
                param, status = await self._transport_cmd(
                    TCMD_GET_SW_INFO, 0, pending_retries=9, retry_any=True
                )
        except (BraavaProtocolError, TimeoutError) as err:
            _LOGGER.debug("GET_SW_INFO failed, will retry: %s", err)
            return False
        if status != TSTATUS_OK:
            _LOGGER.debug("GET_SW_INFO failed, will retry: status=%d", status)
            return False
        major, minor, image_id = (param >> 16) & 0xFF, (param >> 8) & 0xFF, param & 0xFF
        version = f"{major}.{minor:02d}"
        _LOGGER.info(
            "Robot software %s (image %d; BLE module %s)", version, image_id, self.sw_version
        )
        self.robot_sw_version = version
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device(identifiers={(DOMAIN, self.address)}):
            registry.async_update_device(device.id, sw_version=version)
        return True

    async def _read_robot_name(self) -> bool:
        """Read the robot name via GET_NAME. Returns True on success."""
        try:
            raw = await self._send_robot_command(CMD_GET_NAME)
        except BraavaProtocolError as err:
            _LOGGER.debug("GET_NAME failed, will retry: %s", err)
            return False
        parsed = parse_response(raw) if raw else None
        if not parsed or parsed.get("type") != "name":
            return False
        self.data["robot_name"] = parsed["robot_name"]
        _LOGGER.info("Robot name: %s", parsed["robot_name"])
        return True

    async def _read_bbk_life1(self) -> bool:
        """Read mission counts + average duration (GET_BBK_DATA group 1)."""
        try:
            raw = await self._send_robot_command(
                CMD_GET_BBK_DATA, payload=bytes([1])
            )
        except BraavaProtocolError as err:
            _LOGGER.debug("GET_BBK_DATA group 1 failed, will retry: %s", err)
            return False
        parsed = parse_bbk_life1(raw) if raw else None
        if not parsed:
            return False
        _apply(self.data, parsed)
        return True

    async def _read_bbk_life2(self) -> bool:
        """Read the total cleaning time (GET_BBK_DATA group 2)."""
        try:
            raw = await self._send_robot_command(
                CMD_GET_BBK_DATA, payload=bytes([2])
            )
        except BraavaProtocolError as err:
            _LOGGER.debug("GET_BBK_DATA group 2 failed, will retry: %s", err)
            return False
        parsed = parse_bbk_life2(raw) if raw else None
        if not parsed:
            return False
        _apply(self.data, parsed)
        return True

    async def _disconnect(self) -> None:
        """Disconnect cleanly.

        Not gated on is_connected: a client the robot already disconnected
        still needs disconnect() called to close bleak's own D-Bus
        connection. bleak only tears that down inside BleakClient.disconnect()
        — a remote disconnect (on_disconnect callback) never calls it, so
        the connection is otherwise left open and BlueZ keeps any
        StartNotify registrations on it alive, re-arming them on the next
        connect (same mechanism as mtheli/philips_sonicare_ble#45).
        """
        if self._client:
            try:
                async with asyncio.timeout(5.0):
                    await self._client.disconnect()
                    _LOGGER.debug("Disconnected from Braava 240")
            except Exception as err:
                _LOGGER.debug("Disconnect error: %s", err)

        self._client = None
        was_connected = self._connected
        self._connected = False
        # A link lost without the disconnected callback (e.g. a failed read or
        # reconnect) must still flip the connected sensor and the vacuum.
        if was_connected:
            self.async_update_listeners()

    # ── Transport layer (cc2 write → cc3 read) ────────────────────────────────

    async def _transport_cmd(
        self,
        cmd: int,
        param: int = 0,
        *,
        pending_delay: float = _PENDING_POLL_DELAY,
        pending_retries: int = _PENDING_MAX_RETRIES,
        retry_any: bool = False,
    ) -> tuple[int, int]:
        """Send a transport command to cc2 and read the status from cc3.

        Writes 4 bytes to cc2: little-endian int32 = (cmd << 24) | (param & 0xFFFFFF)
        Then reads 4 bytes from cc3: [param_low, param_mid, param_high, status_byte]

        Handles BUSY/IPCPEND by re-reading cc3 until a final status arrives.

        Returns (response_param, status_byte) where status_byte is signed.
        """
        # Pack transport command as little-endian int32
        wire_value = (cmd << 24) | (param & 0x00FFFFFF)
        wire_bytes = wire_value.to_bytes(4, "little")

        _LOGGER.debug(
            "Transport CMD %d param=0x%06x → %s",
            cmd, param & 0xFFFFFF, wire_bytes.hex(" "),
        )

        # Write to cc2 with response=True (acknowledged write)
        await self._client.write_gatt_char(CHAR_UUID_COMMAND, wire_bytes, response=True)

        # Read cc3 and handle busy-wait
        busy_left = _BUSY_MAX_RETRIES
        pending_left = pending_retries
        while True:
            resp = bytes(await self._client.read_gatt_char(CHAR_UUID_STATUS))

            if len(resp) < 4:
                _LOGGER.warning("Transport status too short: %d bytes", len(resp))
                raise BraavaProtocolError(f"Status read returned {len(resp)} bytes")

            resp_param = resp[0] | (resp[1] << 8) | (resp[2] << 16)
            # Convert status byte to signed (signed byte: -1=BUSY, -2=IPCPEND)
            status = resp[3] if resp[3] < 128 else resp[3] - 256

            _LOGGER.debug(
                "Transport status: param=0x%06x status=%d (%s)",
                resp_param, status, resp.hex(" "),
            )

            if status == TSTATUS_BUSY:
                busy_left -= 1
                if busy_left <= 0:
                    break
                await asyncio.sleep(_BUSY_POLL_DELAY)
                continue
            if status == TSTATUS_IPCPEND or (retry_any and status != TSTATUS_OK):
                pending_left -= 1
                if pending_left < 0:
                    if retry_any:
                        return resp_param, status  # caller reports the status
                    break
                await asyncio.sleep(pending_delay)
                continue

            return resp_param, status

        raise BraavaProtocolError("Robot stayed BUSY for too long")

    # ── Robot command protocol ─────────────────────────────────────────────────

    async def _send_robot_command(self, cmd_id: int, payload: bytes = b"") -> bytes | None:
        """Execute the full transport protocol to send a robot command.

        Steps:
          1. RESET_STATE
          2. DATA_XFER_START with padded data length
          3. Write command data to cc1 in 20-byte chunks
          4. BLOCK_END with block checksum
          5. XFER_END
          6. SEND_CMD → get bytes_to_receive
          7. STAGE_DATA + read cc1 for each response chunk
          8. DATA_XFER_END

        Returns the raw response bytes, or None if no response data.

        The command lock ensures only one robot command runs at a time.
        Concurrent commands would interleave transport writes on the BLE link.
        """
        # Control and set commands go before the periodic reads: the poll and
        # the deferred reads stop while one is waiting.
        control = cmd_id not in _READ_COMMANDS
        timeout = (
            _BBK_COMMAND_TIMEOUT if cmd_id == CMD_GET_BBK_DATA else _COMMAND_TIMEOUT
        )
        if control:
            self._control_waiting += 1
        try:
            async with self._command_lock:
                if control:
                    self._control_waiting -= 1
                    control = False
                try:
                    async with asyncio.timeout(timeout):
                        return await self._send_robot_command_locked(cmd_id, payload)
                except TimeoutError as err:
                    raise BraavaProtocolError(
                        f"Command 0x{cmd_id:02x} timed out after {timeout:.0f}s"
                    ) from err
        finally:
            if control:
                self._control_waiting -= 1

    async def _send_robot_command_locked(self, cmd_id: int, payload: bytes = b"") -> bytes | None:
        """Inner implementation of _send_robot_command (called with _command_lock held)."""
        if not self._client or not self._client.is_connected:
            raise BraavaProtocolError("Not connected")

        # Build the robot command packet
        cmd_packet = build_robot_packet(cmd_id, payload)
        padded = pad_to_chunk_boundary(cmd_packet)

        _LOGGER.debug(
            "Sending robot CMD 0x%02x (%d bytes, padded to %d): %s",
            cmd_id, len(cmd_packet), len(padded), cmd_packet.hex(" "),
        )

        # ── Step 1: RESET_STATE ──────────────────────────────────────────────
        _, status = await self._transport_cmd(TCMD_RESET_STATE, 0x10000)
        if status != TSTATUS_OK:
            raise BraavaProtocolError(f"RESET_STATE failed: status={status}")

        # ── Step 2: DATA_XFER_START ──────────────────────────────────────────
        _, status = await self._transport_cmd(TCMD_DATA_XFER_START, len(padded))
        if status != TSTATUS_OK:
            raise BraavaProtocolError(f"DATA_XFER_START failed: status={status}")

        # ── Step 3: Block transfer – write command data to cc1 ───────────────
        # Calculate block checksum over the full padded buffer
        block_checksum = sum(padded) & 0x00FFFFFF

        for offset in range(0, len(padded), DATA_CHAR_CHUNK_SIZE):
            chunk = padded[offset : offset + DATA_CHAR_CHUNK_SIZE]
            # Write to cc1 with response=False (write without response)
            await self._client.write_gatt_char(CHAR_UUID_DATA, chunk, response=False)
            await asyncio.sleep(_XFER_CHUNK_DELAY)

        # ── Step 4: BLOCK_END with checksum ──────────────────────────────────
        # A pending BLOCK_END is re-read after 20 ms, without a limit (the
        # command timeout ends it).
        _, status = await self._transport_cmd(
            TCMD_BLOCK_END, block_checksum, pending_delay=0.02, pending_retries=1000
        )
        if status != TSTATUS_OK:
            raise BraavaProtocolError(f"BLOCK_END failed: status={status}")

        # Wait between blocks
        await asyncio.sleep(_BLOCK_END_DELAY)

        # ── Step 5: XFER_END ─────────────────────────────────────────────────
        # The status byte of the XFER_END response is not meaningful on this
        # robot, so it is deliberately not checked here.
        await self._transport_cmd(TCMD_XFER_END, 0)

        # ── Step 6: SEND_CMD ─────────────────────────────────────────────────
        # Any non-zero SEND_CMD status is re-read after 1 s, twice.
        resp_param, status = await self._transport_cmd(
            TCMD_SEND_CMD, len(cmd_packet), pending_retries=2, retry_any=True
        )
        if status != TSTATUS_OK:
            raise BraavaProtocolError(f"SEND_CMD failed: status={status}")

        bytes_to_receive = resp_param & 0xFFFF
        _LOGGER.debug("SEND_CMD response: bytes_to_receive=%d", bytes_to_receive)

        # ── Step 7: Collect response data ────────────────────────────────────
        if bytes_to_receive == 0:
            # No response data (e.g., START_CLEAN, STOP_CLEAN)
            await self._transport_cmd(TCMD_DATA_XFER_END, len(cmd_packet))
            return None

        response_data = bytearray()
        read_offset = 0
        remaining = bytes_to_receive

        while remaining > 0:
            chunk_size = min(remaining, DATA_CHAR_CHUNK_SIZE)

            # STAGE_DATA: param = (address << 8) | count
            stage_param = (read_offset << 8) | chunk_size
            _, status = await self._transport_cmd(TCMD_STAGE_DATA, stage_param)
            if status != TSTATUS_OK:
                raise BraavaProtocolError(f"STAGE_DATA failed: status={status}")

            # Read data from cc1
            data = bytes(await self._client.read_gatt_char(CHAR_UUID_DATA))
            response_data.extend(data[:chunk_size])

            read_offset += chunk_size
            remaining -= chunk_size

        # ── Step 8: DATA_XFER_END ────────────────────────────────────────────
        await self._transport_cmd(TCMD_DATA_XFER_END, len(cmd_packet))

        _LOGGER.debug(
            "Robot CMD 0x%02x response (%d bytes): %s",
            cmd_id, len(response_data), bytes(response_data).hex(" "),
        )
        return bytes(response_data)

    # ── Polling ────────────────────────────────────────────────────────────────

    async def _read_into(
        self, new_data: dict, cmd_id: int, name: str, *, critical: bool = False
    ) -> bool:
        """Send one GET command and merge its parsed answer into *new_data*.

        A protocol error (e.g. the robot stayed BUSY) is logged and re-raised
        for the caller to decide; any other error is re-raised only for
        *critical* reads, so the live loop reconnects.
        """
        try:
            raw = await self._send_robot_command(cmd_id)
        except BraavaProtocolError as err:
            if critical:
                _LOGGER.warning("%s failed: %s", name, err)
            else:
                _LOGGER.debug("%s failed: %s", name, err)
            raise
        except Exception as err:
            _LOGGER.debug("%s error: %s", name, err)
            if critical:
                raise
            return False
        if raw:
            parsed = parse_response(raw)
            if parsed:
                _apply(new_data, parsed)
                return True
        return False

    async def _poll(self) -> None:
        """Read status, battery, pad type and (when needed) the settings."""
        if not self._client or not self._client.is_connected:
            return

        new_data = dict(self.data)
        received = 0

        # GET_STATUS – pushed at once, so a started, stopped or failed mission
        # shows without waiting for the slower reads below (~2 s each).
        try:
            if await self._read_into(new_data, CMD_GET_STATUS, "GET_STATUS", critical=True):
                received += 1
        except BraavaProtocolError:
            # Robot busy (IPCPEND): the other commands would fail too.
            _LOGGER.debug("Skipping remaining commands – robot busy")
            self.async_set_updated_data(new_data if new_data != self.data else (self.data or {}))
            return
        if new_data != self.data:
            self.async_set_updated_data(dict(new_data))

        now = time.monotonic()
        reads = []
        # The pad type is read at once when a pad error appears or clears;
        # otherwise every POLL_INTERVAL_PAD.
        pad_error = new_data.get("mission_status") in PAD_ERROR_MISSIONS
        pad_changed = pad_error != (self.data.get("mission_status") in PAD_ERROR_MISSIONS)
        for cmd_id, name, interval, force in (
            (CMD_GET_BATTERY, "GET_BATTERY", POLL_INTERVAL_BATTERY, False),
            (CMD_GET_PAD_TYPE, "GET_PAD_TYPE", POLL_INTERVAL_PAD, pad_changed),
        ):
            last = self._last_read.get(cmd_id)
            if force or last is None or now - last >= interval:
                reads.append((cmd_id, name))
        # Volume, wetness and room confinement are settings: read once after
        # connecting and after we write one, not periodically.
        if self._settings_dirty:
            reads += [
                (CMD_GET_VOLUME, "GET_VOLUME"),
                (CMD_GET_WETNESS, "GET_WETNESS"),
                (CMD_GET_ROOM_CONFINE, "GET_ROOM_CONFINE"),
            ]
        settings_done = False
        for cmd_id, name in reads:
            if self._control_waiting:
                break  # a control command goes first; the rest stays due
            try:
                if await self._read_into(
                    new_data, cmd_id, name, critical=cmd_id == CMD_GET_BATTERY
                ):
                    received += 1
                    self._last_read[cmd_id] = now
            except BraavaProtocolError:
                pass
            if cmd_id == CMD_GET_ROOM_CONFINE:
                settings_done = True
        if settings_done:
            self._settings_dirty = False

        if received > 0 or new_data != self.data:
            self.async_set_updated_data(new_data)
        else:
            _LOGGER.debug("Manually updated Braava 240 data")
            self.async_set_updated_data(self.data or {})

    # ── Control commands ───────────────────────────────────────────────────────

    async def _send_with_retry(self, cmd_id: int, payload: bytes = b"") -> bytes | None:
        """Send a robot command with one retry on BUSY timeout."""
        try:
            return await self._send_robot_command(cmd_id, payload)
        except BraavaProtocolError:
            _LOGGER.debug("Command 0x%02x busy, retrying after 1s", cmd_id)
            await asyncio.sleep(1.0)
            return await self._send_robot_command(cmd_id, payload)

    async def async_start_cleaning(self) -> None:
        """Start cleaning using the selected mode (normal or spot)."""
        if self.cleaning_mode == "spot":
            await self._send_with_retry(CMD_SPOT_CLEAN)
            _LOGGER.info("SPOT_CLEAN sent to Braava 240")
        else:
            await self._send_with_retry(CMD_START_CLEAN)
            _LOGGER.info("START_CLEAN sent to Braava 240")
        # Let the live loop read the status at once so the UI updates promptly
        self._poll_now.set()

    async def async_stop_cleaning(self) -> None:
        """Send STOP_CLEAN command (0x11)."""
        await self._send_with_retry(CMD_STOP_CLEAN)
        _LOGGER.info("STOP_CLEAN sent to Braava 240")
        self._poll_now.set()

    async def async_beep(self) -> None:
        """Send BEEP command (0x0D) inside remote control mode.

        Sent alone, BEEP is rejected (SEND_CMD status 4, robot software 4.63);
        with remote control mode around it, the robot beeps.
        """
        await self._send_robot_command(CMD_REMOTE_CONTROL, payload=bytes([1]))
        try:
            await self._send_robot_command(CMD_BEEP)
            _LOGGER.info("BEEP sent to Braava 240")
        finally:
            await self._send_robot_command(CMD_REMOTE_CONTROL, payload=bytes([0]))

    async def async_set_volume(self, level: int) -> None:
        """Send SET_VOLUME command (0x04)."""
        await self._send_robot_command(CMD_SET_VOLUME, payload=bytes([level]))
        self._settings_dirty = True
        _LOGGER.info("SET_VOLUME(%d) sent to Braava 240", level)

    async def async_set_wetness(self, pad_type: int, level: int) -> None:
        """Send SET_WETNESS command (0x02). payload: [type, level]."""
        await self._send_robot_command(CMD_SET_WETNESS, payload=bytes([pad_type, level]))
        self._settings_dirty = True
        _LOGGER.info("SET_WETNESS(type=%d, level=%d) sent to Braava 240", pad_type, level)

    async def async_reset_wetness(self) -> None:
        """Reset all wetness levels to defaults (medium)."""
        for pad_type, level in WETNESS_DEFAULTS.items():
            await self.async_set_wetness(pad_type, level)
        _LOGGER.info("Wetness levels reset to defaults")

    async def async_power_off(self) -> None:
        """Send POWER_OFF command (0x15). The robot will shut down completely."""
        await self._send_robot_command(CMD_POWER_OFF)
        _LOGGER.info("POWER_OFF sent to Braava 240")

    async def async_set_name(self, name: str) -> None:
        """Send SET_NAME command (0x05). Name is a null-terminated string, max 20 bytes."""
        # 19 bytes leave room for the null terminator; cut at a character
        # boundary so a multi-byte character is not split.
        encoded = name.encode("utf-8")[:19].decode("utf-8", "ignore").encode("utf-8")
        payload = encoded + b"\x00" * (20 - len(encoded))  # Pad to 20 bytes
        await self._send_robot_command(CMD_SET_NAME, payload=payload)
        self.data["robot_name"] = encoded.decode("utf-8")
        _LOGGER.info("SET_NAME('%s') sent to Braava 240", name)

    async def async_set_room_confine(self, enabled: bool) -> None:
        """Send SET_ROOM_CONFINE command (0x08). payload: 0=off, 1=on."""
        if self.data.get("robot_state") == ROBOT_STATE_MISSION_IN_PROGRESS:
            # The robot has to finish or cancel the job first.
            raise HomeAssistantError(
                "Room confinement cannot be changed while the robot is cleaning"
            )
        await self._send_robot_command(CMD_SET_ROOM_CONFINE, payload=bytes([int(enabled)]))
        self.data["room_confine"] = enabled
        self._settings_dirty = True
        _LOGGER.info("SET_ROOM_CONFINE(%s) sent to Braava 240", enabled)

    # ── DataUpdateCoordinator override ────────────────────────────────────────

    async def _async_update_data(self) -> dict:
        """Called by HA's scheduler – the live loop already handles updates."""
        return self.data or {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _apply(data: dict, parsed: dict) -> None:
    """Merge a parsed response dict into the coordinator data dict."""
    ptype = parsed.get("type")
    if ptype == "status":
        data.update({
            "robot_state":       parsed["robot_state"],
            "robot_state_str":   parsed["robot_state_str"],
            "runtime_minutes":   parsed["runtime_minutes"],
            "mission_status":    parsed["mission_status"],
            "mission_status_str": parsed["mission_status_str"],
        })
    elif ptype == "battery":
        data.update({
            "battery_level":    parsed["battery_level"],
            "current_voltage":  parsed.get("current_voltage"),
            "current_charge":   parsed.get("current_charge"),
        })
    elif ptype == "pad_type":
        data.update({
            "pad_type":     parsed["pad_type"],
            "pad_type_str": parsed["pad_type_str"],
        })
    elif ptype == "volume":
        data["volume"] = parsed["volume"]
    elif ptype == "wetness":
        data.update({
            "wetness_wet":            parsed["wetness_wet"],
            "wetness_damp":           parsed["wetness_damp"],
            "wetness_reusable_wet":   parsed["wetness_reusable_wet"],
            "wetness_reusable_damp":  parsed["wetness_reusable_damp"],
            "wetness_wet_str":           parsed["wetness_wet_str"],
            "wetness_damp_str":          parsed["wetness_damp_str"],
            "wetness_reusable_wet_str":  parsed["wetness_reusable_wet_str"],
            "wetness_reusable_damp_str": parsed["wetness_reusable_damp_str"],
        })
    elif ptype == "name":
        data["robot_name"] = parsed["robot_name"]
    elif ptype == "room_confine":
        data["room_confine"] = parsed["room_confine"]
    elif ptype == "bbk_life1":
        data.update({
            "total_missions": parsed["total_missions"],
            "successful_missions": parsed["successful_missions"],
            "failed_missions": parsed["failed_missions"],
            "average_mission_minutes": parsed["average_mission_minutes"],
        })
    elif ptype == "bbk_life2":
        runtime_min = parsed["total_cleaning_minutes"]
        data["total_cleaning_hours"] = round(runtime_min / 60, 1)
