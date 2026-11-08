import json,os,subprocess,sys
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def native_report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('transient-generated-access')
    for mode,limit in (('prepare',12),('exercise',150)):
        r=subprocess.run([interpreter,'-B',str(Path(__file__).with_name('native_transient_generated_access_probe.py')),mode,str(out),sys.executable],cwd=root,env={**os.environ,'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=limit)
        (out/(mode+'.log')).write_text(r.stdout+r.stderr)
        assert r.returncode==0,r.stdout+r.stderr
    return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('face',['front','back'])
def test_fresh_matrix_capture_keeps_only_verified_access_provisional(native_report,face):
    row=native_report[face]
    assert row['new_access_is_transient'] and row['key_owned'] and row['fixed_owned'],row
    assert row['genuine_external_fixed_interface'] and row['fixed_control_initially_open'],row
    assert row['originals_permanent'] and row['source_exact'],row
    assert row['open_access_retained'] and row['open_contacts_exact'],row
    assert row['closed_redundant_key_vias_retired'] and row['closed_unused_head_retired'],row
    assert row['closed_contacts_exact'] and row['permanent_exact'] and row['matrix_exact'] and row['saved_exact'],row
