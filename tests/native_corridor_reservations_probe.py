"""Real two-face corridor coupon; run only through explicit Native interpreter."""
import json
import sys
from pathlib import Path

from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,project_settings
from smilepcb.keyboard_routing.corridor_reservations import reserve_corridors,current_corridors
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.virtual_copper import VirtualCopper

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
path=out/'fixture.kicad_pcb'
path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')))
board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,'fr4-2l-1oz-0p5-0p2')
for net in ('USB','lane-owner','foreign'):board.Add(p.NETINFO_ITEM(board,net))
corners=[(0,0),(40000000,0),(40000000,40000000),(0,40000000)]
for a,b in zip(corners,corners[1:]+corners[:1]):
    edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vec(a));edge.SetEnd(vec(b));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
fp=p.FOOTPRINT(board);fp.SetReference('FIXED');fp.SetFPID(p.LIB_ID('Fixture','Corridor'));board.Add(fp)
for number,net,at in [('1','USB',(10000000,5000000)),('2','USB',(30000000,5000000)),
                      ('3','lane-owner',(35000000,30000000)),('4','foreign',(35000000,35000000))]:
    pad=p.PAD(fp);pad.SetNumber(number);pad.SetNet(board.FindNet(net));pad.SetPosition(vec(at));pad.SetAttribute(p.PAD_ATTRIB_SMD)
    pad.SetShape(p.PAD_SHAPE_RECT);pad.SetSize(vec((1000000,1000000)));layers=p.LSET();layers.AddLayer(p.F_Cu);pad.SetLayerSet(layers);fp.Add(pad)
t=p.PCB_TRACK(board);t.SetStart(vec((10000000,5000000)));t.SetEnd(vec((30000000,5000000)));t.SetWidth(200000);t.SetLayer(p.F_Cu);t.SetNet(board.FindNet('USB'));board.Add(t)
assert p.SaveBoard(str(path),board)
board=p.LoadBoard(str(path));before=snapshot(board)
router=LocalRouter(board,track_width_nm=200000)
row=dict(id='front-lane',layer=0,nets=['lane-owner'],path=[(10000000,20000000),(30000000,20000000)],width_nm=1000000)
probe=p.SHAPE_SEGMENT(vec((20000000,18000000)),vec((20000000,22000000)),200000)
report={}
with reserve_corridors([row]):
    report['foreign_blocked']=not router.clear(probe,p.F_Cu,'foreign')
    report['owner_allowed']=router.clear(probe,p.F_Cu,'lane-owner')
    report['back_face_allowed']=router.clear(probe,p.B_Cu,'foreign')
    physical=p.SHAPE_SEGMENT(vec((20000000,4000000)),vec((20000000,6000000)),200000)
    report['owner_still_obeys_physical_clearance']=not router.clear(physical,p.F_Cu,'lane-owner')
    for label,instance in [('actual',router),('ghost',VirtualCopper(board,track_width_nm=200000))]:
        count=len(list(board.GetTracks()));added=len(instance.added)
        try:instance.add_path([(5000000,10000000),(5000000,20000000),(20000000,20000000)],p.F_Cu,'foreign')
        except ValueError:pass
        else:raise AssertionError('Foreign path accepted')
        assert len(list(board.GetTracks()))==count and len(instance.added)==added
        if label=='ghost':assert instance.ghosts==instance.operations==[]
        report[label+'_no_prefix_leak']=True
    report['via_both_faces_blocked']=True
    router.add_path([(12000000,20000000),(28000000,20000000)],p.F_Cu,'lane-owner')
    report['owner_emitted']=len(router.added)==1
for face in (0,2):
    with reserve_corridors([{**row,'id':'via-face-'+str(face),'layer':face}]):
        count=len(list(board.GetTracks()))
        try:router.add_via((20000000,20000000),'foreign')
        except ValueError:pass
        else:raise AssertionError('Foreign via accepted')
        assert len(list(board.GetTracks()))==count
assert current_corridors()==()
# Reserve the future through-via's annulus independently on both real faces,
# before any physical via exists. A foreign B trace must respect it already.
future_center=(25000000,30000000)
future_cross=[(24000000,30000000),(26000000,30000000)]
future_shape=p.SHAPE_SEGMENT(vec(future_cross[0]),vec(future_cross[1]),200000)
assert not any(isinstance(item,p.PCB_VIA) for item in board.GetTracks())
assert router.clear(future_shape,p.B_Cu,'foreign')
future=[dict(kind='via',id='future-annulus-'+str(face),layer=face,nets=['lane-owner'],
             center_nm=list(future_center),radius_nm=600000) for face in (0,2)]
with reserve_corridors(future):
    report['future_annulus_blocks_foreign_back_trace']=not router.clear(future_shape,p.B_Cu,'foreign')
    report['future_annulus_owner_bridge_allowed']=router.clear(future_shape,p.B_Cu,'lane-owner')
    count=len(list(board.GetTracks()))
    try:router.add_path(future_cross,p.B_Cu,'foreign')
    except ValueError:pass
    else:raise AssertionError('Foreign B trace crossed future via annulus')
    assert len(list(board.GetTracks()))==count
    # Exact circle, not a square/capsule surrogate: this diagonal lies outside
    # radius 0.6 mm plus its 0.1 mm copper half width but inside the disk bbox.
    corner=p.SHAPE_SEGMENT(vec((25550000,30550000)),vec((25650000,30650000)),200000)
    report['future_annulus_uses_exact_circle']=router.clear(corner,p.B_Cu,'foreign')
    router.add_path(future_cross,p.B_Cu,'lane-owner')
    assert router.via_clear(future_center,'lane-owner')
    router.add_via(future_center,'lane-owner')
    report['future_annulus_owner_bridge_emitted']=len(list(board.GetTracks()))==count+2
assert current_corridors()==()
after=snapshot(board)
assert after['footprints']==before['footprints']
assert all(after['tracks'][uid]==value for uid,value in before['tracks'].items())
assert p.SaveBoard(str(path),board)
reloaded=p.LoadBoard(str(path));saved=snapshot(reloaded)
report['usb_exact_after_reload']=all(saved['tracks'][uid]==value for uid,value in before['tracks'].items())
report['pads_exact_after_reload']=saved['footprints']==before['footprints']
report['two_layers']=reloaded.GetCopperLayerCount()==2
(out/'report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
