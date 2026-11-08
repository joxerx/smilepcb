import json,os,subprocess
from pathlib import Path
import pytest
import sexpdata

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('candidate-source-deadline')
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_local_router_candidate_deadline_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':os.pathsep.join((str(root),str(Path(sexpdata.__file__).parent)))},capture_output=True,text=True,timeout=15)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('face',['front','back'])
def test_native_candidate_expiry_preserves_sources_and_never_becomes_admission(report,face):
    fields=('error_contract','candidate_contract','source_exact','no_emission','deadline_exact','preexpired_skips_graph')
    bad={key:[field for field in fields if not value[field]] for key,value in report[face].items() if not all(value[field] for field in fields)}
    assert bad=={},bad


@pytest.mark.parametrize('face',['front','back'])
def test_spoke_budget_failure_is_nonmutating_and_healthy_relocation_still_works(report,face):
    fields=('expected_result','source_exact','native_partitions_exact','deadline_not_renewed')
    assert all(report['spokes'][face]['expired'][key] for key in fields),report['spokes'][face]['expired']
    assert all(report['spokes'][face]['healthy'][key] for key in fields),report['spokes'][face]['healthy']
