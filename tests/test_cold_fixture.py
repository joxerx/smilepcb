"""A cold build must select actual bounded KLE/config inputs before native work."""
import json
from pathlib import Path

import pytest

INPUTS = Path(__file__).resolve().parents[1] / 'recipes/stm32/inputs'


def test_default_and_compact_fixture_preserve_distinct_layouts_and_keycodes():
    from recipes.stm32.fixture_inputs import load_fixture
    name, directory, layout = load_fixture(INPUTS)
    assert name == 'ansi61' and directory == INPUTS / 'ansi61'
    assert len(layout['keys']) == 61
    name, directory, compact = load_fixture(INPUTS, 'compact68')
    assert name == 'compact68' and directory == INPUTS / 'compact68'
    assert len(compact['keys']) == 68
    assert (compact['rows'], compact['cols']) == (5, 15)
    assert compact['kle_sha256'] != layout['kle_sha256']
    expected = json.loads((directory / 'keyboard.json').read_text())['keycodes']
    assert [key['keycode'] for key in compact['keys']] == expected


@pytest.mark.parametrize('name', ['', '../ansi61', '/ansi61', 'a/b', 'a.b', 'missing'])
def test_invalid_fixture_rejected_without_creating_inputs(name):
    from recipes.stm32.fixture_inputs import load_fixture
    with pytest.raises(ValueError):
        load_fixture(INPUTS, name)


def test_symlinked_fixture_and_files_rejected(tmp_path):
    from recipes.stm32.fixture_inputs import load_fixture
    (tmp_path / 'linked').symlink_to(INPUTS / 'ansi61', target_is_directory=True)
    with pytest.raises(ValueError):
        load_fixture(tmp_path, 'linked')
    fixture = tmp_path / 'local'
    fixture.mkdir()
    (fixture / 'layout.kle.json').symlink_to(INPUTS / 'ansi61/layout.kle.json')
    (fixture / 'keyboard.json').write_bytes((INPUTS / 'ansi61/keyboard.json').read_bytes())
    with pytest.raises(ValueError):
        load_fixture(tmp_path, 'local')


def test_malformed_or_oversized_fixture_rejected_before_model_creation(tmp_path):
    from recipes.stm32.fixture_inputs import load_fixture
    fixture = tmp_path / 'local'
    fixture.mkdir()
    (fixture / 'keyboard.json').write_bytes((INPUTS / 'ansi61/keyboard.json').read_bytes())
    kle = fixture / 'layout.kle.json'
    kle.write_text('not json')
    with pytest.raises(ValueError):
        load_fixture(tmp_path, 'local')
    kle.write_text(' ' * 65537)
    with pytest.raises(ValueError):
        load_fixture(tmp_path, 'local')
