"""Small source-face-aware USB join and independent saved-board inspection."""
import sys,json,time,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_native import validate,pad_gaps
from smilepcb.keyboard_routing.native_graph import copper_graph,Endpoint
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.completion import component_frontiers
from smilepcb.keyboard_routing.safety import require_via_land_clearance
from smilepcb.routing_style import Clearance,octile_paths,add_path
from smilepcb.keyboard_routing.generation import select_generation_profile
request=json.load(sys.stdin);select_generation_profile(request.get('generation_profile','native'));path=Path(request['board']);b=p.LoadBoard(str(path));validate(b,request);fps={f.GetReference():f for f in b.GetFootprints()};before=snapshot(b)
layer=fps['U_MCU'].GetLayer()
if request['operation']=='usb':
 from smilepcb.usb_pair import try_joint_pair
 report=try_joint_pair(b,('USB_DP','USB_DM'),source_ref='U_MCU',target_ref='J_UDB',
                       layer=layer,deadline=time.monotonic()+20)
 if report is None:
  oracle=Clearance(b);report={}
  for name in ('USB_DP','USB_DM'):
   graph=copper_graph(b,{name},time.monotonic()+10)
   component=next(rows for rows in graph['groups'].values() if any(isinstance(graph['nodes'][i]['item'],p.PAD) and graph['nodes'][i]['item'].GetParentFootprint().GetReference()=='U_MCU' for i in rows))
   starts=component_frontiers(graph,component,time.monotonic()+5);target=next(d for d in fps['J_UDB'].Pads() if d.GetNetname()==name);end=(target.GetPosition().x,target.GetPosition().y)
   choices=[]
   for start in starts:
    for route in octile_paths(start,end):
     if all(oracle.clear(a,z,200000,layer,name) for a,z in zip(route,route[1:])):choices.append((sum(math.dist(a,z) for a,z in zip(route,route[1:])),route))
   if choices:
    _,route=min(choices);ids=add_path(b,oracle,route,layer,name,200000);report[name]={'points_nm':route,'added_ids':ids,'method':'checked octile'}
   else:
    router=LocalRouter(b,deadline=time.monotonic()+15,compact=True);router.grid_step=125000;router.connection_budget_s=5;router.search_budget_s=5;router.heuristic_weight=3
    router.via_clear=lambda *args:False
    connected=False
    for start in sorted(starts,key=lambda q:math.dist(q,end)):
     if router.connect(Endpoint(start,layer,name),target):connected=True;break
    if not connected:raise ValueError('Existing router found no single-layer USB join for '+name)
    report[name]={'added_ids':router.added,'method':'existing single-layer grid search'}
   oracle=Clearance(b)
 from smilepcb.usb_pair import balance_pair
 from smilepcb.keyboard_native import verify_usb_pair
 balance=balance_pair(b,('USB_DP','USB_DM'),editable_ids=[uid for item in report.values() for uid in item['added_ids']])
 lengths=verify_usb_pair(b)
 if balance['changed']:
  name=balance['net'];report[name]['added_ids']=[uid for uid in report[name]['added_ids'] if uid not in balance['removed_ids']]+balance['added_ids']
 after=snapshot(b)
 assert after['footprints']==before['footprints']
 assert all(after['tracks'].get(uid)==shape for uid,shape in before['tracks'].items())
 p.SaveBoard(str(path),b);print(json.dumps({'usb':report,'layer':b.GetLayerName(layer),'original_copper_unchanged':True,'balance':balance,'verified_lengths_mm':lengths}))
elif request['operation'] in ('escape','ports'):
 from smilepcb.keyboard_routing.power import seed_plane_access
 cfg=json.loads((path.parent/'.smilepcb-keyboard.json').read_text())
 gpio=cfg['layout']['gpio'];pin_names=list(gpio['rows'])+list(gpio['cols'])
 if cfg['layout'].get('rgb'):pin_names += [gpio[k] for k in ('rgb_data','rgb_enable','rgb_output_enable')]
 numbers={cfg['mcu_gpio_pads'][pin] for pin in pin_names}
 pads=[d for d in fps['U_MCU'].Pads() if d.GetNumber() in numbers]
 graph=copper_graph(b,{d.GetNetname() for d in pads},time.monotonic()+10)
 by_id={node['item'].m_Uuid.AsString():i for i,node in enumerate(graph['nodes'])}
 existing=[];missing=[]
 for d in pads:
  group=graph['groups'][graph['find'](by_id[d.m_Uuid.AsString()])]
  vias=[graph['nodes'][i]['item'] for i in group if isinstance(graph['nodes'][i]['item'],p.PCB_VIA)]
  if vias:existing.append({'pin':d.GetNumber(),'net':d.GetNetname(),'via_ids':[v.m_Uuid.AsString() for v in vias]})
  else:missing.append(d)
 access=seed_plane_access(b,missing,deadline=time.monotonic()+30) if request['operation']=='escape' else []
 after=snapshot(b)
 assert after['footprints']==before['footprints']
 assert all(after['tracks'].get(uid)==shape for uid,shape in before['tracks'].items())
 require_via_land_clearance(b)
 if request['operation']=='escape':p.SaveBoard(str(path),b)
 print(json.dumps({'required':len(pads),'existing_two_layer':existing,'missing_two_layer':[{'pin':d.GetNumber(),'net':d.GetNetname()} for d in missing],
                   'added_access':access,'original_copper_unchanged':True}))
else:
 require_via_land_clearance(b)
 gaps=pad_gaps(b)
 lengths={name:sum(t.GetLength() for t in b.GetTracks() if t.GetNetname()==name)/1e6 for name in ('USB_DP','USB_DM')}
 usb_layers={b.GetLayerName(t.GetLayer()) for t in b.GetTracks() if t.GetNetname() in lengths};usb_vias=sum(isinstance(t,p.PCB_VIA) for t in b.GetTracks() if t.GetNetname() in lengths)
 cfg=json.loads((path.parent/'.smilepcb-keyboard.json').read_text());pins=True
 for part in cfg['parts']:
  if part['ref'].startswith('#'):continue
  for pad in fps[part['ref']].Pads():
   if pad.GetNumber() and part['nets'][pad.GetNumber()]!=pad.GetNetname():pins=False
 print(json.dumps({'pad_group_gaps':sum(gaps.values()),'gaps_by_net':gaps,'pin_nets_match':pins,
 'copper_layers':b.GetCopperLayerCount(),'usb_lengths_mm':lengths,'usb_layers':sorted(usb_layers),'usb_vias':usb_vias,
 'usb_recipe_pass':usb_layers=={b.GetLayerName(layer)} and usb_vias==0 and all(0<v<20 for v in lengths.values()) and abs(lengths['USB_DP']-lengths['USB_DM'])<.5}))
