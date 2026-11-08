"""Explicit return access can bend; a truly closed barrier must still reject."""
import json,sys,time
from unittest.mock import patch
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing import power
from smilepcb.keyboard_routing.power import seed_controller_returns,seed_plane_access
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.routing_quality import using_quality_profile
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);report={}
for label,top in (('bend',5050000),('blocked',5650000),('length_bound',5050000),('straight_priority',4950000)):
 b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000;r.m_CopperEdgeClearance=200000;r.m_MinThroughDrill=200000;r.m_ViasMinSize=500000;r.m_ViasMinAnnularWidth=150000
 for n in ('return','obstacle'):b.Add(p.NETINFO_ITEM(b,n))
 bottom=6400000 if label=='straight_priority' else 6000000
 for a,z in (((4500000,4000000),(7200000,4000000)),((7200000,4000000),(7200000,bottom)),((7200000,bottom),(4500000,bottom)),((4500000,bottom),(4500000,4000000))):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(a));e.SetEnd(vec(z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
 f=p.FOOTPRINT(b);f.SetReference('U_RETURN');f.SetLayer(p.B_Cu);b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(vec((5000000,5000000)));d.SetSize(vec((300000,300000)));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.B_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet('return'));f.Add(d)
 wall_x=5450000 if label=='straight_priority' else 5650000
 wall=p.PCB_TRACK(b);wall.SetStart(vec((wall_x,4400000)));wall.SetEnd(vec((wall_x,top)));wall.SetWidth(150000);wall.SetLayer(p.B_Cu);wall.SetNet(b.FindNet('obstacle'));wall.SetLocked(True);b.Add(wall);before=snapshot(b)
 with generation_profile('dsn-safe-v1'),using_quality_profile('turns'):
  if label=='length_bound':
   rows=seed_plane_access(b,[d],deadline=time.monotonic()+8,track_width_nm=150000,max_distance_nm=1600000,via_to_smd_clearance_nm=200000);result={'accepted':bool(rows) and rows[0]['connected']}
  else:result=seed_controller_returns(b,controller_refs={'U_RETURN'},plane_nets={'return'},deadline=time.monotonic()+8,via_to_smd_clearance_nm=200000)
  after=snapshot(b);added=set(after['tracks'])-set(before['tracks']);world=LocalRouter(b,compact=True,ignored_copper=added,track_width_nm=150000)
  native_clear=all(world.via_clear((t.GetPosition().x,t.GetPosition().y),t.GetNetname()) if isinstance(t,p.PCB_VIA) else all(world.clear(t.GetEffectiveShape(layer),layer,t.GetNetname()) for layer in (p.F_Cu,p.B_Cu) if t.IsOnLayer(layer)) for t in b.GetTracks() if t.m_Uuid.AsString() in added)
  g=copper_graph(b,{'return'},include_zones=False);pad_root=next(g['find'](i) for i,n in enumerate(g['nodes']) if isinstance(n['item'],p.PAD));vias=[i for i,n in enumerate(g['nodes']) if isinstance(n['item'],p.PCB_VIA)]
 report[label]={'accepted':result['accepted'],'bent':sum(t.GetClass()=='PCB_TRACK' for t in b.GetTracks() if t.m_Uuid.AsString() in added)>1,'track_count':sum(t.GetClass()=='PCB_TRACK' for t in b.GetTracks() if t.m_Uuid.AsString() in added),'pad_reaches_real_via':bool(vias) and all(g['find'](i)==pad_root for i in vias),'native_clear':native_clear,'source_exact':after['footprints']==before['footprints'] and all(after['tracks'].get(u)==s for u,s in before['tracks'].items()),'rollback_exact':after==before,'reason':result.get('reason')}
for label,top in (('grid_join',4950000),('grid_blocked',5450000),('grid_wide_annulus',4950000)):
 b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000;r.m_CopperEdgeClearance=200000;r.m_MinThroughDrill=200000;r.m_ViasMinSize=500000;r.m_ViasMinAnnularWidth=150000
 for n in ('return','obstacle'):b.Add(p.NETINFO_ITEM(b,n))
 for a,z in (((4500000,4200000),(7200000,4200000)),((7200000,4200000),(7200000,5800000)),((7200000,5800000),(4500000,5800000)),((4500000,5800000),(4500000,4200000))):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(a));e.SetEnd(vec(z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
 f=p.FOOTPRINT(b);f.SetReference('U_RETURN');f.SetLayer(p.B_Cu);b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(vec((5000000,5000000)));d.SetSize(vec((300000,300000)));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.B_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet('return'));f.Add(d)
 wall=p.PCB_TRACK(b);wall.SetStart(vec((5650000,4600000)));wall.SetEnd(vec((5650000,top)));wall.SetWidth(150000);wall.SetLayer(p.B_Cu);wall.SetNet(b.FindNet('obstacle'));wall.SetLocked(True);b.Add(wall)
 via=p.PCB_VIA(b);via.SetPosition(vec((6200000,5000000)));via.SetWidth(500000);via.SetDrill(200000);via.SetLayerPair(p.F_Cu,p.B_Cu);via.SetNet(b.FindNet('return'));via.SetLocked(True);b.Add(via)
 if label=='grid_wide_annulus':
  via.Padstack().SetMode(p.PADSTACK.MODE_FRONT_INNER_BACK);via.SetWidth(p.F_Cu,500000);via.SetWidth(p.B_Cu,900000)
  assert via.GetWidth(p.F_Cu)==500000 and via.GetWidth(p.B_Cu)==900000
  f2=p.FOOTPRINT(b);f2.SetReference('U_LAND');f2.SetLayer(p.B_Cu);b.Add(f2);d2=p.PAD(f2);d2.SetNumber('1');d2.SetPosition(vec((6650000,5500000)));d2.SetSize(vec((100000,100000)));d2.SetAttribute(p.PAD_ATTRIB_SMD);d2.SetShape(p.PAD_SHAPE_RECT);d2.SetLayerSet(ls);d2.SetNet(b.FindNet('obstacle'));f2.Add(d2)
 before=snapshot(b)
 with generation_profile('dsn-safe-v1'),using_quality_profile('turns'):
  routers=[]
  def observe_router(*args,**kwargs):
   router=LocalRouter(*args,**kwargs);routers.append(router);return router
  with patch.object(power,'LocalRouter',observe_router):
   result=seed_controller_returns(b,controller_refs={'U_RETURN'},plane_nets={'return'},deadline=time.monotonic()+8,via_to_smd_clearance_nm=200000)
  after=snapshot(b);g=copper_graph(b,{'return'},include_zones=False);ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(g['nodes'])};actual=g['find'](ids[d.m_Uuid.AsString()])==g['find'](ids[via.m_Uuid.AsString()]);added=set(after['tracks'])-set(before['tracks']);world=LocalRouter(b,compact=True,ignored_copper=added,track_width_nm=150000)
  native_clear=all(world.via_clear((t.GetPosition().x,t.GetPosition().y),t.GetNetname()) if isinstance(t,p.PCB_VIA) else all(world.clear(t.GetEffectiveShape(layer),layer,t.GetNetname()) for layer in (p.F_Cu,p.B_Cu) if t.IsOnLayer(layer)) for t in b.GetTracks() if t.m_Uuid.AsString() in added)
  # A candidate overlapping each newly committed fallback segment must see
  # the exact foreign-copper blocker in the oracle used by subsequent islands.
  fallback_indexed=bool(added) and all(
   t.m_Uuid.AsString() in routers[0].collisions(t.GetEffectiveShape(t.GetLayer()),t.GetLayer(),'obstacle')
   for t in b.GetTracks() if t.m_Uuid.AsString() in added)
 report[label]={'accepted':result['accepted'],'pad_reaches_existing_via':actual,'no_extra_via':sum(isinstance(t,p.PCB_VIA) for t in b.GetTracks())==1,'native_clear':native_clear,'source_exact':after['footprints']==before['footprints'] and all(after['tracks'].get(u)==state for u,state in before['tracks'].items()),'rollback_exact':after==before,'new_tracks':len(added),'fallback_indexed':fallback_indexed,'reason':result.get('reason')}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
