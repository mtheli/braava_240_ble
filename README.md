# iRobot Braava 240 BLE – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/mtheli/braava_240_ble)](https://github.com/mtheli/braava_240_ble/releases)
[![License: MIT](https://img.shields.io/github/license/mtheli/braava_240_ble)](LICENSE)

Custom Home Assistant integration to control the iRobot Braava 240 mopping robot via Bluetooth Low Energy (BLE).

![Home Assistant Screenshot](images/screenshot.png)

## Features

- **Auto-discovery** – The Braava 240 is automatically detected when powered on and within BLE range
- **Start/stop cleaning** – Via the vacuum entity in Home Assistant
- **Battery level** – Calculated from actual charge current (more reliable than the internal raw value)
- **Robot status** – Ready, Cleaning, Error
- **Pad detection** – Shows the currently attached pad type (wet, damp, dry, reusable, etc.)
- **Device information** – Serial number, firmware version, hardware revision via GATT Device Information Service
- **Power off** – Power-off button to fully shut down the robot (disabled by default)
- **Multilingual** – English and German

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| Braava 240 | Vacuum | Start/stop cleaning |
| Status | Sensor | Robot state (Ready / Cleaning / Error) |
| Battery | Sensor | Battery level in percent |
| Pad | Sensor | Detected pad type |
| Connected | Binary Sensor | BLE connection status |
| Cleaning | Binary Sensor | Active cleaning yes/no |
| Power Off | Button | Fully shut down the robot (must be manually enabled) |

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. **Integrations** → three-dot menu → **Custom repositories**
3. Enter the repository URL and select **Integration** as the category
4. Install "iRobot Braava 240 BLE"
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/braava_240_ble/` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Setup

1. Turn on the Braava 240 and place it within BLE range
2. Home Assistant will automatically discover the robot via the Bluetooth integration
3. Click **Configure** in the notification and confirm

If the robot is not discovered automatically:
**Settings** → **Devices & Services** → **Add Integration** → "iRobot Braava 240 BLE"

## Requirements

- Home Assistant 2024.11.0 or newer
- Bluetooth adapter on the Home Assistant host (built-in or USB dongle)
- iRobot Braava 240 (codename "Altadena")

## BLE Protocol

This integration communicates directly with the Braava 240 via BLE — **no cloud service** and **no iRobot account** required. All communication is fully local.

The robot uses a two-layer GATT protocol:

- **Transport layer** – Manages data transfer via two BLE characteristics (command + status)
- **Robot command layer** – The actual commands (query status, start cleaning, etc.) are transferred as packets via a data characteristic

## License

MIT
