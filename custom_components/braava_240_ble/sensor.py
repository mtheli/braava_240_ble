"""Sensor platform for the iRobot Braava 240 BLE integration.

Exposes:
  - Battery percentage sensor
  - Robot state sensor (idle / cleaning / error)
  - Pad type sensor (which cleaning pad is attached)
"""

import logging

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PAD_TYPE_MAP
from .helpers import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Braava 240 sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        BraavaStateSensor(coordinator),
        BraavaBatterySensor(coordinator),
        BraavaPadTypeSensor(coordinator),
    ])


# ── Base ───────────────────────────────────────────────────────────────────────

class _BraavaSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Braava 240 sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, translation_key: str, data_key: str) -> None:
        super().__init__(coordinator)
        self._data_key = data_key
        self._attr_unique_id = f"{coordinator.address}_{data_key}"
        self._attr_translation_key = translation_key
        self._attr_device_info = device_info(
            coordinator.address,
            sw_version=coordinator.sw_version,
            hw_version=coordinator.hw_version,
            serial_number=coordinator.serial_number,
        )

    @property
    def available(self) -> bool:
        """Available while the robot is in BLE range (advertising)."""
        svc = bluetooth.async_last_service_info(
            self.hass, self.coordinator.address, connectable=True
        )
        return svc is not None

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get(self._data_key)
        return None


# ── Concrete sensors ───────────────────────────────────────────────────────────

class BraavaStateSensor(_BraavaSensorBase):
    """Enum sensor showing the robot cleaning state."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "cleaning", "error"]
    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "robot_state", "robot_state_str")

    @property
    def icon(self) -> str:
        state = self.native_value
        if state == "cleaning":
            return "mdi:robot-vacuum"
        if state == "error":
            return "mdi:robot-vacuum-alert"
        return "mdi:robot-vacuum-off"


class BraavaBatterySensor(_BraavaSensorBase):
    """Battery percentage sensor."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "battery", "battery_level")


class BraavaPadTypeSensor(_BraavaSensorBase):
    """Enum sensor showing the attached cleaning pad type."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(PAD_TYPE_MAP.values())
    _attr_icon = "mdi:spray-bottle"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "pad_type", "pad_type_str")

    @property
    def icon(self) -> str:
        pad = self.native_value
        if pad and "wet" in pad:
            return "mdi:water"
        if pad and "dry" in pad:
            return "mdi:broom"
        if pad == "no_pad":
            return "mdi:close-circle-outline"
        return "mdi:spray-bottle"
