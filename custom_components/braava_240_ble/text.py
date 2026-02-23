"""Text platform for the iRobot Braava 240 BLE integration.

Exposes the robot name as a read/write text entity.
"""

import logging

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .helpers import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Braava 240 text entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BraavaNameText(coordinator)])


class BraavaNameText(CoordinatorEntity, TextEntity):
    """Text entity for the Braava 240 robot name."""

    _attr_has_entity_name = True
    _attr_translation_key = "robot_name"
    _attr_icon = "mdi:robot-mower"
    _attr_mode = TextMode.TEXT
    _attr_native_max = 20

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_robot_name"
        self._attr_device_info = device_info(
            coordinator.address,
            sw_version=coordinator.sw_version,
            hw_version=coordinator.hw_version,
            serial_number=coordinator.serial_number,
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get("robot_name")
        return None

    async def async_set_value(self, value: str) -> None:
        """Set the robot name."""
        await self.coordinator.async_set_name(value)
        self.async_write_ha_state()
