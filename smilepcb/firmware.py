"""Manifest-checked synthetic firmware inputs shared by preparation and workflows."""
import hashlib
import json
from pathlib import Path

def read_firmware(fixture):
    root = Path(fixture).resolve()
    files = ("keyboard.json", "keymaps/default/keymap.c")
    for relative in (".smilepcb-lab", "firmware-manifest.json", "qmk", "qmk/keymaps", "qmk/keymaps/default",
                     *("qmk/" + name for name in (*files, "config.h"))):
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Refusing a symlink or outside path in the lab firmware")
    if (root / ".smilepcb-lab").read_text().strip() != "synthetic-smilepcb-lab-v1":
        raise ValueError("Only an explicitly generated lab fixture is accepted")
    if (root / "qmk/config.h").is_file():
        files += ("config.h",)
    manifest = root / 'firmware-manifest.json'
    expected = None
    if manifest.is_file():
        data = json.loads(manifest.read_text())
        expected = data.get('files_sha256')
        allowed = {'keyboard.json', 'keymaps/default/keymap.c', 'config.h'}
        if (data.get('schema') != 1 or not isinstance(expected, dict) or set(expected) != allowed):
            raise ValueError('Unsupported firmware manifest')
        if set(files) != set(expected):
            raise ValueError('Required firmware file missing or outside manifest')
    result = {name: (root / "qmk" / name).read_bytes() for name in files}
    if expected is not None and any(hashlib.sha256(data).hexdigest() != expected[name] for name, data in result.items()):
        raise ValueError('Generated firmware hashes differ from the manifest')
    return result
