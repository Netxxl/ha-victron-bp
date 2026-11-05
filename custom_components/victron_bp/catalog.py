"""Data-driven device catalog.

Two small, factual tables under ``catalog/`` let the integration decide, from
the product id in the BLE advertisement alone, which device it is and how to
decode its registers -- instead of hard-coding one device:

  products.json : {"0xa3c0": ["Orion Smart", "12V|12V-18A Isolated"], ...}
  vregs.json    : [[id, name, type, scale, writable], ...]

These hold protocol facts only -- register numbers, types, scales and the
VE_REG_* identifiers, plus product-id-to-model -- gathered by reverse
engineering for interoperability. The registers the integration actually reads
and their units are declared in code (COMMON_SENSORS / _UNITS), so no vendor
label or description text is bundled.

No ``from __future__ import annotations`` quirk is needed here (this module has
no dbus-fast annotations), but it is kept import-light so it loads fast.
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_DIR = Path(__file__).parent / "catalog"

# byte width and signedness per VE.Direct scalar type
_SCALAR = {
    "un8": (1, False), "sn8": (1, True),
    "un16": (2, False), "sn16": (2, True),
    "un32": (4, False), "sn32": (4, True),
    "un64": (8, False),
}


@dataclass(frozen=True)
class VregDef:
    """One VE.Direct register definition."""

    id: int
    name: str
    type: str
    scale: float          # raw / scale = engineering value; 1 = as-is
    writable: bool

    def decode(self, data: bytes) -> float | int | str | None:
        """Turn raw little-endian bytes into a value, or None if N/A."""
        info = _SCALAR.get(self.type)
        if info is not None:
            size, signed = info
            if len(data) < size:
                return None
            raw = int.from_bytes(data[:size], "little", signed=signed)
            # Victron marks "not available" with the extreme of the range.
            if not signed and raw == (1 << (size * 8)) - 1:
                return None
            if signed and raw == (1 << (size * 8 - 1)) - 1:
                return None
            return raw / self.scale if self.scale not in (1, 0) else raw
        if self.type == "float":
            if len(data) < 4:
                return None
            return struct.unpack("<f", data[:4])[0]
        if self.type.startswith(("string", "bytearray")):
            # strings come back zero-terminated; bytes we hand back as hex
            if self.type.startswith("string"):
                return data.split(b"\x00", 1)[0].decode("ascii", "replace")
            return data.hex()
        return None


@dataclass(frozen=True)
class Metric:
    """A register to read for a product: how to decode and show it."""

    vreg: VregDef
    name: str      # fallback label (shown only when no translation key applies)
    unit: str


# ---------------------------------------------------------------------------
# loading / indexing
# ---------------------------------------------------------------------------

def _to_int(x: str) -> int | None:
    try:
        return int(x, 16)
    except (ValueError, TypeError):
        return None


@lru_cache(maxsize=1)
def _products() -> dict[int, tuple[str, str]]:
    raw = json.loads((_DIR / "products.json").read_text(encoding="utf-8"))
    out: dict[int, tuple[str, str]] = {}
    for hexid, (family, model) in raw.items():
        pid = _to_int(hexid)
        if pid is not None:
            out[pid] = (family, model)
    return out


@lru_cache(maxsize=1)
def _vregs() -> tuple[dict[str, VregDef], dict[int, VregDef]]:
    raw = json.loads((_DIR / "vregs.json").read_text(encoding="utf-8"))
    by_name: dict[str, VregDef] = {}
    by_id: dict[int, VregDef] = {}
    for vid, name, vtype, scale, writable in raw:
        rid = _to_int(vid)
        if rid is None or not name:
            continue
        try:
            sc = float(scale) if scale not in ("", None) else 1.0
        except (ValueError, TypeError):
            sc = 1.0
        d = VregDef(rid, name, vtype, sc or 1.0, bool(writable))
        by_name[name] = d
        by_id.setdefault(rid, d)
    return by_name, by_id


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def load() -> None:
    """Read the JSON files into the cache; blocking, so run it in an executor."""
    _products()
    _vregs()


def product_info(product_id: int) -> tuple[str, str] | None:
    """(family, model) for a product id, or None if unknown."""
    return _products().get(product_id)


def is_known_product(product_id: int) -> bool:
    return product_id in _products()


def vreg_by_name(name: str) -> VregDef | None:
    return _vregs()[0].get(name)


def vreg_by_id(vreg_id: int) -> VregDef | None:
    return _vregs()[1].get(vreg_id)


# A curated, cross-family baseline of the registers that matter for BLE "smart"
# devices. The product still only answers the ones it actually has, so unknown
# ones simply stay unavailable. Extend per family later (or derive from the
# device at runtime). Names verified to exist in vregs.json.
COMMON_SENSORS: tuple[str, ...] = (
    "VE_REG_DEVICE_STATE",
    "VE_REG_DEVICE_OFF_REASON_2",
    "VE_REG_DC_CHANNEL1_VOLTAGE",   # Orion/charger battery-side voltage
    "VE_REG_DC_CHANNEL1_CURRENT",   # Orion/charger battery-side current
    "VE_REG_DC_CHANNEL1_POWER",
    "VE_REG_CHR_TEMPERATURE",       # charger temperature (Orion)
    "VE_REG_BAT_TEMPERATURE",
    "VE_REG_SOC",
    "VE_REG_DC_INPUT_VOLTAGE",
    "VE_REG_DC_OUTPUT_VOLTAGE",     # BatteryProtect output side
    "VE_REG_DC_OUTPUT_CURRENT",
)

# Writable controls surfaced as switches/selects. MODE is on/off (3=On, 4=Off).
COMMON_CONTROLS: tuple[str, ...] = (
    "VE_REG_DEVICE_MODE",
)

# Units for the registers above, declared here (SI units, nothing vendor-specific).
# Registers not listed have no unit (enums/counters like state and mode).
_UNITS: dict[str, str] = {
    "VE_REG_DC_CHANNEL1_VOLTAGE": "V",
    "VE_REG_DC_CHANNEL1_CURRENT": "A",
    "VE_REG_DC_CHANNEL1_POWER": "W",
    "VE_REG_CHR_TEMPERATURE": "°C",
    "VE_REG_BAT_TEMPERATURE": "°C",
    "VE_REG_SOC": "%",
    "VE_REG_DC_INPUT_VOLTAGE": "V",
    "VE_REG_DC_OUTPUT_VOLTAGE": "V",
    "VE_REG_DC_OUTPUT_CURRENT": "A",
}


@dataclass(frozen=True)
class Profile:
    product_id: int
    family: str
    model: str
    sensors: tuple[Metric, ...] = field(default_factory=tuple)
    controls: tuple[Metric, ...] = field(default_factory=tuple)

    @property
    def title(self) -> str:
        bits = [b for b in (self.family, self.model) if b]
        return " ".join(bits) or f"Victron 0x{self.product_id:04X}"

    @property
    def read_metrics(self) -> tuple[Metric, ...]:
        """Every register to subscribe to and read, sensors and controls."""
        seen: set[int] = set()
        out: list[Metric] = []
        for m in (*self.sensors, *self.controls):
            if m.vreg.id not in seen:
                seen.add(m.vreg.id)
                out.append(m)
        return tuple(out)


def _metric(vreg_name: str) -> Metric | None:
    vdef = vreg_by_name(vreg_name)
    if vdef is None:
        return None
    label = vreg_name.removeprefix("VE_REG_").replace("_", " ").title()
    return Metric(vreg=vdef, name=label, unit=_UNITS.get(vreg_name, ""))


def profile(product_id: int) -> Profile:
    """Build the entity set for a product id from the catalog."""
    family, model = product_info(product_id) or ("", "")
    sensors = tuple(m for n in COMMON_SENSORS if (m := _metric(n)) is not None)
    controls = tuple(
        m for n in COMMON_CONTROLS
        if (m := _metric(n)) is not None and m.vreg.writable
    )
    return Profile(product_id, family, model, sensors, controls)
