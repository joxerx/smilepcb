"""Real KiCad regression: only explicitly deferred movable fill is omitted."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.routing_style import filled_zones
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
b=p.BOARD();b.SetCopperLayerCount(2);rules=b.GetDesignSettings();rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000
for name in ('a','earth'):b.Add(p.NETINFO_ITEM(b,name))
for a,z in zip([(0,0),(20000000,0),(20000000,15000000),(0,15000000)],[(20000000,0),(20000000,15000000),(0,15000000),(0,0)]):
 e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetStart(vec(a));e.SetEnd(vec(z));e.SetWidth(50000);b.Add(e)
f=p.FOOTPRINT(b);f.SetReference('ground-terminal');b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(vec((2000000,2000000)));d.SetSize(vec((1000000,1000000)));d.SetShape(p.PAD_SHAPE_RECT);d.SetAttribute(p.PAD_ATTRIB_SMD);layers=p.LSET();layers.AddLayer(p.F_Cu);d.SetLayerSet(layers);d.SetNet(b.FindNet('earth'));f.Add(d)
z=p.ZONE(b);z.SetLayer(p.F_Cu);z.SetNet(b.FindNet('earth'));z.SetLocalClearance(150000);z.SetMinThickness(150000);z.SetPadConnection(p.ZONE_CONNECTION_FULL);poly=z.Outline();poly.NewOutline()
for a in [(1000000,1000000),(19000000,1000000),(19000000,14000000),(1000000,14000000)]:poly.Append(*a)
b.Add(z);assert p.ZONE_FILLER(b).Fill(b.Zones());uid=z.m_Uuid.AsString();before=snapshot(b)
shape=p.SHAPE_SEGMENT(vec((5000000,8000000)),vec((15000000,8000000)),150000)
def world(**kwargs):return LocalRouter(b,compact=True,track_width_nm=150000,deadline=time.monotonic()+5,**kwargs)
result={'default_blocked':not world().clear(shape,p.F_Cu,'a')}
try:
 deferred=world(deferred_zone_ids={uid});result['deferred_clear']=deferred.clear(shape,p.F_Cu,'a')
 t=p.PCB_TRACK(b);t.SetNet(b.FindNet('earth'));t.SetWidth(150000);t.SetLayer(p.F_Cu);t.SetStart(vec((8000000,8100000)));t.SetEnd(vec((12000000,8100000)));b.Add(t)
 result['real_track_still_blocks']=not world(deferred_zone_ids={uid}).clear(shape,p.F_Cu,'a');b.Delete(t)
 def rejected(values):
  try:world(deferred_zone_ids=values)
  except ValueError:return True
  return False
 result['unknown_rejected']=rejected({'00000000-0000-0000-0000-000000000001'})
 z.SetLocked(True);result['locked_rejected']=rejected({uid});z.SetLocked(False)
 keep=p.ZONE(b);keep.SetIsRuleArea(True);keep.SetLayer(p.F_Cu);keep.SetDoNotAllowTracks(True);poly=keep.Outline();poly.NewOutline()
 for a in [(6000000,7000000),(14000000,7000000),(14000000,9000000),(6000000,9000000)]:poly.Append(*a)
 b.Add(keep);result['keepout_rejected']=rejected({keep.m_Uuid.AsString()});result['keepout_still_blocks']=not world(deferred_zone_ids={uid}).clear(shape,p.F_Cu,'a');b.Delete(keep)
 result['source_copper_and_components_exact']=snapshot(b)==before
except TypeError as error:result['missing_api']=str(error)
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
