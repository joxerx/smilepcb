"""Paths for the tested KiCad 10 runtime; all native operations use one install."""
if not __debug__:
    raise RuntimeError('SmilePCB validation requires Python without -O/PYTHONOPTIMIZE')

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB_APP = ROOT / ".lab/kicad-volume/KiCad/KiCad.app"
APP = Path(os.environ.get("SMILEPCB_KICAD_APP", str(LAB_APP if LAB_APP.is_dir() else Path("/Applications/KiCad/KiCad.app"))))
CONTENTS = APP / "Contents"
SUPPORT = Path(os.environ.get("SMILEPCB_KICAD_SUPPORT", str(CONTENTS / "SharedSupport")))
KICAD_PYTHON = os.environ.get("SMILEPCB_KICAD_PYTHON", str(CONTENTS / "Frameworks/Python.framework/Versions/Current/bin/python3"))
KICAD_CLI = os.environ.get("SMILEPCB_KICAD_CLI", str(CONTENTS / "MacOS/kicad-cli"))
BOARD_VERSION = 20260206


def environment():
    env = os.environ.copy()
    env.setdefault("KICAD_CONFIG_HOME", str(ROOT / ".lab/kicad-config"))
    env.setdefault("XDG_CACHE_HOME", str(ROOT / ".lab/cache"))
    env.setdefault("KICAD10_SYMBOL_DIR", str(SUPPORT / "symbols"))
    env.setdefault("KICAD10_FOOTPRINT_DIR", str(SUPPORT / "footprints"))
    env["SMILEPCB_KICAD_SUPPORT"] = str(SUPPORT)
    return env
