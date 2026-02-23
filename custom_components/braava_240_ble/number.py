"""Number platform for the iRobot Braava 240 BLE integration.

Exposes a volume control slider.
"""

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .helpers import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Braava 240 number entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BraavaVolumeNumber(coordinator)])


class BraavaVolumeNumber(CoordinatorEntity, NumberEntity):
    """Number entity for the Braava 240 speaker volume."""

    _attr_has_entity_name = True
    _attr_translation_key = "volume"
    _attr_icon = "mdi:volume-medium"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_volume"
        self._attr_device_info = device_info(
            coordinator.address,
            sw_version=coordinator.sw_version,
            hw_version=coordinator.hw_version,
            serial_number=coordinator.serial_number,
        )

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data:
            return self.coordinator.data.get("volume")
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the volume level on the robot."""
        level = int(value)
        await self.coordinator.async_set_volume(level)
        # Update local state immediately for responsive UI
        if self.coordinator.data is not None:
            self.coordinator.data["volume"] = level
            self.async_write_ha_state()
