"""Make the pure-protocol modules importable as a package without running the
integration's __init__ (which needs Home Assistant). Import this first."""

import sys
import types
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "victron_bp"
if "victron_bp" not in sys.modules:
    module = types.ModuleType("victron_bp")
    module.__path__ = [str(PKG)]
    sys.modules["victron_bp"] = module
