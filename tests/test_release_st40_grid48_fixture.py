"""Host-only reproducibility checks for the public ST40-derived layout fixture."""
import json
from pathlib import Path

from recipes.stm32.fixture_inputs import load_fixture
from smilepcb.keyboard_model import design

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / 'smilepcb/stm32/inputs'


def test_st40_grid48_is_complete_nonoverlapping_public_layout():
    name, directory, layout = load_fixture(INPUTS, 'st40-grid48')
    assert name == directory.name == 'st40-grid48'
    assert len(layout['keys']) == 48
    assert (layout['rows'], layout['cols']) == (4, 12)
    assert layout['rgb'] is False
    assert {tuple(key['position_u']) for key in layout['keys']} == {
        (float(column), float(row)) for row in range(4) for column in range(12)
    }
    assert all(key['size_u'] == [1.0, 1.0] for key in layout['keys'])
    assert 'layout-derived' in layout['name'].lower()


def test_st40_grid48_uses_the_ordinary_model_and_explicit_keycodes():
    _, directory, layout = load_fixture(INPUTS, 'st40-grid48')
    configuration = json.loads((directory / 'keyboard.json').read_text())
    assert len(configuration['keycodes']) == 48
    assert set(configuration) == {'schema', 'name', 'profile', 'keycodes', 'rgb'}
    model = design(layout, controller_origin_mm=(0, 0))
    assert model['layout'] == layout
    assert len([part for part in model['parts'] if part['ref'].startswith('SW')]) == 48
    assert len([part for part in model['parts'] if part['ref'].startswith('D')]) == 48


def test_st40_grid48_provenance_identifies_source_and_differences():
    source = (INPUTS / 'st40-grid48/SOURCES.md').read_text()
    for required in ('coarse/st40', '85a298d5c642bd3f0558771611119981649b09ad',
                     'cf1427e4dc9ba7c6fcd6038defa56626babeb27c3a4b239e22903cb20586ea04',
                     'CC BY-SA 4.0', '2u', 'hotswap', 'no RGB'):
        assert required in source
