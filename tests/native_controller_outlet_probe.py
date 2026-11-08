"""A valid short stub is not necessarily a usable outward controller exit."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.controller_ports import route_controller_ports
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.bank_geometry import port_outside_bounds

def fixture():
 b=p.BOARD();b.SetCopperLayerCount(2);apply_native_rules(b,COMPACT_VIAS)
 for name in ('signal','barrier'):b.Add(p.NETINFO_ITEM(b,name))
 pts=[(0,0),(30000000,0),(30000000,30000000),(0,30000000)]
 for a,z in zip(pts,pts[1:]+pts[:1]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
 f=p.FOOTPRINT(b);f.SetReference('U_MCU');f.SetPosition(p.VECTOR2I(15000000,15000000));f.SetLayer(p.B_Cu);b.Add(f)
 d=p.PAD(f);d.SetNumber('1');d.SetNet(b.FindNet('signal'));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(300000,700000));d.SetPosition(p.VECTOR2I(15000000,14000000));ls=p.LSET();ls.AddLayer(p.B_Cu);d.SetLayerSet(ls);f.Add(d)
 t=p.PCB_TRACK(b);t.SetNet(b.FindNet('barrier'));t.SetLayer(p.B_Cu);t.SetWidth(500000);t.SetStart(p.VECTOR2I(300000,12000000));t.SetEnd(p.VECTOR2I(29700000,12000000));b.Add(t)
 return b
frame=(12000000,11000000,18000000,18000000);out={}
for strict in (False,True):
 b=fixture();before=snapshot(b)
 kwargs={'exit_bounds_nm':{'U_MCU':frame}} if strict else {}
 with generation_profile('dsn-safe-v1'):
  r=route_controller_ports(b,controller_refs={'U_MCU'},mcu_refs={'U_MCU'},include_nets={'signal'},access_mode='prefer_same_layer',continuation_nm=500000,deadline=time.monotonic()+10,**kwargs)
 ports=r['ports'];live=snapshot(b);closed=False;outside=False
 if r['accepted']:
  q=ports[0];a=q['heading_origin_nm'];z=q['exit_nm'];h=tuple((z[i]>a[i])-(z[i]<a[i]) for i in (0,1));outside=port_outside_bounds(z,h,frame,150000)
  graph=copper_graph(b,{'signal'},time.monotonic()+2);groups={graph['find'](i) for i,n in enumerate(graph['nodes'])};closed=len(groups)==1
 out[str(strict)]={'accepted':r['accepted'],'outside':outside,'closed':closed,'kinds':[q['kind'] for q in ports],'source_exact':live['footprints']==before['footprints'] and all(live['tracks'].get(u)==t for u,t in before['tracks'].items()),'report':r}
from smilepcb.keyboard_routing.ordered_controller import probe_gpio_access
cfg={'parts':[{'ref':'U_MCU','region':'controller','nets':{'1':'signal'}}],
     'mcu_gpio_pads':{'A0':'1'},'layout':{'gpio':{'rows':['A0'],'cols':[]},'rgb':False},'plane_nets':[]}
out['preflight']={}
for wall in (False,True):
 b=fixture()
 if not wall:
  for t in list(b.GetTracks()):b.Delete(t)
 before=snapshot(b)
 with generation_profile('dsn-safe-v1'):
  r=probe_gpio_access(b,cfg,deadline=time.monotonic()+5,exit_bounds_nm=frame)
 out['preflight'][str(wall)]={'report':r,'source_exact':snapshot(b)==before}
Path(sys.argv[1]).write_text(json.dumps(out,indent=2)+'\n')
