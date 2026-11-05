"""Decode the Smart BatteryProtect "Instant Readout" BLE advertisement.

This is the read-only path: the device broadcasts an encrypted manufacturer
record roughly once a second. It is AES-128-CTR encrypted with the device's
16-byte key (VictronConnect -> product info -> "Instant readout via Bluetooth"
-> show encryption data). The GATT path in ``gatt.py`` is what adds control.

Layout (Victron manufacturer id 0x02E1), matching the public victron-ble spec
and the app's BroadcastDataSmartBatteryProtect; verify the field scaling once a
real device key is available:

    0x10                prefix
    u16 LE              product id (0xA3B0..0xA3B3 for BatteryProtect)
    u8                  record type (0x09 = BatteryProtect)
    u16 LE              nonce / data counter -> AES-CTR IV
    u8                  first byte of the encryption key (sanity check)
    ...                 AES-CTR encrypted payload

Decrypted payload, read LSB-first:
    device_state    8
    output_state    8
    error_code      8
    alarm_reason   16
    warning_reason 16
    input_voltage  16  signed, 0.01 V
    output_voltage 16  unsigned, 0.01 V
    off_reason     32
"""

from __future__ import annotations

from dataclasses import dataclass

VICTRON_MANUFACTURER_ID = 0x02E1
RECORD_TYPE_BATTERY_PROTECT = 0x09


def product_id_from_raw(raw: bytes | None) -> int | None:
    """Read the product id (u16 LE at offset 1) from a Victron 0x10 record."""
    if not raw or len(raw) < 3 or raw[0] != 0x10:
        return None
    return int.from_bytes(raw[1:3], "little")


class BitReader:
    """Reads fields LSB-first out of a little-endian byte string."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._bit = 0

    def read(self, bits: int, signed: bool = False) -> int:
        value = 0
        for i in range(bits):
            byte_index = (self._bit + i) // 8
            bit_index = (self._bit + i) % 8
            if byte_index < len(self._data):
                value |= ((self._data[byte_index] >> bit_index) & 1) << i
        self._bit += bits
        if signed and value & (1 << (bits - 1)):
            value -= 1 << bits
        return value


@dataclass
class BatteryProtectState:
    device_state: int
    output_state: int
    error_code: int
    alarm_reason: int
    warning_reason: int
    input_voltage: float | None   # V
    output_voltage: float | None  # V
    off_reason: int


def _aes_ctr(key: bytes, counter_init: int, payload: bytes) -> bytes:
    # imported lazily so importing this module needs no crypto dependency
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = counter_init.to_bytes(16, "little")
    decryptor = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()
    return decryptor.update(payload) + decryptor.finalize()


def parse_manufacturer_data(raw: bytes, key: bytes) -> BatteryProtectState:
    """Decrypt and decode one Victron manufacturer record.

    ``raw`` is the manufacturer-specific data for id 0x02E1 (without the id).
    ``key`` is the 16-byte device encryption key.
    """
    if raw[0] != 0x10:
        raise ValueError("not an Instant Readout product record")
    record_type = raw[3]
    if record_type != RECORD_TYPE_BATTERY_PROTECT:
        raise ValueError(f"record type 0x{record_type:02x} is not a BatteryProtect")
    nonce = int.from_bytes(raw[4:6], "little")
    if raw[6] != key[0]:
        raise ValueError("encryption key does not match this device")

    plain = _aes_ctr(key, nonce, raw[7:])
    r = BitReader(plain)
    device_state = r.read(8)
    output_state = r.read(8)
    error_code = r.read(8)
    alarm_reason = r.read(16)
    warning_reason = r.read(16)
    input_raw = r.read(16, signed=True)
    output_raw = r.read(16)
    off_reason = r.read(32)

    return BatteryProtectState(
        device_state=device_state,
        output_state=output_state,
        error_code=error_code,
        alarm_reason=alarm_reason,
        warning_reason=warning_reason,
        input_voltage=None if input_raw == 0x7FFF else input_raw / 100,
        output_voltage=None if output_raw == 0xFFFF else output_raw / 100,
        off_reason=off_reason,
    )
