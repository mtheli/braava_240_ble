"""Config flow for the iRobot Braava 240 BLE integration.

Discovery uses the service UUID filter defined in manifest.json.  The flow
auto-detects the device name and asks the user to confirm before creating
the config entry.
"""

import logging
from typing import Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import BRAAVA_NAME_PREFIXES, DOMAIN, IROBOT_MANUFACTURER_ID, SERVICE_UUID

_LOGGER = logging.getLogger(__name__)


def _is_braava_240(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if the discovered device looks like a Braava 240.

    Matches on any of:
      • device name starting with a known prefix ("Altadena", "iRobot Braav")
      • iRobot manufacturer ID (0x0600 = 1536) present in advertisement
      • Braava service UUID advertised
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

    # ── Step 1: auto BLE discovery ─────────────────────────────────────────────

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

    # ── Step 2: user confirmation ──────────────────────────────────────────────

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show confirmation form; create entry on submit."""
        assert self._discovery_info is not None

        if user_input is not None:
            return self.async_create_entry(
                title=_friendly_name(self._discovery_info),
                data={"address": self._discovery_info.address},
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": _friendly_name(self._discovery_info)},
        )

    # ── Fallback: manual entry (no active discovery) ───────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Shown when the user adds the integration manually."""
        if self._discovery_info:
            return await self.async_step_bluetooth_confirm(user_input)
        return self.async_abort(reason="no_devices_found")
