"""Host-only profile provenance at real exchange/receipt boundaries."""
import ast,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest


def test_roundtrip_worker_carries_actual_profile_and_keeps_failure_honest(tmp_path,monkeypatch):
    from smilepcb.stm32.verify_exchange import prove_roundtrip
    project=tmp_path/'project';project.mkdir();(project/'keyboard.kicad_pcb').write_text('preserved source')
    seen=[]
    class Boundary(Exception):pass
    def worker(script,request,log,**kwargs):seen.append(request);raise Boundary('native not launched')
    monkeypatch.setitem(sys.modules,'smilepcb.lab_tools',SimpleNamespace(worker=worker,rule_checks=None))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_build',SimpleNamespace(BRIDGE=Path('native.py')))
    with pytest.raises(Boundary):
        prove_roundtrip(project,tmp_path/'roundtrip',tmp_path/'no-native.ses',[],generation_profile='dsn-process-v2')
    assert seen[0]['generation_profile']=='dsn-process-v2'
    report=json.loads((tmp_path/'roundtrip/result.json').read_text())
    assert report['generation_profile']=='dsn-process-v2' and report['accepted'] is False and report['source_unchanged']


@pytest.mark.parametrize('name',['native','dsn-safe-v1','dsn-process-v2'])
def test_usb_worker_emits_the_actual_selected_profile(name,capsys):
    from smilepcb.keyboard_routing.generation import generation_profile
    source=Path(__file__).resolve().parents[1]/'smilepcb/stm32/route_native.py'
    emit=next((n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='emit'),None)
    assert emit is not None,'Every USB worker result must expose its actual creation context'
    with generation_profile(name) as creation:
        env={'json':json,'creation':creation};exec(compile(ast.Module(body=[emit],type_ignores=[]),str(source),'exec'),env)
        env['emit']({'accepted':False,'reason':'negative control'})
    assert json.loads(capsys.readouterr().out)=={'accepted':False,'reason':'negative control','generation_profile':name}
