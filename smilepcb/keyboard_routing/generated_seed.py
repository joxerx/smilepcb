"""Internal provenance for rerouting only freshly generated recipe copper.

This is an integrity receipt, not a signature or a manufacturing certificate.
All files are checked again before the generated-copper exchange starts.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

FILES = ("keyboard.kicad_pcb", "keyboard.kicad_pro", "layout.kle.json", ".smilepcb-keyboard.json")
PROOFS = ("controller-ports.json", "key-data-access.json", "controller-return-tree.json")
MARKER = "generated-seed.json"


def _read(path, limit=64 * 1024 * 1024):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise ValueError("Generated seed file must be a readable regular file: " + str(path)) from exc
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Invalid or oversized generated seed file: " + str(path))
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Oversized generated seed file: " + str(path))
    return value


def _inventory(project):
    project = Path(project)
    result = {}
    for name in FILES:
        result["project/" + name] = hashlib.sha256(_read(project / name)).hexdigest()
    for name in PROOFS:
        data = _read(project.parent / name, 4 * 1024 * 1024)
        if json.loads(data).get("accepted") is not True:
            raise ValueError("Seed recipe proof is not accepted: " + name)
        result[name] = hashlib.sha256(data).hexdigest()
    return result


def write_manifest(project, *, generation_profile):
    project = Path(project)
    if generation_profile != "dsn-safe-v1":
        raise ValueError("Generated routing exchange requires the explicit DSN-safe creation profile")
    manifest = {"schema": 1, "scope": "fresh-generated-recipe-copper",
                "generation_profile": generation_profile, "files": _inventory(project),
                "completed_keyboard": False, "hardware_tested": False}
    path = project.parent / MARKER
    if path.is_symlink():
        raise ValueError("Generated seed receipt must not be a symlink")
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def validate_manifest(project):
    project = Path(project)
    result = json.loads(_read(project.parent / MARKER, 65536))
    if (result.get("schema") != 1 or result.get("scope") != "fresh-generated-recipe-copper"
            or result.get("generation_profile") != "dsn-safe-v1"
            or result.get("completed_keyboard") is not False
            or result.get("files") != _inventory(project)):
        raise ValueError("Source is not the unchanged fresh generated recipe seed")
    return result
