"""Binary sensor platform for the iRobot Braava 240 BLE integration.

Exposes:
  - Connected – BLE connection state
  - Cleaning  – True while the robot is on a mission
"""

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ROBOT_STATE_MISSION_IN_PROGRESS
from .helpers import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Braava 240 binary sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        BraavaConnectedSensor(coordinator),
        BraavaCleaningSensor(coordinator),
    ])


class BraavaConnectedSensor(CoordinatorEntity, BinarySensorEntity):
    """True when the BLE connection to the robot is active."""

    _attr_has_entity_name = True
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_connected"
        self._attr_device_info = device_info(coordinator.address)

    @property
    def is_on(self) -> bool:
        return self.coordinator._connected


class BraavaCleaningSensor(CoordinatorEntity, BinarySensorEntity):
    """True while the robot is actively mopping."""

    _attr_has_entity_name = True
    _attr_translation_key = "cleaning"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_cleaning"
        self._attr_device_info = device_info(coordinator.address)

    @property
    def is_on(self) -> bool:
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get("robot_state") == ROBOT_STATE_MISSION_IN_PROGRESS
