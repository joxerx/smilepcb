"""Native adapter seam: original heads/collector inputs, changed feeder order.

A deliberately suppressed last feeder leaves a real native missing component.
This tests dispatch and state propagation; physical causality is established by
separate unchanged-input A/B/A experiment, not by this injected failure.
"""
from copy import deepcopy
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules
from smilepcb.keyboard_routing import conditional_banks as banks,collectors
from smilepcb.keyboard_routing.controller_ports import route_controller_ports
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.field import build_field
from smilepcb.keyboard_routing.interface_launches import reserve_launches
from native_collector_flow_fixture import fixture

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);records={}
def write():(out/'report.json').write_text(json.dumps(records,indent=2)+'\n')
real_banks=banks.physical_banks;real_search=banks.conditional_search
real_route=banks.route_components;real_collectors=collectors.route_collectors
captured={};phase=['A'];skip_net=[None];feeder_calls={'A':[],'B':[]};collector_orders={'A':[],'B':[]}
def physical(*args,**kwargs):
    answer=real_banks(*args,**kwargs);captured['banks']=answer[0];return answer
def no_collectors(board,cfg,ports,**kwargs):
    collector_orders[phase[0]].append([q['net'] for q in ports])
    return {'accepted':False,'closed_nets':[],'added_ids':[],'ports':deepcopy(ports),
            'reason':'Regression isolates native feeder dispatch from collector search'}
def route(board,router,nets,**kwargs):
    feeder_calls[phase[0]].append(sorted(nets))
    if phase[0]=='A' and nets=={skip_net[0]}:
        router.graph_diagnostic={'test_injected_skipped_feeder':True};return 0
    return real_route(board,router,nets,**kwargs)
def search(order,attempt,*,deadline,**kwargs):
    bank=order[0];ports=captured['banks'][bank];assert len(ports)>=2
    source_order=[q['net'] for q in ports];skip_net[0]=source_order[-1]
    first=attempt(bank,'original',[],[],deadline);records['original']=first;write()
    assert not first['accepted'] and first.get('feedback_basis')=='completed_final_native_graph_after_scheduled_routing',first
    assert first['failed_interfaces']==[sorted(ports[-1]['pad_ids'])],first
    phase[0]='B';second=attempt(bank,'original_priority',[],deepcopy(first['failed_interfaces']),deadline)
    records.update(priority=second,collector_orders=collector_orders,feeder_calls=feeder_calls,
                   expected_source_order=source_order,expected_feeder_order=[source_order[-1]]+source_order[:-1]);write()
    assert first['parent_sha256']==second['parent_sha256'],'Priority changed native parent'
    assert first['head_geometry_sha256']==second.get('head_geometry_sha256'),'Priority regenerated source heads'
    assert collector_orders['A']==collector_orders['B']==[source_order],'Priority changed original collector inputs'
    assert feeder_calls['B'][0]==[skip_net[0]],'Priority did not change feeder iteration after collectors'
    assert second['accepted'] and second.get('creation_checked_items',0)>0,second
    assert second['source_order']==source_order and second['feeder_order']==records['expected_feeder_order']
    return {'accepted':False,'selection':[],'reason':'native_priority_adapter_regression_only'}

try:
    with generation_profile('dsn-process-v2'):
        board,cfg=fixture(p.B_Cu);apply_native_rules(board,'fr4-2l-1oz-0p5-0p2');before=snapshot(board)
        heads=route_controller_ports(board,controller_refs={'U_MCU','C_RETURN'},mcu_refs={'U_MCU'},plane_nets=cfg['plane_nets'],
            include_nets={'r0','r1','c0','c1'},access_mode='prefer_same_layer',continuation_nm=500000,
            clearance_nm=173000,deadline=time.monotonic()+10)
        assert heads['accepted'],heads
        token=banks.certify_generated_heads(board,cfg,before,heads['ports'],deadline=time.monotonic()+5)
        field=build_field(board,cfg,row_layer=p.B_Cu,column_layer=p.F_Cu,deadline=time.monotonic()+25)
        assert field['complete'],field
        entry=snapshot(board);model=deepcopy(cfg)
        banks.physical_banks=physical;banks.conditional_search=search;banks.route_components=route;collectors.route_collectors=no_collectors
        with reserve_launches(heads['ports']):
            result=banks.prepare_conditional_banks(board,cfg,field,heads['ports'],token,deadline=time.monotonic()+45)
        assert not result['accepted'] and snapshot(board)==entry and cfg==model
        records.update(caller_unchanged=True,model_unchanged=True,full_matrix_accepted=False,passed=True);write()
finally:
    collectors.route_collectors=real_collectors;banks.route_components=real_route
    banks.conditional_search=real_search;banks.physical_banks=real_banks
print(json.dumps({'priority_preserves_original_heads_and_collector_order':True,'changed_feeder_order_native_complete':True}))
