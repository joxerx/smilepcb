import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('feeder-budget')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_feeder_budget_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=45)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'budget-report.json').read_text())

def test_short_remaining_repair_budget_returns_without_expired_negotiation(report):
 r=report['short_repair'];assert r['error'] is None and r['protected_preserved'],r

def test_expired_cleanup_uses_callers_deadline_before_any_mutation(report):
 r=report['expired_cleanup'];assert r['exception']=='TimeoutError' and r['unchanged'],r


def test_negotiation_only_starts_with_a_future_deadline_and_restoration_reserve(report):
 bounds=report['allocation_boundaries'];assert not bounds['1'],bounds
 assert all(0<stop<float(remaining) for remaining,stops in bounds.items() for stop in stops),bounds
