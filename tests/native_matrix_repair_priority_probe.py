"""Actual pad proof must reach repair before a fine retry spends the stage."""
import json,sys,time
from pathlib import Path
from types import SimpleNamespace
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing import matrix_stage as stage
from smilepcb.keyboard_routing.field_contract import capture,verify_native
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_phases import ordered_nets
from native_collector_flow_fixture import fixture

original_route,original_repair,original_time=stage.route_components,stage.repair_feeders,stage.time
records={}
for name,face in (("front",p.F_Cu),("back",p.B_Cu)):
    board,cfg=fixture(face);before=snapshot(board)
    chosen=dict(ordered_nets(cfg))["rows"][0];events=[];offset=[0.0]
    started=time.monotonic();deadline=started+35
    def partial(candidate,router,nets,**kwargs):
        events.append({"kind":"feeders","grid_nm":router.grid_step})
        count=original_route(candidate,router,set(nets)-{chosen},**kwargs)
        if router.grid_step==125000:
            # A failed fine search can legitimately consume all its remaining
            # stage allowance. Advance only the coordinator clock; no sleeps.
            offset[0]=deadline-time.monotonic()+1
        return count
    def repair(candidate,nets,protected,**kwargs):
        events.append({"kind":"repair","deadline":kwargs["deadline"]})
        return original_repair(candidate,nets,protected,**kwargs)
    stage.route_components,stage.repair_feeders=partial,repair
    stage.time=SimpleNamespace(monotonic=lambda:time.monotonic()+offset[0])
    try:
        with generation_profile("dsn-safe-v1"):
            result=stage.prepare_matrix(board,cfg,row_layer=face,
                column_layer=p.B_Cu if face==p.F_Cu else p.F_Cu,deadline=deadline)
    finally:
        stage.route_components,stage.repair_feeders,stage.time=original_route,original_repair,original_time
    proof=False
    if result["complete"]:
        verify_native(board,capture(snapshot(board),result),deadline=time.monotonic()+5);proof=True
    records[name]={"complete":result["complete"],"physical_proof":proof,
        "events":events,"within_original_deadline":all(e.get("deadline",deadline)<=deadline for e in events),
        "footprints_exact":snapshot(board)["footprints"]==before["footprints"],
        "pending":result.get("pending_matrix")}
from smilepcb.keyboard_routing.grid_search import valid_plan
for name,face in (("front-policy",p.F_Cu),("back-policy",p.B_Cu)):
    board,cfg=fixture(face);source=next(d for f in board.GetFootprints() if f.GetReference()=="U_MCU" for d in f.Pads() if d.GetNumber()=="1")
    a=(source.GetPosition().x,source.GetPosition().y);tip=(a[0]+1000000,a[1])
    lead=p.PCB_TRACK(board);lead.SetStart(p.VECTOR2I(*a));lead.SetEnd(p.VECTOR2I(*tip));lead.SetLayer(face);lead.SetWidth(150000);lead.SetNet(source.GetNet());board.Add(lead)
    port={"net":source.GetNetname(),"pad_ids":[source.m_Uuid.AsString()],"added_ids":[lead.m_Uuid.AsString()],
        "terminal_id":lead.m_Uuid.AsString(),"kind":"same_layer","heading_origin_nm":a,"exit_nm":tip,"exit_layer":face}
    observed=[]
    def observe(candidate,router,nets,**kwargs):
        if port["net"] in nets and not observed:
            # A same-net micro-edge inside the MCU pad is physically clear but
            # must not bypass this already prepared live escape terminal.
            observed.append(valid_plan(router,[(face,[a,(a[0]+125000,a[1])])],[],port["net"]))
        return original_route(candidate,router,nets,**kwargs)
    stage.route_components=observe
    try:
        with generation_profile("dsn-safe-v1"):
            result=stage.prepare_matrix(board,cfg,row_layer=face,
                column_layer=p.B_Cu if face==p.F_Cu else p.F_Cu,deadline=time.monotonic()+35,ports=[port])
    finally:stage.route_components=original_route
    records[name]={"internal_pad_shortcut_admitted":observed,"complete":result["complete"]}
Path(sys.argv[1]).write_text(json.dumps(records,indent=2)+"\n")
