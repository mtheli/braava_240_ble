"""Button platform for the iRobot Braava 240 BLE integration.

Exposes a power-off button that shuts down the robot completely.
After power-off the robot must be turned on physically.
"""

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity

from .const import DOMAIN
from .helpers import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Braava 240 buttons from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BraavaPowerOffButton(coordinator)])


class BraavaPowerOffButton(ButtonEntity):
    """Button that powers off the Braava 240."""

    _attr_has_entity_name = True
    _attr_translation_key = "power_off"
    _attr_icon = "mdi:power"
    _attr_entity_registry_enabled_default = False  # disabled by default – destructive action

    def __init__(self, coordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address}_power_off"
        self._attr_device_info = device_info(
            coordinator.address,
            sw_version=coordinator.sw_version,
            hw_version=coordinator.hw_version,
            serial_number=coordinator.serial_number,
        )

    async def async_press(self) -> None:
        """Send power-off command to the robot."""
        _LOGGER.info("Power off requested for Braava 240")
        await self._coordinator.async_power_off()
