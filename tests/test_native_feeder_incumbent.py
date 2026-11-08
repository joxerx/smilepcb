import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('feeder-incumbent')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_feeder_incumbent_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'report.json').read_text())
@pytest.mark.parametrize('case',['worse','timeout'])
def test_failed_local_negotiation_restores_best_native_matrix_exactly(report,case):
 r=report[case];assert r['error'] is None and r['exact'] and r['source_exact'] and r['pending']=={'c':1},r
 assert r['history'] and r['history'][-1]['accepted'] is False,r

def test_local_negotiation_keeps_a_real_connectivity_improvement(report):
 r=report['improved'];assert r['error'] is None and r['pending']=={} and r['source_exact'] and not r['exact'],r
 assert r['history'][-1]['accepted'] is True,r
