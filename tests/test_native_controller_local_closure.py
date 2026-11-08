"""Native tests are dormant until root grants the sole CAD token."""
import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    native=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not native:pytest.skip('Explicit pinned KiCad interpreter/token required')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('controller-local-native')
    script=Path(__file__).with_name('native_controller_local_closure_probe.py')
    for mode,limit in (('prepare',12),('exercise',70)):
        result=subprocess.run([native,'-B',str(script),mode,str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=limit)
        (out/(mode+'.log')).write_text(result.stdout+result.stderr)
        assert result.returncode==0,result.stdout+result.stderr
    return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('face',['front','back'])
def test_early_actual_controller_joins_replace_only_closed_local_heads(report,face):
    row=report[face]
    assert all(row[k] for k in ('accepted','local_closed_without_head','external_partial_keeps_head','all_physical_aliases_joined','joint_matrix_native_proved','early_functional_copper_permanent','source_exact','saved_contacts_exact','actual_finalize_rebind_proved','normal_controller_ports_keeps_local_proof','expired_source_rollback'))
    assert all(row['negative_controls'].values())
