import json,os,subprocess
from pathlib import Path
import pytest


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('portal-detail')
    run=subprocess.run([interpreter,'-B',str(Path(__file__).with_name('native_collector_portal_detail_probe.py')),str(out)],
                       cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=40)
    assert run.returncode==0,run.stdout+run.stderr
    rows=json.loads((out/'report.json').read_text())
    cases={'unused_entry','unused_exit','track','via_opposite','same_net_land','current_launch','future_launch','own_contact','rollback'}
    assert len(rows)==18 and {row['case'] for row in rows}==cases
    faces={row['face'] for row in rows};assert len(faces)==2
    assert all(sum(row['case']==case and row['face']==face for row in rows)==1 for case in cases for face in faces)
    return rows


@pytest.mark.parametrize('case',['unused_entry','unused_exit'])
def test_unused_both_face_envelope_space_does_not_reject_native_copper(report,case):
    for row in (r for r in report if r['case']==case):
        assert not all(row['coarse']) and all(all(q.values()) for q in row['actual_checks']),row
        assert row['only_unused_obstacle_blocks_envelope'],row
        assert row['proposed'] and row['committed'] and row['connected'] and row['saved_connected'],row
        assert row['source_exact'] and row['ports_exact'] and row['saved_exact'] and row['departures_clear'],row
        assert row['vias']==2 and row['endpoints']=={'lower':[17000000,10000000],'upper':[17000000,10600000]},row


@pytest.mark.parametrize('case',['track','via_opposite','same_net_land','current_launch','future_launch','own_contact'])
def test_true_copper_land_and_launch_collisions_remain_rejected(report,case):
    for row in (r for r in report if r['case']==case):
        assert not row['proposed'] and not all(all(q.values()) for q in row['actual_checks']),row
        assert row['unchanged_before_commit'],row
        if case in ('current_launch','future_launch'):assert row['raw_entry_clear'],row


def test_second_lane_native_rejection_rolls_back_every_new_track_and_via(report):
    for row in (r for r in report if r['case']=='rollback'):
        assert row['proposed'] and not row['committed'] and row['emitted_via_nets']==['lower'] and row['error']=='Expanded lane violates native clearance',row
        assert row['rollback_exact'] and row['ports_exact'] and row['source_exact'],row
