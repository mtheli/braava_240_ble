"""Switch platform for the iRobot Braava 240 BLE integration.

Exposes room confinement as a toggle switch.
"""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .helpers import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Braava 240 switch entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BraavaRoomConfineSwitch(coordinator)])


class BraavaRoomConfineSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for the Braava 240 room confinement setting."""

    _attr_has_entity_name = True
    _attr_translation_key = "room_confine"
    _attr_icon = "mdi:wall"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_room_confine"
        self._attr_device_info = device_info(
            coordinator.address,
            sw_version=coordinator.sw_version,
            hw_version=coordinator.hw_version,
            serial_number=coordinator.serial_number,
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data:
            return self.coordinator.data.get("room_confine")
        return None

    async def async_turn_on(self, **kwargs) -> None:
        """Enable room confinement."""
        await self.coordinator.async_set_room_confine(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable room confinement."""
        await self.coordinator.async_set_room_confine(False)
        self.async_write_ha_state()
