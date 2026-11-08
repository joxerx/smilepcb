import sys,json,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.visual_polish import _parts
from smilepcb.keyboard_routing.local_router import LocalRouter
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
ns={'__name__':'fixture','__file__':str(Path(__file__))};code=Path(__file__).with_name('native_joint_bundle_probe.py').read_text().split('result={}')[0].replace("('first','second','earth')","('first','second','earth','blocker')");exec(compile(code,'existing-native-joint-fixture','exec'),ns)
try:from smilepcb.keyboard_routing.bridge_bundles import refine_bridge_bundles
except ImportError:refine_bridge_bundles=None
result={}
for case in ('bridges','outside','partial-fault','quality-veto'):
 b,z=ns['board'](True)
 for zone in list(b.Zones()):b.Delete(zone)
 t=p.PCB_TRACK(b);t.SetNet(b.FindNet('blocker'));t.SetLayer(p.F_Cu);t.SetWidth(200000);t.SetStart(p.VECTOR2I(10000000,3000000));t.SetEnd(p.VECTOR2I(10000000,10000000));t.SetLocked(True);b.Add(t)
 # Exercise the same saved-board boundary as the actual application. Removing
 # a filled fixture zone leaves stale net-propagation state in this KiCad
 # binding; an independent refill characterization reproduced that state.
 p.SaveBoard(str(out/(case+'-input.kicad_pcb')),b);b=p.LoadBoard(str(out/(case+'-input.kicad_pcb')));b.GetDesignSettings().m_MinClearance=150000;b.GetDesignSettings().m_TrackMinWidth=150000
 before=snapshot(b);parts=_parts(b,time.monotonic()+5);original=LocalRouter.index
 if case=='partial-fault':
  def fail(self,item):
   if item.GetClass()=='PCB_TRACK' and item.m_Uuid.AsString() not in before['tracks']:raise RuntimeError('Injected joint partial Add')
   return original(self,item)
  LocalRouter.index=fail
 try:
  if refine_bridge_bundles is None:r={'missing_api':True}
  else:r=refine_bridge_bundles(b,nets={'first','second'},preferred_layer=p.F_Cu,protected_ids=(),deadline=time.monotonic()+30,focus_bounds=[0,0,1000000,1000000] if case=='outside' else [0,0,20000000,15000000],max_groups=8,free_corridors=True,**({'quality_gate':lambda before,after:False} if case=='quality-veto' else {}))
 except Exception as e:r={'fault':type(e).__name__+': '+str(e)}
 finally:LocalRouter.index=original
 after=snapshot(b);(out/(case+'-snapshots.json')).write_text(json.dumps({'before':before,'after':after},indent=2));result[case]={'report':r,'unchanged':before==after,'contacts_exact':_parts(b,time.monotonic()+5)==parts,'footprints_exact':before['footprints']==after['footprints'],'locked_exact':all(after['tracks'].get(u)==v for u,v in before['tracks'].items() if v['locked']),'vias':sum(t.GetClass()=='PCB_VIA' for t in b.GetTracks())}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
