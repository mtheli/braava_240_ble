"""Config flow for the iRobot Braava 240 BLE integration.

Discovery uses the service UUID filter defined in manifest.json.  The flow
auto-detects the device name and asks the user to confirm before creating
the config entry.

After confirmation, the flow connects to the robot via BLE to read device
information (serial number, firmware/hardware version) and verify that the
expected GATT characteristics are present.  A summary page is shown before
the entry is created.
"""

import logging
from typing import Any

import voluptuous as vol

from bleak import BleakClient
from bleak_retry_connector import establish_connection

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    BRAAVA_NAME_PREFIXES,
    CHAR_UUID_COMMAND,
    CHAR_UUID_DATA,
    CHAR_UUID_HEARTBEAT,
    CHAR_UUID_STATUS,
    DOMAIN,
    GATT_FIRMWARE_REV,
    GATT_HARDWARE_REV,
    GATT_SERIAL_NUMBER,
    GATT_SOFTWARE_REV,
    IROBOT_MANUFACTURER_ID,
    SERVICE_UUID,
)

_LOGGER = logging.getLogger(__name__)


def _is_braava_240(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if the discovered device looks like a Braava 240.

    Matches on any of:
      - device name starting with a known prefix ("Altadena", "iRobot Braav")
      - iRobot manufacturer ID (0x0600 = 1536) present in advertisement
      - Braava service UUID advertised
    """
    if discovery_info.name:
        for prefix in BRAAVA_NAME_PREFIXES:
            if discovery_info.name.startswith(prefix):
                return True

    if IROBOT_MANUFACTURER_ID in discovery_info.manufacturer_data:
        return True

    advertised_uuids = [str(u).lower() for u in discovery_info.service_uuids]
    return SERVICE_UUID in advertised_uuids


def _friendly_name(discovery_info: BluetoothServiceInfoBleak) -> str:
    """Return a human-readable device name for the UI."""
    return discovery_info.name or f"Braava 240 ({discovery_info.address})"


class Braava240ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for the iRobot Braava 240."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._fetched_hw_version: str | None = None
        self._fetched_sw_version: str | None = None
        self._fetched_serial_number: str | None = None
        self._fetched_characteristics: dict[str, bool] = {}

    # ── Device info fetching ────────────────────────────────────────────────────

    async def _async_fetch_device_info(self, address: str) -> None:
        """Connect to the robot and read GATT Device Information Service."""
        device = async_ble_device_from_address(self.hass, address)
        if not device:
            raise ConnectionError("BLE device not found")

        client = await establish_connection(
            BleakClient, device, "Braava 240 Config", timeout=15
        )

        try:
            # Check which characteristics are available
            services = client.services
            self._fetched_characteristics = {
                CHAR_UUID_DATA: services.get_characteristic(CHAR_UUID_DATA) is not None,
                CHAR_UUID_COMMAND: services.get_characteristic(CHAR_UUID_COMMAND) is not None,
                CHAR_UUID_STATUS: services.get_characteristic(CHAR_UUID_STATUS) is not None,
                CHAR_UUID_HEARTBEAT: services.get_characteristic(CHAR_UUID_HEARTBEAT) is not None,
            }

            # Read GATT Device Information Service (standard 0x180A)
            gatt_reads = [
                (GATT_SERIAL_NUMBER, "_fetched_serial_number"),
                (GATT_FIRMWARE_REV, "_fetched_sw_version"),
                (GATT_HARDWARE_REV, "_fetched_hw_version"),
                (GATT_SOFTWARE_REV, "_fetched_sw_version"),  # fallback
            ]
            for uuid, attr in gatt_reads:
                if getattr(self, attr) is not None:
                    continue
                try:
                    raw = await client.read_gatt_char(uuid)
                    value = bytes(raw).decode("utf-8", errors="replace").strip("\x00 ")
                    if value:
                        setattr(self, attr, value)
                except Exception:
                    _LOGGER.debug("GATT char %s not available", uuid)
        finally:
            await client.disconnect()

        # Discard meaningless placeholder serial numbers
        if self._fetched_serial_number and self._fetched_serial_number.strip("0") == "":
            self._fetched_serial_number = None

    def _get_characteristics_text(self) -> str:
        """Format characteristics status for display."""
        labels = {
            CHAR_UUID_DATA: "cc1 (Data)",
            CHAR_UUID_COMMAND: "cc2 (Command)",
            CHAR_UUID_STATUS: "cc3 (Status)",
            CHAR_UUID_HEARTBEAT: "cc4 (Heartbeat)",
        }
        lines = []
        for uuid, found in self._fetched_characteristics.items():
            label = labels.get(uuid, uuid)
            icon = "\u2705" if found else "\u274c"
            lines.append(f"{icon} {label}")
        return "\n".join(lines)

    # ── Step 1: auto BLE discovery ──────────────────────────────────────────────

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Called automatically when HA discovers a matching BLE device."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        if not _is_braava_240(discovery_info):
            return self.async_abort(reason="not_supported")

        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": _friendly_name(discovery_info)}

        return await self.async_step_bluetooth_confirm()

    # ── Step 2: user confirmation → fetch device info ───────────────────────────

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show confirmation form; on submit connect and read device info."""
        assert self._discovery_info is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await self._async_fetch_device_info(self._discovery_info.address)
                return await self.async_step_show_device_info()
            except Exception:
                _LOGGER.error(
                    "Failed to connect to %s", self._discovery_info.address,
                    exc_info=True,
                )
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": _friendly_name(self._discovery_info)},
            errors=errors,
        )

    # ── Step 3: show device info ────────────────────────────────────────────────

    async def async_step_show_device_info(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show discovered device info and create entry on submit."""
        if user_input is not None:
            return self.async_create_entry(
                title=_friendly_name(self._discovery_info),
                data={
                    "address": self._discovery_info.address,
                    "hw_version": self._fetched_hw_version,
                    "sw_version": self._fetched_sw_version,
                    "serial_number": self._fetched_serial_number,
                },
            )

        name = _friendly_name(self._discovery_info)

        return self.async_show_form(
            step_id="show_device_info",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": name,
                "hw_version": self._fetched_hw_version or "Unknown",
                "fw_version": self._fetched_sw_version or "Unknown",
                "serial_number": self._fetched_serial_number or "Unknown",
                "characteristics": self._get_characteristics_text(),
            },
        )

    # ── Fallback: manual entry (no active discovery) ────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Shown when the user adds the integration manually."""
        if self._discovery_info:
            return await self.async_step_bluetooth_confirm(user_input)
        return self.async_abort(reason="no_devices_found")
