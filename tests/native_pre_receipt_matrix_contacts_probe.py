"""Physical interior/opposite-face/filled-contact and exact rollback controls."""
import json,sys,time
from pathlib import Path
from types import SimpleNamespace
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import project_settings,apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing import field_hygiene as hygiene,cleanup
from smilepcb.keyboard_routing.completion import pad_partitions

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
def vec(x,y):return p.VECTOR2I(round(x*1e6),round(y*1e6))
def make(folder,face):
    folder.mkdir();b=p.BOARD();b.SetCopperLayerCount(2);apply_native_rules(b,COMPACT_VIAS)
    for net in ("matrix","plane-contact","source"):b.Add(p.NETINFO_ITEM(b,net))
    for a,z in zip(((0,0),(30,0),(30,20),(0,20)),((30,0),(30,20),(0,20),(0,0))):
        e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(*a));e.SetEnd(vec(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
    other=p.B_Cu if face==p.F_Cu else p.F_Cu
    def pad(ref,xy,net,layer,pth=False):
        f=p.FOOTPRINT(b);f.SetReference(ref);f.SetPosition(vec(*xy));f.SetLayer(layer);b.Add(f)
        d=p.PAD(f);d.SetNumber("1");d.SetNet(b.FindNet(net));d.SetPosition(vec(*xy));d.SetShape(p.PAD_SHAPE_CIRCLE if pth else p.PAD_SHAPE_RECT);d.SetSize(vec(.9,.9));d.SetAttribute(p.PAD_ATTRIB_PTH if pth else p.PAD_ATTRIB_SMD)
        if pth:d.SetLayerSet(p.LSET.AllCuMask());d.SetDrillSize(vec(.3,.3))
        else:
            ls=p.LSET();ls.AddLayer(layer);d.SetLayerSet(ls)
        f.Add(d)
    def track(a,z,net,layer,locked=False):
        t=p.PCB_TRACK(b);t.SetStart(vec(*a));t.SetEnd(vec(*z));t.SetWidth(150000);t.SetLayer(layer);t.SetNet(b.FindNet(net));t.SetLocked(locked);b.Add(t);return t.m_Uuid.AsString()
    pad("A",(5,5),"matrix",face);pad("B",(15,5),"matrix",other);pad("PTH",(10,5),"matrix",other,True)
    source=track((5,5),(6,5),"matrix",face)
    essential=track((6,5),(8,5),"matrix",face);landing=track((8,5),(15,5),"matrix",other)
    duplicate=track((9,5),(11,5),"matrix",other);tail=track((12,5),(12,8),"matrix",other)
    via=p.PCB_VIA(b);via.SetPosition(vec(8,5));via.SetWidth(500000);via.SetDrill(200000);via.SetLayerPair(p.F_Cu,p.B_Cu);via.SetNet(b.FindNet("matrix"));b.Add(via)
    pad("POUR",(20,5),"plane-contact",face);pad("REMOTE",(22,10),"plane-contact",face)
    plane_lead=track((22,10),(22,5),"plane-contact",face)
    fixed=track((26,15),(28,15),"source",face,True)
    zone=p.ZONE(b);zone.SetLayer(face);zone.SetNet(b.FindNet("plane-contact"));outline=zone.Outline();outline.NewOutline()
    for x,y in ((18,3),(25,3),(25,7),(18,7)):outline.Append(p.FromMM(x),p.FromMM(y))
    b.Add(zone);path=folder/"keyboard.kicad_pcb";(folder/"keyboard.kicad_pro").write_text(json.dumps(project_settings(COMPACT_VIAS)))
    assert p.SaveBoard(str(path),b);b=p.LoadBoard(str(path));assert p.ZONE_FILLER(b).Fill(b.Zones())
    owned={essential,landing,duplicate,tail,via.m_Uuid.AsString(),plane_lead}
    return b,{source,fixed},owned,{"essential":essential,"landing":landing,"duplicate":duplicate,"tail":tail,"plane_lead":plane_lead,"via":via.m_Uuid.AsString()}

def parts(board):return {frozenset(g) for g in pad_partitions(board,time.monotonic()+10)}
report={}
for name,face in (("front",p.F_Cu),("back",p.B_Cu)):
    board,source,owned,ids=make(out/name,face);before=snapshot(board);groups=parts(board)
    result=hygiene.clean_matrix_tracks(board,owned_ids=owned,source_ids=source,nets={"matrix","plane-contact"},deadline=time.monotonic()+10)
    after=snapshot(board);path=out/name/"keyboard.kicad_pcb";assert p.SaveBoard(str(path),board);saved=p.LoadBoard(str(path));assert p.ZONE_FILLER(saved).Fill(saved.Zones())
    report[name]={"duplicates_and_tail_removed":{ids["duplicate"],ids["tail"]}<=set(result["removed_ids"]),
        "all_actual_pad_contacts_exact":parts(board)==groups==parts(saved),"source_exact":all(after["tracks"].get(u)==before["tracks"][u] for u in source),
        "interior_pth_via_and_plane_leads_retained":all(after["tracks"].get(ids[k])==before["tracks"][ids[k]] for k in ("essential","landing","plane_lead","via")),
        "saved_copper_exact":snapshot(saved)==after,"pad_group_sizes":sorted(map(len,groups))}
    for failure in ("contact-loss","expired"):
        trial,original_ids,generated,keys=make(out/(name+"-"+failure),face);snap=snapshot(trial);oldparts=parts(trial)
        original_tail=cleanup.remove_tails;original_clock=hygiene.time;clock=[time.monotonic()];stop=clock[0]+10
        def rejected(candidate,**kwargs):
            if failure=="contact-loss":
                t=next(t for t in candidate.GetTracks() if t.m_Uuid.AsString()==keys["essential"]);candidate.Delete(t)
                return {"removed_ids":[keys["essential"]],"trimmed_ids":[]}
            result=original_tail(candidate,**kwargs);clock[0]=stop+1;return result
        cleanup.remove_tails=rejected;hygiene.time=SimpleNamespace(monotonic=lambda:clock[0])
        try:
            try:hygiene.clean_matrix_tracks(trial,owned_ids=generated,source_ids=original_ids,nets={"matrix","plane-contact"},deadline=stop);error=None
            except (ValueError,TimeoutError) as exc:error=type(exc).__name__
        finally:cleanup.remove_tails=original_tail;hygiene.time=original_clock
        report[name+"-"+failure]={"error":error,"copper_and_pads_exact":snapshot(trial)==snap,"refilled_contacts_exact":parts(trial)==oldparts}
(out/"report.json").write_text(json.dumps(report,indent=2)+"\n")
