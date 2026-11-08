"""The normal coordinator consumes the same minimum that qualified sources."""
import ast,json,math,shutil,sys,time,subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from smilepcb.usb_policy import configured_usb_max_length_nm
from smilepcb.keyboard_routing.generated_provenance import tracked_worker
from smilepcb.keyboard_phases import ordered_nets
from smilepcb.keyboard_process import project_settings,COMPACT_VIAS,LEGACY_VIAS

ROOT=Path(__file__).resolve().parents[1]
class Captured(Exception):pass

@pytest.mark.parametrize('profile,min_width,expected',[(COMPACT_VIAS,.15,150000),(LEGACY_VIAS,.15,150000),(COMPACT_VIAS,.2,200000),
    *[(COMPACT_VIAS,value,None) for value in (0,-.1,float('inf'),float('nan'),True,'0.15')]])
def test_control_request_uses_project_qualified_width(monkeypatch,tmp_path,profile,min_width,expected):
    source=tmp_path/'layouts/fixture/project';source.mkdir(parents=True)
    cfg={'parts':[{'ref':'U_MCU','region':'controller','nets':{'1':'row','2':'column','3':'signal'}}],
        'layout':{'gpio':{'rows':['A'],'cols':['B']},'rgb':False,'rgb_order':[]},'mcu_gpio_pads':{'A':'1','B':'2'},'plane_nets':['GND']}
    (source/'.smilepcb-keyboard.json').write_text(json.dumps(cfg));(source/'.smilepcb-field.json').write_text('{}')
    rules=project_settings(profile);rules['board']['design_settings']['rules']['min_track_width']=min_width
    (source/'keyboard.kicad_pro').write_text(json.dumps(rules));requests=[]
    (source/'keyboard.kicad_pcb').write_text('(kicad_pcb)')
    def worker(script,request,log,**kwargs):
        if request['operation']=='components':requests.append(request);raise Captured()
        return {'accepted':True,'generation_profile':'dsn-process-v2','scope':'matrix-with-controller','controller_connected':True,'field_generation_profile':'dsn-process-v2'}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_build',SimpleNamespace(BRIDGE=Path('native.py')))
    monkeypatch.setitem(sys.modules,'smilepcb.lab_tools',SimpleNamespace(worker=worker,rule_checks=lambda *a,**k:None))
    monkeypatch.setitem(sys.modules,'smilepcb.runtime',SimpleNamespace(environment=lambda:{}))
    monkeypatch.setattr(sys,'argv',['route.py','tools','routed','layouts','fixture'])
    path=ROOT/'smilepcb/stm32/route.py';node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='main')
    env={'configured_usb_max_length_nm':configured_usb_max_length_nm,'__name__':'smilepcb.stm32._width_host','__package__':'smilepcb.stm32','Path':Path,'json':json,'math':math,'shutil':shutil,'sys':sys,'time':time,'subprocess':subprocess,
         'quality_from_args':lambda a:'turns','load_tools':lambda p:{},'workspace':lambda:tmp_path,'file_hashes':lambda p:{},
         'configure_kicad':lambda *a:None,'ordered_nets':ordered_nets,'write_manifest':lambda *a,**k:None,'PROFILE':'dsn-process-v2','__file__':str(path),'tracked_worker':tracked_worker}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),env)
    if expected is None:
        with pytest.raises(ValueError,match='positive finite project track minimum'):env['main']()
        assert requests==[]
        return
    with pytest.raises(Captured):env['main']()
    assert len(requests)==1
    assert requests[0]['include_nets']==['signal']
    assert requests[0]['track_width_nm']==expected
    assert requests[0]['connection_budget_s']==3 and requests[0]['grid_step_nm']==500000
