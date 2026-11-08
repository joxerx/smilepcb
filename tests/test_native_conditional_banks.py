"""Exercise actual native replacement and transaction boundaries."""
import json
import os
from pathlib import Path
import subprocess
import pytest

@pytest.fixture(scope='module')
def receipts(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];result={}
    for name,timeout in (('receipt',50),('transaction',100)):
        folder=tmp_path_factory.mktemp('conditional-'+name)
        process=subprocess.run([interpreter,'-B',str(root/'tests'/('native_conditional_'+name+'_probe.py')),str(folder)],
            cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=timeout)
        assert process.returncode==0,process.stdout+process.stderr
        result[name]=json.loads((folder/'report.json').read_text())
    return result

def test_actual_replacement_reaches_outer_interface_and_field_receipts(receipts):
    result=receipts['receipt']
    assert result['accepted'] and result['replacement']['old']!=result['replacement']['new']
    assert any(result['replacement']['new'] in q['added_ids'] for q in result['result']['assignment']['ports'])

def test_stale_ownership_fails_without_changing_native_frame(receipts):
    assert receipts['transaction']['stale']['rejected'] and receipts['transaction']['stale']['unchanged']

def test_post_commit_failure_restores_heads_bindings_and_launch_scope(receipts):
    row=receipts['transaction']['post_commit']
    assert all(row[k] for k in ('rejected','unchanged','model_unchanged','launch_unchanged'))

def test_complete_matrix_retains_actual_source_ownership_after_polish(receipts):
    row=receipts['transaction']['success']
    assert row['complete'] and row['controller_connected'] and row['conditional_banks']['accepted']
    assert row['matrix_ports'] and all(q['added_ids'] for q in row['matrix_ports'])


def test_unrepresentable_generated_property_rejected_before_mutation(receipts):
    row=receipts['transaction']['property']
    assert row['rejected'] and row['unchanged'] and row['raw_override_unchanged']
    assert receipts['transaction']['post_commit']['raw_pad_override_preserved']
    assert all(receipts['transaction']['success_receipts'].values())
