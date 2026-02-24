"""Data update coordinator for the iRobot Braava 240 BLE integration.

Maintains a persistent BLE connection to the robot.  Periodically sends
GET_STATUS and GET_BATTERY commands and collects responses via the
Altadena transport protocol.

The Braava 240 uses a two-layer protocol (from ALRobot.java):

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

Protocol flow for each robot command (from ALSendCommandStateMachine.java):
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
import logging
from collections.abc import Callable

from bleak import BleakClient
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothCallbackMatcher
from homeassistant.core import callback
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
    POLL_INTERVAL_CLEANING,
    POLL_INTERVAL_IDLE,
    ROBOT_STATE_MISSION_IN_PROGRESS,
    TCMD_BLOCK_END,
    TCMD_DATA_XFER_END,
    TCMD_DATA_XFER_START,
    TCMD_RESET_STATE,
    TCMD_SEND_CMD,
    TCMD_STAGE_DATA,
    TCMD_XFER_END,
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

# Transport protocol timing (from ALBlockXferStateMachine.java / ALRobot.java)
_XFER_CHUNK_DELAY = 0.07   # seconds between data chunks (XFER_DELAY_INITIAL)
_BLOCK_END_DELAY  = 0.5    # seconds after block end (DELAY_BETWEEN_BLOCKS)
_BUSY_POLL_DELAY  = 0.05   # seconds between busy-status re-reads
_BUSY_MAX_RETRIES = 60     # max busy-wait iterations (~3s timeout)


class BraavaProtocolError(Exception):
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

        # One-time reads (name, BBK stats) are deferred until after the first
        # _poll() so that status/battery entities appear without waiting.
        self._one_time_reads_pending = False

        # Event signalled when the robot advertises (for fast reconnect)
        self._device_available = asyncio.Event()
        self._unsub_bluetooth: Callable | None = None

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
                        _LOGGER.info(
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

                # ── Poll robot ─────────────────────────────────────────────────
                await self._poll()

                # ── Deferred one-time reads (after first poll) ────────────────
                if self._one_time_reads_pending:
                    self._one_time_reads_pending = False
                    await self._read_robot_name()
                    await self._read_bbk_data()
                    # Push the new data so name/BBK sensors appear immediately
                    self.async_set_updated_data(self.data)

                # Adaptive interval: poll more frequently while cleaning
                robot_state = self.data.get("robot_state")
                interval = (
                    POLL_INTERVAL_CLEANING
                    if robot_state == ROBOT_STATE_MISSION_IN_PROGRESS
                    else POLL_INTERVAL_IDLE
                )
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning(
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
        # (from ALRobot.java onServicesDiscovered – only heartbeatChar gets CCCD enabled)
        def on_heartbeat_notification(sender, data: bytearray) -> None:
            _LOGGER.debug("Heartbeat: %s", bytes(data).hex(" "))

        try:
            await self._client.start_notify(CHAR_UUID_HEARTBEAT, on_heartbeat_notification)
        except Exception as err:
            _LOGGER.debug("Heartbeat notifications not available: %s", err)

        self._connected = True
        _LOGGER.info("Connected to Braava 240 (%s)", self.address)

        # Read GATT Device Information Service (once per integration lifecycle)
        if not self.device_info_read:
            await self._read_gatt_device_info()

        # Robot name + lifetime stats are read after the first _poll() cycle
        # so that status/battery entities appear without the extra delay.
        self._one_time_reads_pending = True

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

    async def _read_robot_name(self) -> None:
        """Read the robot name via GET_NAME (once after connect)."""
        try:
            raw = await self._send_robot_command(CMD_GET_NAME)
            if raw:
                parsed = parse_response(raw)
                if parsed and parsed.get("type") == "name":
                    self.data["robot_name"] = parsed["robot_name"]
                    _LOGGER.info("Robot name: %s", parsed["robot_name"])
        except BraavaProtocolError as err:
            _LOGGER.debug("GET_NAME failed: %s", err)

    async def _read_bbk_data(self) -> None:
        """Read lifetime statistics via GET_BBK_DATA groups 1 and 2."""
        # Group 1: mission counts + average duration
        try:
            raw = await self._send_robot_command(
                CMD_GET_BBK_DATA, payload=bytes([1])
            )
            if raw:
                parsed = parse_bbk_life1(raw)
                if parsed:
                    _apply(self.data, parsed)
        except BraavaProtocolError as err:
            _LOGGER.debug("GET_BBK_DATA group 1 failed: %s", err)

        # Group 2: total runtime
        try:
            raw = await self._send_robot_command(
                CMD_GET_BBK_DATA, payload=bytes([2])
            )
            if raw:
                parsed = parse_bbk_life2(raw)
                if parsed:
                    _apply(self.data, parsed)
        except BraavaProtocolError as err:
            _LOGGER.debug("GET_BBK_DATA group 2 failed: %s", err)

    async def _disconnect(self) -> None:
        """Disconnect cleanly."""
        if self._client and self._client.is_connected:
            try:
                async with asyncio.timeout(5.0):
                    await self._client.disconnect()
                    _LOGGER.debug("Disconnected from Braava 240")
            except Exception as err:
                _LOGGER.debug("Disconnect error: %s", err)

        self._client = None
        self._connected = False

    # ── Transport layer (cc2 write → cc3 read) ────────────────────────────────

    async def _transport_cmd(self, cmd: int, param: int = 0) -> tuple[int, int]:
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

        # Write to cc2 with response=True (WRITE_TYPE_DEFAULT, as per ALRobot.java)
        await self._client.write_gatt_char(CHAR_UUID_COMMAND, wire_bytes, response=True)

        # Read cc3 and handle busy-wait
        # (mirrors ALRobot.handleStatusCharacteristicRead busy-loop)
        for attempt in range(_BUSY_MAX_RETRIES):
            resp = bytes(await self._client.read_gatt_char(CHAR_UUID_STATUS))

            if len(resp) < 4:
                _LOGGER.warning("Transport status too short: %d bytes", len(resp))
                raise BraavaProtocolError(f"Status read returned {len(resp)} bytes")

            resp_param = resp[0] | (resp[1] << 8) | (resp[2] << 16)
            # Convert status byte to signed (Java byte is signed: -1=BUSY, -2=IPCPEND)
            status = resp[3] if resp[3] < 128 else resp[3] - 256

            _LOGGER.debug(
                "Transport status: param=0x%06x status=%d (%s)",
                resp_param, status, resp.hex(" "),
            )

            if status == TSTATUS_BUSY:
                await asyncio.sleep(_BUSY_POLL_DELAY)
                continue
            if status == TSTATUS_IPCPEND:
                await asyncio.sleep(_BUSY_POLL_DELAY * 2)
                continue

            return resp_param, status

        raise BraavaProtocolError("Robot stayed BUSY for too long")

    # ── Robot command protocol (from ALSendCommandStateMachine.java) ───────────

    async def _send_robot_command(self, cmd_id: int, payload: bytes = b"") -> bytes | None:
        """Execute the full transport protocol to send a robot command.

        Steps (from ALSendCommandStateMachine.java):
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
        async with self._command_lock:
            return await self._send_robot_command_locked(cmd_id, payload)

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
        # (from ALBlockXferStateMachine.java BLOCK_START: iterates xferBuffer[0..blockSize])
        block_checksum = sum(padded) & 0x00FFFFFF

        for offset in range(0, len(padded), DATA_CHAR_CHUNK_SIZE):
            chunk = padded[offset : offset + DATA_CHAR_CHUNK_SIZE]
            # Write to cc1 with response=False (WRITE_TYPE_NO_RESPONSE)
            # (from ALRobot.java: dataChar.setWriteType(1))
            await self._client.write_gatt_char(CHAR_UUID_DATA, chunk, response=False)
            await asyncio.sleep(_XFER_CHUNK_DELAY)

        # ── Step 4: BLOCK_END with checksum ──────────────────────────────────
        _, status = await self._transport_cmd(TCMD_BLOCK_END, block_checksum)
        if status != TSTATUS_OK:
            raise BraavaProtocolError(f"BLOCK_END failed: status={status}")

        # Wait between blocks (from ALBlockXferStateMachine: Thread.sleep(500))
        await asyncio.sleep(_BLOCK_END_DELAY)

        # ── Step 5: XFER_END ─────────────────────────────────────────────────
        # The Java FSM sets state=SEND_COMMAND *before* sending XFER_END, so
        # the XFER_END response is consumed by SEND_COMMAND (case 6) which
        # ignores the status byte entirely.  We must NOT check it here.
        await self._transport_cmd(TCMD_XFER_END, 0)

        # ── Step 6: SEND_CMD ─────────────────────────────────────────────────
        resp_param, status = await self._transport_cmd(TCMD_SEND_CMD, len(cmd_packet))
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
            # (from ALRobot.stageDataFromAddress)
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

    async def _poll(self) -> None:
        """Send GET_STATUS + GET_BATTERY via the transport protocol."""
        if not self._client or not self._client.is_connected:
            return

        new_data = dict(self.data)
        received = 0
        robot_busy = False

        # GET_STATUS
        try:
            raw = await self._send_robot_command(CMD_GET_STATUS)
            if raw:
                parsed = parse_response(raw)
                if parsed:
                    _apply(new_data, parsed)
                    received += 1
        except BraavaProtocolError as err:
            _LOGGER.warning("GET_STATUS failed: %s", err)
            robot_busy = True
        except Exception as err:
            _LOGGER.warning("GET_STATUS error: %s", err)
            raise

        # GET_BATTERY – skip if the robot was busy (IPCPEND), it will fail too
        if robot_busy:
            _LOGGER.debug("Skipping remaining commands – robot busy")
        else:
            try:
                raw = await self._send_robot_command(CMD_GET_BATTERY)
                if raw:
                    parsed = parse_response(raw)
                    if parsed:
                        _apply(new_data, parsed)
                        received += 1
            except BraavaProtocolError as err:
                _LOGGER.warning("GET_BATTERY failed: %s", err)
            except Exception as err:
                _LOGGER.warning("GET_BATTERY error: %s", err)
                raise

            # GET_PAD_TYPE
            try:
                raw = await self._send_robot_command(CMD_GET_PAD_TYPE)
                if raw:
                    parsed = parse_response(raw)
                    if parsed:
                        _apply(new_data, parsed)
                        received += 1
            except BraavaProtocolError as err:
                _LOGGER.debug("GET_PAD_TYPE failed: %s", err)
            except Exception as err:
                _LOGGER.debug("GET_PAD_TYPE error: %s", err)

            # GET_VOLUME
            try:
                raw = await self._send_robot_command(CMD_GET_VOLUME)
                if raw:
                    parsed = parse_response(raw)
                    if parsed:
                        _apply(new_data, parsed)
                        received += 1
            except BraavaProtocolError as err:
                _LOGGER.debug("GET_VOLUME failed: %s", err)
            except Exception as err:
                _LOGGER.debug("GET_VOLUME error: %s", err)

            # GET_WETNESS
            try:
                raw = await self._send_robot_command(CMD_GET_WETNESS)
                if raw:
                    parsed = parse_response(raw)
                    if parsed:
                        _apply(new_data, parsed)
                        received += 1
            except BraavaProtocolError as err:
                _LOGGER.debug("GET_WETNESS failed: %s", err)
            except Exception as err:
                _LOGGER.debug("GET_WETNESS error: %s", err)

            # GET_ROOM_CONFINE
            try:
                raw = await self._send_robot_command(CMD_GET_ROOM_CONFINE)
                if raw:
                    parsed = parse_response(raw)
                    if parsed:
                        _apply(new_data, parsed)
                        received += 1
            except BraavaProtocolError as err:
                _LOGGER.debug("GET_ROOM_CONFINE failed: %s", err)
            except Exception as err:
                _LOGGER.debug("GET_ROOM_CONFINE error: %s", err)

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
        # Trigger an immediate status poll so the UI updates promptly
        await asyncio.sleep(1.0)
        await self._poll()

    async def async_stop_cleaning(self) -> None:
        """Send STOP_CLEAN command (0x11)."""
        await self._send_with_retry(CMD_STOP_CLEAN)
        _LOGGER.info("STOP_CLEAN sent to Braava 240")
        await asyncio.sleep(1.0)
        await self._poll()

    async def async_beep(self) -> None:
        """Send BEEP command (0x0D). Requires remote control mode to be active."""
        await self._send_robot_command(CMD_REMOTE_CONTROL, payload=bytes([1]))
        try:
            await self._send_robot_command(CMD_BEEP)
            _LOGGER.info("BEEP sent to Braava 240")
        finally:
            await self._send_robot_command(CMD_REMOTE_CONTROL, payload=bytes([0]))

    async def async_set_volume(self, level: int) -> None:
        """Send SET_VOLUME command (0x04)."""
        await self._send_robot_command(CMD_SET_VOLUME, payload=bytes([level]))
        _LOGGER.info("SET_VOLUME(%d) sent to Braava 240", level)

    async def async_set_wetness(self, pad_type: int, level: int) -> None:
        """Send SET_WETNESS command (0x02). payload: [type, level]."""
        await self._send_robot_command(CMD_SET_WETNESS, payload=bytes([pad_type, level]))
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
        encoded = name.encode("utf-8")[:19]  # Leave room for null terminator
        payload = encoded + b"\x00" * (20 - len(encoded))  # Pad to 20 bytes
        await self._send_robot_command(CMD_SET_NAME, payload=payload)
        self.data["robot_name"] = encoded.decode("utf-8")
        _LOGGER.info("SET_NAME('%s') sent to Braava 240", name)

    async def async_set_room_confine(self, enabled: bool) -> None:
        """Send SET_ROOM_CONFINE command (0x08). payload: 0=off, 1=on."""
        await self._send_robot_command(CMD_SET_ROOM_CONFINE, payload=bytes([int(enabled)]))
        self.data["room_confine"] = enabled
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
