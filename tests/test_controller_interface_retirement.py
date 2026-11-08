import json
import pytest
from smilepcb.keyboard_recovery import controller_interface_ids

A='10000000-0000-0000-0000-000000000001'
B='10000000-0000-0000-0000-000000000002'

def fixture(tmp_path,retired,scope='matrix-with-controller'):
 project=tmp_path/'project';project.mkdir()
 (tmp_path/'controller-ports.json').write_text(json.dumps({'accepted':True,'ports':{'accepted':True,'added_ids':[A,B]},'returns':{'accepted':True,'added_ids':[]}}))
 (project/'.smilepcb-field.json').write_text(json.dumps({'scope':scope,'retired_source_ids':retired}))
 return project

def test_completed_matrix_explicitly_retires_removed_initial_escapes(tmp_path):
 assert controller_interface_ids(fixture(tmp_path,[A]))==[B]

@pytest.mark.parametrize('retired,scope',[('bad','matrix-with-controller'),([A],'key-field')])
def test_malformed_or_unqualified_retirement_does_not_hide_interfaces(tmp_path,retired,scope):
 with pytest.raises(ValueError):controller_interface_ids(fixture(tmp_path,retired,scope))
