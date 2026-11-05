"""Minimal CBOR encoder/decoder for the subset Victron's VE.Direct Smart BLE uses.

Every VE.Direct register (VREG) operation is wrapped in CBOR before it goes
over the GATT vendor service. The protocol only uses unsigned ints, byte
strings, arrays and maps, so this intentionally covers just those major types
(0 unsigned, 1 negative, 2 byte string, 3 text, 4 array, 5 map). That keeps the
code small and easy to check against a capture.

Spec: RFC 8949. Verified against captured frames.
"""

from __future__ import annotations

from typing import Any


def _encode_head(major: int, value: int) -> bytes:
    major <<= 5
    if value < 24:
        return bytes([major | value])
    if value < 0x100:
        return bytes([major | 24, value])
    if value < 0x10000:
        return bytes([major | 25, value >> 8, value & 0xFF])
    if value < 0x100000000:
        return bytes([major | 26]) + value.to_bytes(4, "big")
    return bytes([major | 27]) + value.to_bytes(8, "big")


def encode(obj: Any) -> bytes:
    """Encode a Python value into CBOR."""
    if isinstance(obj, bool):  # bool before int: bool is an int subclass
        return bytes([0xF5 if obj else 0xF4])
    if isinstance(obj, int):
        if obj >= 0:
            return _encode_head(0, obj)
        return _encode_head(1, -obj - 1)
    if isinstance(obj, (bytes, bytearray)):
        return _encode_head(2, len(obj)) + bytes(obj)
    if isinstance(obj, str):
        raw = obj.encode("utf-8")
        return _encode_head(3, len(raw)) + raw
    if isinstance(obj, (list, tuple)):
        out = _encode_head(4, len(obj))
        for item in obj:
            out += encode(item)
        return out
    if isinstance(obj, dict):
        out = _encode_head(5, len(obj))
        for key, val in obj.items():
            out += encode(key) + encode(val)
        return out
    raise TypeError(f"cannot CBOR-encode {type(obj).__name__}")


def _decode(data: bytes, pos: int) -> tuple[Any, int]:
    first = data[pos]
    major, info = first >> 5, first & 0x1F
    pos += 1
    if info < 24:
        value = info
    elif info == 24:
        value = data[pos]; pos += 1
    elif info == 25:
        value = int.from_bytes(data[pos:pos + 2], "big"); pos += 2
    elif info == 26:
        value = int.from_bytes(data[pos:pos + 4], "big"); pos += 4
    elif info == 27:
        value = int.from_bytes(data[pos:pos + 8], "big"); pos += 8
    else:
        raise ValueError(f"unsupported CBOR additional info {info}")

    if major == 0:
        return value, pos
    if major == 1:
        return -1 - value, pos
    if major == 2:
        return data[pos:pos + value], pos + value
    if major == 3:
        return data[pos:pos + value].decode("utf-8"), pos + value
    if major == 4:
        items = []
        for _ in range(value):
            item, pos = _decode(data, pos)
            items.append(item)
        return items, pos
    if major == 5:
        result = {}
        for _ in range(value):
            key, pos = _decode(data, pos)
            val, pos = _decode(data, pos)
            result[key] = val
        return result, pos
    if major == 7:
        if info == 20:
            return False, pos
        if info == 21:
            return True, pos
        if info == 22:
            return None, pos
    raise ValueError(f"unsupported CBOR major type {major}")


def decode(data: bytes) -> Any:
    """Decode a single CBOR item from the front of ``data``."""
    value, _ = _decode(bytes(data), 0)
    return value
