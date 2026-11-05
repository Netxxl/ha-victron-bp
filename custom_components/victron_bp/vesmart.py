"""VE.Direct Smart framing helpers for reading registers over GATT.

The request side is small and well understood from birdie1/victron: a control
write of ``[opcode, instance, CBOR([vreg])]`` subscribes to a register, after
which the device streams its value in notifications. The notification framing
is the part still to be confirmed against a real device, so the decoder here is
deliberately a tolerant scanner that reports what it recognises and leaves the
raw bytes in the log for the rest.
"""

from __future__ import annotations

import logging

from . import cbor
from .catalog import VregDef
from .const import OP_PING, OP_SUBSCRIBE, OP_WRITE

_LOGGER = logging.getLogger(__name__)

# Chunk-transport flow control, written to the ...0002 characteristic. Confirmed
# in birdie's captured frames and on the device:
#   chunk size        -> opcode 0xFA + two bytes
#   ready to receive  -> opcode 0xF9 + a credit count (compared to 0x41)
# The device sends nothing until it has received "ready to receive" credits.
CHUNK_SIZE_FRAME = bytes.fromhex("fa80ff")   # FA 80 FF
READY_INIT_FRAME = bytes.fromhex("f980")     # F9 80  (initial credits)
READY_FRAME = bytes.fromhex("f941")          # F9 41  (periodic top-up, 65)

# Warm-up written to the ...0003 control characteristic before the subscribes.
WARMUP_CONTROL = (bytes.fromhex("01"), bytes.fromhex("0300"))

# Ping / keep-alive on the ...0003 control characteristic.
PING_FRAME = bytes.fromhex("0600821893421027")


def subscribe_frame(vreg: int, instance: int = 0) -> bytes:
    """Ask the device to stream one register: opcode, instance, CBOR([vreg])."""
    return bytes([OP_SUBSCRIBE, instance]) + cbor.encode([vreg])


def write_frame(vreg: int, value: bytes, instance: int = 0) -> bytes:
    """Set a register: opcode, instance, CBOR([vreg, value_bytes]).

    Same shape as the ping frame (confirmed byte-for-byte: PING is
    ``0x06,0x00 + CBOR([0x93, b"\\x10\\x27"])``), so writing is ``0x04,0x00 +
    CBOR([vreg, value])``. ``value`` is the raw little-endian register bytes,
    e.g. ``bytes([4])`` for VE_REG_MODE = Off.
    """
    return bytes([OP_WRITE, instance]) + cbor.encode([vreg, value])


# Value-stream framing, confirmed against birdie1/victron's Orion Smart capture
# and our own hardware log. Each value frame is self-describing:
#
#   08  ff  19 HH LL   TN   <data little-endian>
#   │   │   │  └──┴── VE_REG id, big-endian CBOR uint16 (19 ED BB -> 0xEDBB)
#   │   │   └──────── 0x19 = CBOR uint16 marker
#   │   └──────────── flag byte (0x03 on BMV/SmartSolar, 0x00 on Orion) -- ignored
#   └──────────────── 0x08 var-length value (0x09 = fixed-length, left raw for now)
#                           └ length/type byte: low nibble = data length in bytes
#
# consumed = 6 + length. Because the vreg travels in the frame itself, the same
# parser works for every product: the id is looked up in the register map for
# signedness and scale. Flow-control (0xF9) and ping echoes carry a different
# byte 2 and are skipped by the 0x19 check.
_FRAME_VAR = 0x08
_CBOR_U16 = 0x19
_FRAME_MIN = 6


def scan_for_registers(data: bytes, registers: dict[int, VregDef]) -> dict[int, float | int | None]:
    """Walk the stream, decode every value frame whose vreg we know."""
    found: dict[int, float | int | None] = {}
    i = 0
    n = len(data)
    while i + _FRAME_MIN <= n:
        if data[i] != _FRAME_VAR or data[i + 2] != _CBOR_U16:
            i += 1
            continue
        vreg = (data[i + 3] << 8) | data[i + 4]
        length = data[i + 5] & 0x0F
        start = i + _FRAME_MIN
        if length == 0 or start + length > n:
            i += 1
            continue
        reg = registers.get(vreg)
        if reg is not None:
            value = reg.decode(data[start:start + length])
            if value is not None:
                found[vreg] = value
        i = start + length
    return found
