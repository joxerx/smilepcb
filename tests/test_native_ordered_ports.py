import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('ordered-ports')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_ordered_ports_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=35)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'report.json').read_text())
@pytest.mark.parametrize('face',['front','back'])
def test_open_bank_uses_ordered_same_face_access_without_mandatory_vias(report,face):
 r=report[face];assert r['error'] is None and r['accepted'] is True and r['new_vias']==0 and r['source_preserved'],r
 assert len(r['ports'])==3 and all(p['kind']=='same_layer' and p['terminal_id'] and p['via_id'] is None for p in r['ports']),r


def test_obstructed_bank_uses_a_native_legal_layer_fallback_or_rejects_atomically(report):
 r=report['blocked'];assert r['error'] is None and r['source_preserved'],r
 assert (r['accepted'] is True and r['new_vias']>0 and len(r['ports'])==3 and r['contacts_after_reopen']) or (r['accepted'] is False and r['unchanged'] and not r['ports']),r

def test_expired_bank_preparation_cannot_emit_partial_ports(report):
 r=report['expired'];rejected=r['accepted'] is False or (r['error'] or '').startswith('ValueError: Controller interfaces require a positive deadline')
 assert rejected and r['unchanged'] and not r['ports'],r


@pytest.mark.parametrize('case',['bundle','bundle-blocked'])
def test_whole_bank_extends_with_coordinated_native_clear_turns(report,case):
 r=report[case];b=r['bundle'];assert b.get('error') is None and b['accepted'] is True and len(b['groups'])==1 and len(b['groups'][0]['paths'])==3 and r['contacts_after_reopen'],r
 assert len({len(p) for p in b['groups'][0]['paths']})==1,b

def test_expired_group_extension_keeps_all_original_ports_and_copper(report):
 b=report['bundle-expired']['bundle'];assert b.get('error') is None and b['accepted'] is False and b['unchanged_on_failure'],b


def test_preferred_and_mixed_search_share_the_declared_state_budget(report):
 r=report['state-budget'];assert r['error'] is None and r['accepted'] is False and r['unchanged'] and r['rejected_search_states']<=1,r

def test_native_same_face_pad_to_lane_pitch_conversion_keeps_every_actual_contact(report):
 r=report['pitch'];assert r['accepted'] and r['new_vias']==0 and r['contacts_after_reopen'] and r['source_preserved'],r
 ys=sorted(q['exit_nm'][1] for q in r['ports']);assert [b-a for a,b in zip(ys,ys[1:])]==[350000,350000]
