"""Validate a cold-build fixture before creating output or starting native tools."""
import json
from pathlib import Path
import re

from smilepcb.keyboard_layout import parse_layout


def load_fixture(root, name='ansi61'):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('Invalid fixture name')
    directory = Path(root) / name
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Select an existing regular fixture directory')
    paths = [directory / filename for filename in ('layout.kle.json', 'keyboard.json')]
    if any(path.is_symlink() or not path.is_file() or path.stat().st_size > 65536 for path in paths):
        raise ValueError('Fixture inputs must be bounded regular JSON files')
    layout = parse_layout(paths[0].read_text(), json.loads(paths[1].read_bytes()))
    return name, directory, layout
