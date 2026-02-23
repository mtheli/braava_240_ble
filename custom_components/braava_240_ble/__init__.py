"""iRobot Braava 240 BLE integration for Home Assistant.

Communicates with the Braava 240 robot mop over Bluetooth Low Energy using
the GATT protocol reverse-engineered from the iRobot Home Android app.

Supported functionality:
  • Query robot status (idle / cleaning / error) and mission details
  • Query battery level
  • Start and stop cleaning missions
"""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BraavaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SWITCH, Platform.TEXT, Platform.VACUUM, Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Braava 240 integration from a config entry."""
    address = entry.data["address"]

    coordinator = BraavaDataUpdateCoordinator(
        hass,
        address,
        sw_version=entry.data.get("sw_version"),
        hw_version=entry.data.get("hw_version"),
        serial_number=entry.data.get("serial_number"),
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward setup to all platforms before starting the BLE loop so entities
    # are registered and available to receive the first data push.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start persistent BLE connection in the background
    coordinator.start_live_monitoring()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and disconnect from the robot."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: BraavaDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

    return unload_ok
