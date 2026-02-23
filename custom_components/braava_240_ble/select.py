"""Select platform for the iRobot Braava 240 BLE integration.

Exposes:
  - Cleaning mode selector (Normal / Spot)
  - Wetness level selectors for each pad type (Low / Medium / High)
"""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    WETNESS_LEVEL_MAP,
    WETNESS_LEVEL_REVERSE,
    WETNESS_TYPE_DAMP,
    WETNESS_TYPE_REUSABLE_DAMP,
    WETNESS_TYPE_REUSABLE_WET,
    WETNESS_TYPE_WET,
)
from .helpers import device_info

_LOGGER = logging.getLogger(__name__)

CLEANING_MODES = ["normal", "spot"]
WETNESS_OPTIONS = list(WETNESS_LEVEL_MAP.values())  # ["low", "medium", "high"]

# Mapping: (translation_key, data_key for coordinator.data, SET_WETNESS type byte)
_WETNESS_ENTITIES = [
    ("wetness_wet",           "wetness_wet_str",           WETNESS_TYPE_WET),
    ("wetness_damp",          "wetness_damp_str",          WETNESS_TYPE_DAMP),
    ("wetness_reusable_wet",  "wetness_reusable_wet_str",  WETNESS_TYPE_REUSABLE_WET),
    ("wetness_reusable_damp", "wetness_reusable_damp_str", WETNESS_TYPE_REUSABLE_DAMP),
]


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Braava 240 select entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = [BraavaCleaningModeSelect(coordinator)]
    for trans_key, data_key, pad_type in _WETNESS_ENTITIES:
        entities.append(
            BraavaWetnessSelect(coordinator, trans_key, data_key, pad_type)
        )
    async_add_entities(entities)


class BraavaCleaningModeSelect(SelectEntity):
    """Select entity for choosing the cleaning mode."""

    _attr_has_entity_name = True
    _attr_translation_key = "cleaning_mode"
    _attr_icon = "mdi:map-marker-path"
    _attr_options = CLEANING_MODES

    def __init__(self, coordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address}_cleaning_mode"
        self._attr_device_info = device_info(
            coordinator.address,
            sw_version=coordinator.sw_version,
            hw_version=coordinator.hw_version,
            serial_number=coordinator.serial_number,
        )

    @property
    def current_option(self) -> str:
        return self._coordinator.cleaning_mode

    async def async_select_option(self, option: str) -> None:
        """Update the cleaning mode on the coordinator."""
        self._coordinator.cleaning_mode = option
        self.async_write_ha_state()


class BraavaWetnessSelect(CoordinatorEntity, SelectEntity):
    """Select entity for a pad-specific wetness level."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:water-percent"
    _attr_options = WETNESS_OPTIONS

    def __init__(self, coordinator, translation_key: str, data_key: str, pad_type: int) -> None:
        super().__init__(coordinator)
        self._data_key = data_key
        self._pad_type = pad_type
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{coordinator.address}_{translation_key}"
        self._attr_device_info = device_info(
            coordinator.address,
            sw_version=coordinator.sw_version,
            hw_version=coordinator.hw_version,
            serial_number=coordinator.serial_number,
        )

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get(self._data_key)
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the wetness level on the robot."""
        level = WETNESS_LEVEL_REVERSE[option]
        await self.coordinator.async_set_wetness(self._pad_type, level)
        # Update local state for responsive UI
        if self.coordinator.data is not None:
            self.coordinator.data[self._data_key] = option
            self.async_write_ha_state()
