"""Shared device-info helper for the Braava 240 BLE integration."""

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

from .const import DOMAIN


def device_info(
    address: str,
    sw_version: str | None = None,
    hw_version: str | None = None,
    serial_number: str | None = None,
) -> DeviceInfo:
    """Return a DeviceInfo for the Braava 240."""
    info = DeviceInfo(
        identifiers={(DOMAIN, address)},
        connections={(CONNECTION_BLUETOOTH, address)},
        name="Braava 240",
        manufacturer="iRobot",
        model="Braava 240",
    )
    if sw_version:
        info["sw_version"] = sw_version
    if hw_version:
        info["hw_version"] = hw_version
    if serial_number:
        info["serial_number"] = serial_number
    return info
