"""Constants for the Victron BT Devices integration."""

DOMAIN = "victron_bp"

CONF_KEY = "advertisement_key"  # 16-byte hex, decodes the Instant Readout broadcast
CONF_PIN = "pin"                # 6-digit BLE pairing PIN, default 000000
CONF_PRODUCT_ID = "product_id"  # cached from the advertisement, picks the catalog profile
CONF_MODE_ON = "mode_on"        # cached per-device VE_REG_MODE "on" value (BP 3, Orion 1, ...)

DEFAULT_PIN = "000000"

# Options (Configure dialog)
CONF_SCAN_INTERVAL = "scan_interval"        # seconds between reads
CONF_AUTO_RESET = "auto_reset"              # cure "Notify acquired" by itself
CONF_AUTO_RESET_MINUTES = "auto_reset_minutes"  # at most one automatic reset per this many minutes
UNPAIR = "unpair"                           # one-time action, not stored
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_AUTO_RESET = True
DEFAULT_AUTO_RESET_MINUTES = 30

# VE_REG_MODE (0x0200) values. "Off" (4) is the same on every product; the "on"
# value differs (3 on a BatteryProtect, 1 on an Orion), so it is learned/cached.
DEVICE_MODE_OFF = 4
DEFAULT_MODE_ON = 3

# GATT vendor service and characteristics, from birdie1/victron (VE.Direct Smart).
# The device exposes several; these are the ones that carry the value stream.
SERVICE_VREG = "306b0001-b081-4037-83dc-e59fcc3cdfd0"
CHAR_CONTROL = "306b0003-b081-4037-83dc-e59fcc3cdfd0"  # opcodes / short writes
CHAR_STREAM = "306b0004-b081-4037-83dc-e59fcc3cdfd0"   # chunked value stream (notify)

# Command opcodes, byte 0 of a control write. Confirmed byte-for-byte against
# birdie1/victron's captured app traffic: a set is 0x06 + instance +
# CBOR([vreg, value_bytes]) -- the app writes "06 00 82 19 0206 41 01" to set a
# register. (The keepalive "ping" is just such a set, so 0x06 is known to work.)
# 0x05 is get/subscribe. Our earlier 0x04 was a wrong guess -> "opcode not
# supported", which is why the switch had no effect.
OP_SUBSCRIBE = 0x05
OP_WRITE = 0x06
OP_PING = 0x06
