"""Round-trip and known-vector checks for the CBOR subset."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _boot  # noqa: F401,E402
from victron_bp import cbor  # noqa: E402
from victron_bp.vreg import VE_REG_MODE, Mode  # noqa: E402


def check(got, want, label):
    assert got == want, f"{label}: {got!r} != {want!r}"
    print("ok", label)


# RFC 8949 appendix A vectors
check(cbor.encode(0).hex(), "00", "enc 0")
check(cbor.encode(23).hex(), "17", "enc 23")
check(cbor.encode(24).hex(), "1818", "enc 24")
check(cbor.encode(0x0200).hex(), "190200", "enc 512")
check(cbor.encode(-1).hex(), "20", "enc -1")
check(cbor.encode(b"\x01\x02\x03\x04").hex(), "4401020304", "enc bytes")
check(cbor.encode([1, 2, 3]).hex(), "83010203", "enc array")
check(cbor.encode(True).hex(), "f5", "enc true")

for value in (0, 1, 23, 24, 255, 256, 0x0200, 0xEDAB, 70000, -1, -1000):
    check(cbor.decode(cbor.encode(value)), value, f"roundtrip int {value}")
check(cbor.decode(cbor.encode(b"\xab\xcd")), b"\xab\xcd", "roundtrip bytes")
check(cbor.decode(cbor.encode([VE_REG_MODE, 3])), [VE_REG_MODE, 3], "roundtrip array")
check(cbor.decode(cbor.encode({1: b"\x04", 2: [3, 4]})), {1: b"\x04", 2: [3, 4]}, "roundtrip map")

# a plausible "write MODE = OFF" payload shape: [vreg, value]
frame = cbor.encode([VE_REG_MODE, int(Mode.OFF)])
check(cbor.decode(frame), [0x0200, 4], "mode off frame")

print("all cbor tests passed")
