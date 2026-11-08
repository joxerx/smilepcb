"""Complete real matrix geometry must be clean before its fixed receipt."""
import json,sys,time
from types import SimpleNamespace
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import project_settings,apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing import matrix_stage as stage
from smilepcb.keyboard_routing.field_contract import capture,verify_native
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.completion import pad_partitions
from native_collector_flow_fixture import fixture

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
original=stage.build_field;report={}
for name,face in (("front",p.F_Cu),("back",p.B_Cu),("front-expired",p.F_Cu),("back-expired",p.B_Cu)):
    folder=out/name;folder.mkdir();path=folder/"keyboard.kicad_pcb"
    board,cfg=fixture(face)
    # Selected process sidecar exists before LoadBoard fixes native caches.
    (folder/"keyboard.kicad_pro").write_text(json.dumps(project_settings(COMPACT_VIAS)))
    apply_native_rules(board,COMPACT_VIAS)
    board.Add(p.NETINFO_ITEM(board,"source"))
    anchor=p.PCB_TRACK(board);anchor.SetStart(p.VECTOR2I(37000000,35000000));anchor.SetEnd(p.VECTOR2I(38000000,35000000))
    anchor.SetWidth(150000);anchor.SetLayer(face);anchor.SetNet(board.FindNet("source"));anchor.SetLocked(True);board.Add(anchor)
    assert p.SaveBoard(str(path),board);board=p.LoadBoard(str(path));before=snapshot(board);injected={}
    own_before=sorted({d.GetOwnClearance(p.F_Cu) for f in board.GetFootprints() for d in f.Pads() if d.GetNumber()})
    def with_redundant_access(candidate,model,**kwargs):
        result=original(candidate,model,**kwargs)
        assert result["complete"],result
        tracks={t.m_Uuid.AsString():t for t in candidate.GetTracks()}
        trunk=next(tracks[u] for row in result["rows"] for u in row["trunk_ids"] if tracks[u].GetLength()>2000000)
        a,z=trunk.GetStart(),trunk.GetEnd()
        duplicate=p.PCB_TRACK(candidate);duplicate.SetStart(p.VECTOR2I((3*a.x+z.x)//4,(3*a.y+z.y)//4))
        duplicate.SetEnd(p.VECTOR2I((a.x+3*z.x)//4,(a.y+3*z.y)//4));duplicate.SetWidth(trunk.GetWidth());duplicate.SetLayer(trunk.GetLayer());duplicate.SetNet(trunk.GetNet());candidate.Add(duplicate)
        diode=next(f for f in candidate.GetFootprints() if f.GetReference()=="D1")
        pad=next(d for d in diode.Pads() if d.GetNumber()=="1");q=pad.GetPosition()
        spur=p.PCB_TRACK(candidate);spur.SetStart(q);spur.SetEnd(p.VECTOR2I(q.x-3000000,q.y));spur.SetWidth(150000);spur.SetLayer(face);spur.SetNet(pad.GetNet());candidate.Add(spur)
        injected.update(duplicate=duplicate.m_Uuid.AsString(),spur=spur.m_Uuid.AsString(),trunk=trunk.m_Uuid.AsString())
        for key in ("duplicate","spur"):
            uid=injected[key];result["roles"][uid]="row_access";result["owned_ids"].append(uid)
        return result
    stage.build_field=with_redundant_access
    original_hygiene=stage.clean_matrix_tracks
    if name.endswith("-expired"):
        def expire_after_mutation(*args,**kwargs):
            from smilepcb.keyboard_routing import cleanup,field_hygiene
            original_tails=cleanup.remove_tails;original_clock=field_hygiene.time
            clock=[time.monotonic()]
            def expire(candidate,**limits):
                value=original_tails(candidate,**limits);clock[0]=kwargs["deadline"]+1;return value
            cleanup.remove_tails=expire;field_hygiene.time=SimpleNamespace(monotonic=lambda:clock[0])
            try:return original_hygiene(*args,**kwargs)
            finally:cleanup.remove_tails=original_tails;field_hygiene.time=original_clock
        stage.clean_matrix_tracks=expire_after_mutation
    try:
        with generation_profile("dsn-process-v2"):
            result=stage.prepare_matrix(board,cfg,row_layer=face,column_layer=p.B_Cu if face==p.F_Cu else p.F_Cu,deadline=time.monotonic()+35)
    finally:stage.build_field=original;stage.clean_matrix_tracks=original_hygiene
    assert result["complete"],result
    after=snapshot(board);contract=capture(after,result);verify_native(board,contract,deadline=time.monotonic()+5)
    assert p.SaveBoard(str(path),board);saved=p.LoadBoard(str(path));verify_native(saved,contract,deadline=time.monotonic()+5)
    report[name]={"complete":result["complete"],"duplicate_retired":injected["duplicate"] not in after["tracks"],
        "spur_retired":injected["spur"] not in after["tracks"],"trunk_retained":injected["trunk"] in after["tracks"],
        "source_exact":all(after["tracks"].get(u)==s for u,s in before["tracks"].items()),
        "footprints_exact":before["footprints"]==after["footprints"],
        "saved_exact":snapshot(saved)==after,"all_actual_matrix_pads_proved":True,
        "own_clearances_nm":own_before,"hygiene":result.get("pre_receipt_hygiene"),"injected":injected}
(out/"report.json").write_text(json.dumps(report,indent=2)+"\n")
