import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('exit-assignment')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_exit_assignment_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=100)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'report.json').read_text())
def test_physical_after_turn_order_updates_one_shared_model_and_every_mcu_pad_alias(report):
 r=report['valid'];assert r['error'] is None and r['rows']==r['qmk_rows']==['A2','A1','A0'] and r['cols']==['B0'] and r['all_pin1_aliases_remapped'],r
@pytest.mark.parametrize('case',['unowned','locked'])
def test_copper_outside_the_editable_port_contract_cannot_be_relabelled(report,case):
 r=report[case];assert r['error'] and r['error'].startswith('ValueError:') and r['unchanged'],r


def test_explicit_matrix_scope_updates_cross_role_gpio_cad_qmk_and_pad_aliases(report):
 r=report['matrix_valid']
 assert r['error'] is None, r
 assert r['rows']==['B0','A1','A2'] and r['cols']==['A0'],r
 assert r['qmk_pins']=={'rows':r['rows'],'cols':r['cols']} and r['input_model_unchanged'],r
 assert r['fixed_functions_unchanged'] and r['aliases_remapped'],r
 assert r['receipt']['native_pad_partitions_exact'] and r['receipt']['placements_exact'],r


@pytest.mark.parametrize('case',['matrix_default','matrix_incomplete','matrix_unowned','matrix_locked','matrix_field_connected','matrix_omitted_alias','matrix_disconnected_claim'])
def test_matrix_scope_cannot_relabel_missing_locked_unowned_or_field_connected_sources(report,case):
 r=report[case]
 assert r['error'] and r['error'].startswith('ValueError:') and r['unchanged'] and r['input_model_unchanged'],r
