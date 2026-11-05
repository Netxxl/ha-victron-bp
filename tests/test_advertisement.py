"""Decode a self-made encrypted BatteryProtect advertisement round-trip."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _boot  # noqa: F401,E402
from victron_bp import advertisement as adv  # noqa: E402


def _encrypt(key: bytes, nonce: int, plain: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = nonce.to_bytes(16, "little")
    enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    return enc.update(plain) + enc.finalize()


def build(key, nonce, fields):
    r = bytearray(16)

    def put(bit, nbits, value):
        for i in range(nbits):
            if (value >> i) & 1:
                r[(bit + i) // 8] |= 1 << ((bit + i) % 8)

    bit = 0
    for nbits, value in fields:
        put(bit, nbits, value)
        bit += nbits
    cipher = _encrypt(key, nonce, bytes(r))
    header = bytes([0x10]) + (0xA3B0).to_bytes(2, "little") + bytes([0x09]) + nonce.to_bytes(2, "little") + bytes([key[0]])
    return header + cipher


key = bytes.fromhex("00112233445566778899aabbccddeeff")
nonce = 0x1234
# device_state=5(float), output_state=1(on), error=0, alarm=0, warning=0,
# input=1310 (13.10V), output=1305 (13.05V), off_reason=0
raw = build(key, nonce, [(8, 5), (8, 1), (8, 0), (16, 0), (16, 0),
                         (16, 1310), (16, 1305), (32, 0)])
st = adv.parse_manufacturer_data(raw, key)
assert st.device_state == 5, st
assert st.output_state == 1, st
assert abs(st.input_voltage - 13.10) < 1e-6, st
assert abs(st.output_voltage - 13.05) < 1e-6, st
assert st.off_reason == 0, st
print("ok advertisement roundtrip:", st)

# wrong key is rejected via the key-check byte
try:
    adv.parse_manufacturer_data(raw, bytes([0xAA]) + key[1:])
    raise AssertionError("should have rejected wrong key")
except ValueError:
    print("ok wrong key rejected")

print("all advertisement tests passed")
