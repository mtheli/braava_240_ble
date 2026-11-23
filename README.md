# iRobot Braava 240 BLE – Home Assistant Integration

[![Home Assistant Marketplace](https://img.shields.io/badge/Marketplace-listed-41BDF5.svg)](https://www.home-assistant.io/integrations/marketplace/)
[![GitHub Release](https://img.shields.io/github/v/release/mtheli/braava_240_ble)](https://github.com/mtheli/braava_240_ble/releases)
[![License: MIT](https://img.shields.io/github/license/mtheli/braava_240_ble)](LICENSE)

This is a custom component for Home Assistant to control the **iRobot Braava 240** mopping robot.

The integration connects to the robot via **Bluetooth Low Energy (BLE)** to start and stop cleaning, show battery, status and the attached pad, adjust wetness, volume and room confinement, and read lifetime statistics. All communication is fully local -- no cloud, no iRobot account required.

<a href="images/screenshot01.png"><img src="images/screenshot01.png" alt="Braava 240 device page in Home Assistant" width="860"></a>

---

## Table of Contents

- [Dashboard Cards](#dashboard-cards)
  - [Vacuum Card by Denys Dovhan](#vacuum-card-by-denys-dovhan)
  - [Vacuum Card by juju35](#vacuum-card-by-juju35)
- [Supported Languages](#supported-languages)
- [Features](#features)
- [Entities](#entities)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [BLE Protocol](#ble-protocol)
- [Supported Robot Commands](#supported-robot-commands)
- [Screenshots](#screenshots)

---

## Dashboard Cards

The integration has no card of its own. Two community cards work with it; both are **separate projects and not bundled with this integration** -- install the one you prefer in addition from the Marketplace.

### Vacuum Card by Denys Dovhan

The [Vacuum Card](https://github.com/denysdovhan/vacuum-card) works with any vacuum entity and is configured by hand.

<a href="images/screenshot_card.png"><img src="images/screenshot_card.png" alt="Vacuum Card with the Braava 240" width="484"></a>

The vacuum entity exposes `start`, `stop` and `locate` (beep) actions, and additional attributes (`pad_type`, `cleaning_mode`, `robot_name`, `runtime_minutes`, `mission_status`, `battery_voltage_v`) that can be used as stats. For translated values, reference the sensor entity directly via `entity_id` instead of using `attribute`.

#### Example configuration

```yaml
type: custom:vacuum-card
entity: vacuum.braava_240
battery_entity: sensor.braava_240_battery
image: https://raw.githubusercontent.com/mtheli/braava_240_ble/master/images/braava-jet-240.svg
stats:
  default:
    - attribute: robot_name
      subtitle: Name
    - entity_id: sensor.braava_240_cleaning_pad
      subtitle: Pad
    - attribute: cleaning_mode
      subtitle: Mode
  cleaning:
    - attribute: runtime_minutes
      subtitle: Runtime
      unit: min
    - entity_id: sensor.braava_240_cleaning_pad
      subtitle: Pad
shortcuts:
  - name: Spot
    icon: mdi:target
    service: select.select_option
    service_data:
      entity_id: select.braava_240_cleaning_mode
      option: spot
  - name: Normal
    icon: mdi:map-marker-path
    service: select.select_option
    service_data:
      entity_id: select.braava_240_cleaning_mode
      option: normal
```

### Vacuum Card by juju35

The [Vacuum Card by juju35](https://github.com/juju35/ha-vacuum-card) recognises the Braava 240 on its own: it picks the robot's entities from the device, shows the attached pad and its wetness, the runtime while cleaning, the error reason and the lifetime statistics, and offers only the buttons the robot supports (start, stop, locate).

<a href="images/screenshot_robot_vacuum_card.png"><img src="images/screenshot_robot_vacuum_card.png" alt="Vacuum Card by juju35 with the Braava 240 while cleaning" width="424"></a>

Both cards register the type `custom:vacuum-card`. Use `custom:robot-vacuum-card` for this one, it is always available:

```yaml
type: custom:robot-vacuum-card
device: Braava 240
```

---

## Supported Languages

| Language | Code | Comment |
|----------|------|---------|
| English  | en | |
| Deutsch  | de | |

Setup dialogs, entity names, entity states and the `mission_status` and `pad_type` attributes follow the language configured in your Home Assistant instance. If your language is not yet supported, it falls back to English. Contributions for additional languages are welcome — copy `custom_components/braava_240_ble/translations/en.json`, translate the values and open a pull request.

---

## Features

- **Auto-discovery** – The Braava 240 is automatically detected when powered on and within BLE range
- **Start/stop cleaning** – Via the vacuum entity in Home Assistant
- **Cleaning mode** – Normal (full room) or Spot cleaning
- **Battery level** – Calculated from actual charge current (more reliable than the internal raw value)
- **Robot status** – Ready, Cleaning, Error
- **Pad detection** – Shows the currently attached pad type (wet, damp, dry, reusable, etc.)
- **Wetness control** – Per-pad-type wetness levels (Low / Medium / High)
- **Volume control** – Adjustable speaker volume (0–100)
- **Robot name** – Read and change the robot's Bluetooth name
- **Room confinement** – Enable/disable room confinement mode
- **Lifetime statistics** – Total missions, successful/failed missions, cleaning time, average duration
- **Find robot** – Beep button to locate the robot
- **Device information** – Serial number, firmware version, hardware revision via GATT Device Information Service
- **Power off** – Power-off button to fully shut down the robot (disabled by default)

---

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| Braava 240 | Vacuum | Start/stop/locate cleaning |
| Status | Sensor | Robot state (Ready / Cleaning / Error) |
| Battery | Sensor | Battery level in percent |
| Cleaning Pad | Sensor | Detected pad type |
| Connected | Binary Sensor | BLE connection status |
| Cleaning | Binary Sensor | Active cleaning yes/no |
| Cleaning Mode | Select | Normal / Spot |
| Wetness Wet Pad | Select | Wetness level for wet pads |
| Wetness Damp Pad | Select | Wetness level for damp pads |
| Wetness Reusable Wet | Select | Wetness level for reusable wet pads |
| Wetness Reusable Damp | Select | Wetness level for reusable damp pads |
| Volume | Number | Speaker volume (0–100) |
| Name | Text | Robot name (read/write, max 19 characters) |
| Room Confinement | Switch | Room confinement on/off |
| Total Missions | Sensor | Lifetime missions started (diagnostic) |
| Successful Missions | Sensor | Lifetime missions completed (diagnostic) |
| Failed Missions | Sensor | Lifetime missions failed (diagnostic) |
| Total Cleaning Time | Sensor | Lifetime cleaning time in hours (diagnostic) |
| Average Mission Time | Sensor | Average mission duration in minutes (diagnostic) |
| Beep | Button | Trigger an audible beep to locate the robot |
| Reset Wetness | Button | Reset all wetness levels to defaults (Medium) |
| Power Off | Button | Fully shut down the robot (must be manually enabled) |

---

## Prerequisites

- Home Assistant 2025.1.0 or newer
- Bluetooth adapter on the Home Assistant host (built-in or USB dongle)
- iRobot Braava 240

---

## Installation

### Marketplace (recommended)

This integration is listed in the Home Assistant **Marketplace**. Click the button below, or go to **Settings → Marketplace** and search for "Braava". Select **Install** and the setup starts right away, no restart needed.

[![Open in the Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=mtheli&repository=braava_240_ble&category=integration)

On Home Assistant before 2026.11, the button opens the repository in [HACS](https://hacs.xyz/docs/use/) instead. Download it there and restart Home Assistant.

<details>
<summary>Manual installation</summary>

1. Copy the `custom_components/braava_240_ble/` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

</details>

---

## Configuration

1. Turn on the Braava 240 and place it within BLE range
2. Home Assistant will automatically discover the robot via the Bluetooth integration
3. Click **Configure** in the notification and confirm

If the robot is not discovered automatically:
**Settings** → **Devices & Services** → **Add Integration** → "iRobot Braava 240 BLE"

---

## BLE Protocol

This integration communicates directly with the Braava 240 via BLE — **no cloud service** and **no iRobot account** required. All communication is fully local.

The robot uses a two-layer GATT protocol:

- **Transport layer** – Manages data transfer via two BLE characteristics (command + status)
- **Robot command layer** – The actual commands (query status, start cleaning, etc.) are transferred as packets via a data characteristic

For a detailed technical description of the BLE protocol, see [PROTOCOL.md](PROTOCOL.md).

---

## Supported Robot Commands

The Braava 240 firmware exposes 26 robot commands. This integration currently uses 19 of them:

| ID | Command | Status | Description |
|----|---------|--------|-------------|
| 0x00 | NOP | Implemented | No operation (transport layer) |
| 0x01 | GET_WETNESS | Implemented | Query wetness levels for all pad types |
| 0x02 | SET_WETNESS | Implemented | Set wetness level per pad type |
| 0x03 | GET_VOLUME | Implemented | Query speaker volume |
| 0x04 | SET_VOLUME | Implemented | Set speaker volume |
| 0x05 | SET_NAME | Implemented | Set robot name |
| 0x06 | GET_BBK_DATA | Implemented | Lifetime statistics (missions, runtime, errors) |
| 0x07 | GET_ROOM_CONFINE | Implemented | Query room confinement setting |
| 0x08 | SET_ROOM_CONFINE | Implemented | Enable/disable room confinement |
| 0x09 | REMOTE_CONTROL | Implemented | Enable/disable remote control mode |
| 0x0A | JOYSTICK | – | Manual joystick control |
| 0x0B | SPRAY | – | Trigger water spray |
| 0x0C | VIBRATE | Unused | Enable/disable vibration (requires remote control mode) |
| 0x0D | BEEP | Implemented | Trigger audible beep |
| 0x0E | SPOT_CLEAN | Implemented | Start spot cleaning |
| 0x0F | GET_APP_DATA | – | Query mission data (runtime, ending reason) |
| 0x10 | START_CLEAN | Implemented | Start full-room cleaning |
| 0x11 | STOP_CLEAN | Implemented | Stop active cleaning |
| 0x12 | GET_STATUS | Implemented | Query robot state and mission status |
| 0x13 | GET_BATTERY | Implemented | Query battery level and voltages |
| 0x14 | GET_PAD_TYPE | Implemented | Query attached pad type |
| 0x15 | POWER_OFF | Implemented | Power off the robot |
| 0x16 | GET_ROBOT_REGISTERED | – | Check registration status |
| 0x17 | SET_ROBOT_REGISTERED | – | Set registration status |
| 0x18 | GET_NAME | Implemented | Query robot name |
| 0x19 | FACTORY_RESET | – | Factory reset the robot |

---

## Screenshots

| Device Page | Sensors & Diagnostics |
| :---: | :---: |
| <a href="images/screenshot01.png"><img src="images/screenshot01.png" alt="Braava 240 device page" width="414"></a> | <a href="images/screenshot02.png"><img src="images/screenshot02.png" alt="Braava 240 sensors and diagnostics" width="300"></a> |

| Vacuum Card by Denys Dovhan | Vacuum Card by juju35 |
| :---: | :---: |
| <a href="images/screenshot_card.png"><img src="images/screenshot_card.png" alt="Vacuum Card by Denys Dovhan" width="360"></a> | <a href="images/screenshot_robot_vacuum_card.png"><img src="images/screenshot_robot_vacuum_card.png" alt="Vacuum Card by juju35" width="360"></a> |

---

## Disclaimer

This is an independent community project and is not affiliated with, endorsed by, or sponsored by iRobot. All product names, trademarks, and registered trademarks are property of their respective owners.

## License

[MIT](LICENSE)
