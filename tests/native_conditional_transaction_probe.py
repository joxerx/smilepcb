"""Native ownership, complete commit and post-commit receipt-phase rollback."""
from copy import deepcopy
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing import matrix_stage
from smilepcb.keyboard_routing.controller_ports import route_controller_ports
from smilepcb.keyboard_routing.conditional_banks import certify_generated_heads,prepare_conditional_banks
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory
from smilepcb.keyboard_routing.field import build_field
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.feeder_repair import _pad_state
from native_collector_flow_fixture import fixture

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);records={}
def setup(mode="prefer_same_layer"):
    b,cfg=fixture(p.B_Cu)
    next(d for f in b.GetFootprints() if f.GetReference()=='U_MCU' for d in f.Pads() if d.GetNumber()=='1').SetLocalClearance(100000)
    from smilepcb.keyboard_process import apply_native_rules
    apply_native_rules(b,'fr4-2l-1oz-0p5-0p2');before=snapshot(b)
    proof=route_controller_ports(b,controller_refs={'U_MCU','C_RETURN'},mcu_refs={'U_MCU'},plane_nets=cfg['plane_nets'],
        include_nets={'r0','r1','c0','c1'},access_mode=mode,continuation_nm=500000,clearance_nm=173000,deadline=time.monotonic()+10)
    assert proof['accepted'],proof
    token=certify_generated_heads(b,cfg,before,proof['ports'],deadline=time.monotonic()+5)
    return b,cfg,proof['ports'],token
with generation_profile('dsn-process-v2'):
    b,cfg,ports,token=setup('via')
    via=next(t for t in b.GetTracks() if isinstance(t,p.PCB_VIA) and t.m_Uuid.AsString() in token.tracks)
    via.SetFrontTentingMode(p.TENTING_MODE_NOT_TENTED)
    state=snapshot(b);prior={'tracks':{u:q for u,q in state['tracks'].items() if u not in token.tracks},'footprints':state['footprints']}
    try:certify_generated_heads(b,cfg,prior,ports,deadline=time.monotonic()+5)
    except ValueError as error:
        assert 'unrepresentable' in str(error),str(error)
        records['property']={'rejected':True,'reason':str(error),'unchanged':snapshot(b)==state,
            'raw_override_unchanged':via.GetFrontTentingMode()==p.TENTING_MODE_NOT_TENTED}
    else:raise AssertionError('Unrepresentable fresh via property was certified')
    assert records['property']['unchanged'] and records['property']['raw_override_unchanged']
    b,cfg,ports,token=setup()
    field=build_field(b,cfg,row_layer=p.B_Cu,column_layer=p.F_Cu,deadline=time.monotonic()+25);assert field['complete'],field
    item=next(t for t in b.GetTracks() if t.m_Uuid.AsString() in token.tracks);item.SetWidth(item.GetWidth()+1000)
    before=snapshot(b)
    try:prepare_conditional_banks(b,cfg,field,ports,token,deadline=time.monotonic()+10)
    except ValueError as error:records['stale']={'rejected':True,'reason':str(error),'unchanged':snapshot(b)==before}
    else:raise AssertionError('Stale generated-head ownership was admitted')
    assert records['stale']['unchanged']
    b,cfg,ports,token=setup();before=snapshot(b);model=deepcopy(cfg)
    import smilepcb.keyboard_routing.matrix_polish as polish
    old=polish.polish_matrix
    def reject_after_commit(*a,**kw):raise ValueError('Injected post-commit polish rejection')
    polish.polish_matrix=reject_after_commit
    with reserve_launches(ports):
        inv=current_launch_inventory();prior=(deepcopy(inv.ports),set(inv.resolved),set(inv.repair_reserved))
        try:
            matrix_stage.prepare_matrix(b,cfg,row_layer=p.B_Cu,column_layer=p.F_Cu,deadline=time.monotonic()+45,
                ports=ports,collectors=True,generated_heads=token)
        except ValueError as error:
            assert str(error)=='Injected post-commit polish rejection',str(error)
            records['post_commit']={'rejected':True,'unchanged':snapshot(b)==before,'model_unchanged':cfg==model,
                'launch_unchanged':(inv.ports,inv.resolved,inv.repair_reserved)==prior}
        else:raise AssertionError('Expected injected post-commit failure was not reached')
        finally:polish.polish_matrix=old
    assert all(records['post_commit'][k] for k in ('unchanged','model_unchanged','launch_unchanged'))
    assert next(d for f in b.GetFootprints() if f.GetReference()=='U_MCU' for d in f.Pads() if d.GetNumber()=='1').GetLocalClearance()==100000
    records['post_commit']['raw_pad_override_preserved']=True
    b,cfg,ports,token=setup()
    with reserve_launches(ports):
        report=matrix_stage.prepare_matrix(b,cfg,row_layer=p.B_Cu,column_layer=p.F_Cu,deadline=time.monotonic()+45,
            ports=ports,collectors=True,generated_heads=token)
    records['success']=report
    assert report['complete'] and report['conditional_banks']['accepted'],report
    for q in report['matrix_ports']:
        assert set(q['added_ids'])<=set(snapshot(b)['tracks'])
    assert not _pad_state(copper_graph(b,{'r0','r1','c0','c1'},time.monotonic()+5),{'r0','r1','c0','c1'})['missing']
    from smilepcb.keyboard_routing.interface_contract import capture_interfaces
    from smilepcb.keyboard_routing.field_contract import capture,verify_native
    receipt=capture_interfaces(b,cfg,report['matrix_ports'],deadline=time.monotonic()+5)
    contract=capture(snapshot(b),report);verify_native(b,contract,deadline=time.monotonic()+5)
    records['success_receipts']={'interfaces_verified':bool(receipt),'matrix_contract_verified':True}
(out/'report.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps({'stale_rejected_unchanged':True,'post_commit_rollback_exact':True,'complete_actual_ports':True}))
