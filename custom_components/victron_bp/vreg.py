"""VE.Direct registers (VREGs) for the Smart BatteryProtect.

The ``MODE`` register and its values ("on" 3, "off" 4 on a BatteryProtect) are
confirmed on the device. The telemetry VREGs are the standard VE.Direct ones;
mark them "to verify" until checked against a real device.
"""

from __future__ import annotations

from enum import IntEnum

# Accepted product IDs:
#   Smart BatteryProtect  0xA3B0..0xA3B3
#   Orion Smart DC-DC     0xA3C0..0xA3D6  (same VE.Direct Smart GATT transport and
#                         the same control register 0x0200 / off-reason 0x0207,
#                         so the Orion works as a close-range test proxy and the
#                         switch code built for one applies to both).
PRODUCT_IDS = frozenset(range(0xA3B0, 0xA3B4)) | frozenset(range(0xA3C0, 0xA3D7))

# --- control --------------------------------------------------------------
VE_REG_MODE = 0x0200            # read/write, the output control. Values below.
VE_REG_DEVICE_STATE = 0x0201    # read, device state machine
VE_REG_DEVICE_OFF_REASON = 0x0207


class Mode(IntEnum):
    """Values written to VE_REG_MODE (dbus /Mode)."""

    CHARGE_ON = 1
    DISCHARGE_ON = 2
    ON = 3
    OFF = 4


# --- telemetry (to verify against hardware) -------------------------------
VE_REG_DC_INPUT_VOLTAGE = 0xED8D   # 0.01 V
VE_REG_DC_OUTPUT_VOLTAGE = 0xEDB1  # 0.01 V  (placeholder: confirm with capture)
VE_REG_INTERNAL_TEMP = 0xEDEC      # 0.01 degC
VE_REG_ERROR_CODE = 0xED3C
VE_REG_UPTIME = 0x0120
VE_REG_LOAD_STATE = 0xED8E         # output on/off (placeholder: confirm)

# Settings
VE_REG_SBP_MODE = 0xEDCA           # /Settings/SbpMode (placeholder: confirm)
VE_REG_RELAY_MODE = 0xEDD8         # VE_REG_CHR_RELAY_CONTROL, seen in app comment
