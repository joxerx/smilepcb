"""Native geometry controls run only with the explicitly selected CAD runtime."""
import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('final-led-placement')
    from smilepcb.keyboard_libraries import create_libraries
    create_libraries(out)
    from smilepcb.keyboard_layout import parse_layout
    from smilepcb.keyboard_model import design
    source=root/'smilepcb/stm32/inputs/st40-grid48'
    cfg=design(parse_layout((source/'layout.kle.json').read_text(),json.loads((source/'keyboard.json').read_text())),controller_origin_mm=(0,0))
    (out/'st40-model.json').write_text(json.dumps(cfg))
    result=subprocess.run([interpreter,str(Path(__file__).with_name('native_final_led_placement_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('side',['F.Cu','B.Cu'])
def test_final_chain_controls_actual_native_data_pads_and_fresh_placement(side,report):
    row=report[side]
    assert row['order']==[3,2,1] and row['track_count']==0,row
    required=('data_inputs_face_upstream','centres_exact','other_parts_exact','manufacturing_edges_exact',
              'native_clear','foreign_overlap_rejected','foreign_edge_rejected','other_aperture_rejected',
              'own_aperture_pad_gap_rejected','foreign_hole_rejected','changed_native_body_rejected',
              'negative_controls_restored','native_cfg_angles_match','save_reload_exact',
              'local_power_pairs_connected','local_power_footprints_exact')
    assert all(row[key] for key in required),row


def test_actual_st40_no_rgb_native_outline_and_accepted_save_path(report):
    required=('placed','valid_board_region','four_outer_edges_only','no_aperture_owners',
              'no_led_or_driver','dormant_order_preserved','qmk_exact','native_geometry_exact','save_reload_exact')
    assert all(report['no_rgb'][key] for key in required),report['no_rgb']
