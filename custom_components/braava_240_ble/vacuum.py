"""Vacuum (robot mop) platform for the iRobot Braava 240 BLE integration.

The Braava 240 is a robot mop.  Home Assistant's vacuum platform is the
closest match – it provides start/stop controls and maps the BraavaRobotState
enum to VacuumActivity states.

State mapping (BraavaRobotState → VacuumActivity):
    0 Idle                  → IDLE
    1 MissionInProgress     → CLEANING
    2 MissionEndedSuccess   → IDLE   (returned home)
    3 MissionEndedWithError → ERROR
"""

import logging

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MISSION_STATUS_MAP,
    ROBOT_STATE_MISSION_ERROR,
    ROBOT_STATE_MISSION_IN_PROGRESS,
)
from .helpers import device_info

_LOGGER = logging.getLogger(__name__)

_SUPPORTED_FEATURES = (
    VacuumEntityFeature.START
    | VacuumEntityFeature.STOP
    | VacuumEntityFeature.STATE
    | VacuumEntityFeature.LOCATE
)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Braava 240 vacuum entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BraavaVacuumEntity(coordinator)])


class BraavaVacuumEntity(CoordinatorEntity, StateVacuumEntity):
    """Represents the Braava 240 as a Home Assistant vacuum / robot-mop entity."""

    _attr_has_entity_name = True
    _attr_name = None  # entity name = device name
    _attr_supported_features = _SUPPORTED_FEATURES

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_vacuum"
        self._attr_device_info = device_info(
            coordinator.address,
            sw_version=coordinator.sw_version,
            hw_version=coordinator.hw_version,
            serial_number=coordinator.serial_number,
        )

    # ── State ──────────────────────────────────────────────────────────────────

    @property
    def activity(self) -> VacuumActivity | None:
        """Return the current robot activity."""
        if not self.coordinator.data:
            return None
        robot_state = self.coordinator.data.get("robot_state")
        if robot_state is None:
            return None
        if robot_state == ROBOT_STATE_MISSION_IN_PROGRESS:
            return VacuumActivity.CLEANING
        if robot_state == ROBOT_STATE_MISSION_ERROR:
            return VacuumActivity.ERROR
        return VacuumActivity.IDLE

    @property
    def available(self) -> bool:
        """Robot is available when the BLE connection is active."""
        return self.coordinator._connected

    @property
    def extra_state_attributes(self) -> dict:
        """Expose additional Braava state as attributes."""
        if not self.coordinator.data:
            return {}
        attrs: dict = {}
        runtime = self.coordinator.data.get("runtime_minutes")
        if runtime is not None:
            attrs["runtime_minutes"] = runtime
        mission_status = self.coordinator.data.get("mission_status")
        if mission_status is not None:
            attrs["mission_status"] = MISSION_STATUS_MAP.get(
                mission_status, str(mission_status)
            )
        voltage = self.coordinator.data.get("current_voltage")
        if voltage is not None:
            attrs["battery_voltage_v"] = round(voltage, 2)
        pad = self.coordinator.data.get("pad_type_str")
        if pad is not None:
            attrs["pad_type"] = pad
        attrs["cleaning_mode"] = self.coordinator.cleaning_mode
        return attrs

    # ── Commands ───────────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Start a cleaning mission."""
        await self.coordinator.async_start_cleaning()

    async def async_stop(self, **kwargs) -> None:
        """Stop / terminate the current cleaning mission."""
        await self.coordinator.async_stop_cleaning()

    async def async_locate(self, **kwargs) -> None:
        """Locate the robot by triggering an audible beep."""
        await self.coordinator.async_beep()
