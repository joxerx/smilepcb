"""The coordinator tries ordinary paths before aggressive leftover search."""
import json
import sys

import pytest

from smilepcb.stm32 import route
from smilepcb.keyboard_process import COMPACT_VIAS,project_settings
from smilepcb import lab_tools


class RequestsCaptured(BaseException):
    """Stop before unrelated native planes/checks/recovery operations."""


def test_ordered_phases_request_normal_then_completion_search(monkeypatch,tmp_path):
    # A 200um project minimum also distinguishes qualified control width from
    # ordinary RGB width while exercising the real role-derived phase order.
    source=tmp_path/'layouts/fixture/project'
    source.mkdir(parents=True)
    cfg={'parts':[
        {'ref':'U_MCU','region':'controller','nets':{
            '1':'row_wire','2':'column_wire','3':'reset_wire','4':'strip_in','5':'GND'}},
        {'ref':'LED7','region':'keys','nets':{'3':'strip_in','2':'rgb_link'}},
        {'ref':'LED3','region':'keys','nets':{'3':'rgb_link','2':'unused_tail'}}],
        'layout':{'gpio':{'rows':['RA'],'cols':['CA']},'rgb':True,'rgb_order':[7,3]},
        'mcu_gpio_pads':{'RA':'1','CA':'2'},'plane_nets':['GND']}
    (source/'.smilepcb-keyboard.json').write_text(json.dumps(cfg))
    (source/'.smilepcb-field.json').write_text('{}')
    process=project_settings(COMPACT_VIAS)
    process['board']['design_settings']['rules']['min_track_width']=.2
    (source/'keyboard.kicad_pro').write_text(json.dumps(process))
    (source/'keyboard.kicad_pcb').write_text('(kicad_pcb)')
    requests=[]

    def worker(script,request,log,*,timeout_s):
        # Substitute only the external native boundary: main still chooses
        # phases, grids, widths, worker limits and the actual request payload.
        if request['operation']=='components':
            requests.append((dict(request),timeout_s))
            if len(requests)==4:
                raise RequestsCaptured()
        return {'accepted':True,'generation_profile':'dsn-process-v2',
                'scope':'matrix-with-controller','controller_connected':True,
                'field_generation_profile':'dsn-process-v2',
                'connections':0,'pad_group_gaps':1}

    monkeypatch.setenv('SMILEPCB_STM32_WORKSPACE',str(tmp_path))
    monkeypatch.setattr(sys,'argv',['route.py','tools.json','routed','layouts','fixture',
                                    '--routing-quality','turns'])
    monkeypatch.setattr(route,'load_tools',lambda path:{})
    monkeypatch.setattr(route,'configure_kicad',lambda *args:None)
    # Native seed-receipt validity is covered separately. This request-only
    # fixture never loads a board or claims a valid preparation receipt.
    monkeypatch.setattr(route,'write_manifest',lambda *args,**kwargs:None)
    monkeypatch.setattr(lab_tools,'worker',worker)
    with pytest.raises(RequestsCaptured):
        route.main()

    assert [(q['include_nets'],q['grid_step_nm'],q['completion_search']) for q,_ in requests]==[
        (['reset_wire'],500000,False),
        (['reset_wire'],125000,True),
        (['strip_in','rgb_link'],500000,False),
        (['strip_in','rgb_link'],125000,True)]
    assert [q['track_width_nm'] for q,_ in requests]==[200000,200000,150000,150000]
    assert all(q['connection_budget_s']==3 and timeout==90 for q,timeout in requests)
    assert all(q['generation_profile']=='dsn-process-v2' and q['routing_quality']=='turns'
               for q,_ in requests)
