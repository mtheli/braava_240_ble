#!/usr/bin/env python3
"""Offline test script for the Braava 240 BLE packet parser.

Run directly from VS Code (launch.json) or from the terminal:
    python3 scripts/test_parser.py

Paste real BLE notification bytes here to verify parsing without a robot.
Use Wireshark / nRF Sniffer or the Android HCI log to capture raw packets.
"""

import sys
import os

# Allow importing from the workspace root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.braava_240_ble.parser import build_robot_packet, parse_response
from custom_components.braava_240_ble.const import (
    CMD_GET_STATUS, CMD_GET_BATTERY, CMD_START_CLEAN, CMD_STOP_CLEAN,
)


# ── Built commands ─────────────────────────────────────────────────────────────
print("=== Robot command packets (checksum = SUM) ===")
for name, cmd_id in [
    ("GET_STATUS  (0x12)", CMD_GET_STATUS),
    ("GET_BATTERY (0x13)", CMD_GET_BATTERY),
    ("START_CLEAN (0x10)", CMD_START_CLEAN),
    ("STOP_CLEAN  (0x11)", CMD_STOP_CLEAN),
]:
    pkt = build_robot_packet(cmd_id)
    # Verify checksum: sum of all bytes except byte[2]
    chk_ok = pkt[2] == (sum(pkt[i] for i in range(len(pkt)) if i != 2) & 0xFF)
    print(f"  {name}  →  {pkt.hex(' ')}  (checksum valid: {chk_ok})")


# ── Synthetic response packets ─────────────────────────────────────────────────
print("\n=== Parsing synthetic responses ===")

# GET_STATUS response:
#   [cmd=0x12, total_size=6, checksum, runtime=5min, state=1=cleaning, mission=26=in_progress]
raw_status = bytearray([0x12, 0x06, 0x00, 0x05, 0x01, 0x1A])
raw_status[2] = sum(raw_status[i] for i in range(len(raw_status)) if i != 2) & 0xFF
print(f"\nGET_STATUS  raw: {bytes(raw_status).hex(' ')}")
print(f"            parsed: {parse_response(bytes(raw_status))}")

# GET_BATTERY response:
#   [cmd=0x13, total_size=14, checksum, level=75, min_v=3200mV, max_v=4200mV,
#    cur_v=3900mV, max_charge=1800mAh, cur_charge=1350mAh]
import struct
bat_payload = struct.pack("<BHHHHH",
    75,      # level %
    3200,    # min_voltage mV
    4200,    # max_voltage mV
    3900,    # current_voltage mV
    1800,    # max_charge mAh
    1350,    # current_charge mAh
)
total_size = 3 + len(bat_payload)
raw_bat = bytearray([0x13, total_size, 0x00]) + bytearray(bat_payload)
raw_bat[2] = sum(raw_bat[i] for i in range(len(raw_bat)) if i != 2) & 0xFF
print(f"\nGET_BATTERY raw: {bytes(raw_bat).hex(' ')}")
print(f"            parsed: {parse_response(bytes(raw_bat))}")


# ── Paste real captured bytes here ─────────────────────────────────────────────
print("\n=== Real captures (edit below) ===")
real_captures: list[tuple[str, str]] = [
    # ("label", "hex bytes space-separated"),
    # ("status from robot",  "12 06 3e 03 01 1a"),
    # ("battery from robot", "13 0e ... ..."),
]

for label, hex_str in real_captures:
    raw = bytes.fromhex(hex_str.replace(" ", ""))
    result = parse_response(raw)
    print(f"  {label}: {result}")

print("\nDone.")
