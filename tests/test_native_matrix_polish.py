import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('matrix-polish')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_matrix_polish_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=45)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'report.json').read_text())
def test_native_fixed_terminal_shortcut_simplifies_without_touching_source(report):
 r=report['jog'];assert r['report']['accepted'] and r['report']['adopted_chains']>0 and r['track_count_after']<r['track_count_before'] and r['fixed_exact'] and r['footprints_exact'],r

def test_interior_pad_contact_survives_or_rejects_the_entire_rewrite(report):
 r=report['interior'];assert r['report']['all_pad_partitions_exact'] and r['fixed_exact'] and r['footprints_exact'],r

def test_expired_polish_keeps_the_original_feasible_incumbent(report):
 assert report['expired']['unchanged'] and report['expired']['report']['adopted_chains']==0

def test_redundant_pad_contact_tail_is_retired_only_with_native_partition_proof(report):
 r=report['contact'];assert r['report']['contacts']['removed_ids'] and r['report']['contacts']['all_pad_partitions_exact'] and r['fixed_exact'],r
