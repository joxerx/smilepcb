"""Run the real composition publication suffix with only native save isolated."""
import ast
from pathlib import Path
from types import SimpleNamespace
import time
import pytest
from smilepcb.keyboard_routing import generated_provenance as gp
from test_generated_provenance import setup_board,write_board,U,G


@pytest.mark.parametrize('with_field',[False,True])
@pytest.mark.parametrize('receipts_present',[False,True])
@pytest.mark.parametrize('failure',['geometry','ledger_publish'])
def test_composition_rebind_failure_restores_complete_saved_revision(tmp_path,monkeypatch,receipts_present,failure,with_field):
    board=setup_board(tmp_path)
    fields=[tmp_path/'.smilepcb-field.json',tmp_path/'.smilepcb-interfaces.json',tmp_path/gp.MARKER]
    for path in fields:
        if not receipts_present:path.unlink(missing_ok=True)
        elif path.name!=gp.MARKER:path.write_text('original '+path.name)
    paths=[board,*fields]
    original={path:path.read_bytes() if path.exists() else None for path in paths}
    before=gp.inventory(board)
    def save(_path,_board):
        write_board(board,[U,G],net='NEW',moved=[G] if failure=='geometry' else [])
        fields[0].write_text('new field');fields[1].write_text('new interfaces')
        return True
    if failure=='ledger_publish':
        real_write=gp._write
        def late_failure(*args):
            real_write(*args)
            raise OSError('ledger publication failed')
        monkeypatch.setattr(gp,'_write',late_failure)
    source=Path(__file__).resolve().parents[1]/'smilepcb/stm32/finalize_native.py'
    parsed=ast.parse(source.read_text(),filename=str(source))
    # Execute everything after the native validation report, including the
    # actual publication branch and its provenance handoff/rollback boundary.
    report=next(index for index,node in enumerate(parsed.body)
        if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call)
        and isinstance(node.value.func,ast.Name) and node.value.func.id=='print')
    publication=ast.Module(body=parsed.body[report+1:],type_ignores=[])
    from smilepcb.keyboard_routing import interface_contract
    monkeypatch.setattr(interface_contract,'load_interfaces',lambda project:None)
    env={'path':board,'cfg':{},'contract':{} if with_field else None,'p':SimpleNamespace(SaveBoard=save),'b':object(),
        'publish':lambda native,path,contract,**kwargs:save(path,native),'time':time,
        'provenance_before':before,'remap':{'SIG':'NEW'},
        'rebind_generated':gp.rebind_after_compose,'rollback_revision':getattr(gp,'rollback_revision',None)}
    expected=ValueError if failure=='geometry' else OSError
    with pytest.raises(expected):exec(compile(publication,str(source),'exec'),env)
    assert {path:path.read_bytes() if path.exists() else None for path in paths}==original
