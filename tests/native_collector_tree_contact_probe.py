"""The electrical anchor may sit behind a useful same-face field contact."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.collectors import route_collectors
from smilepcb.keyboard_routing.native_graph import copper_graph
b=p.BOARD();b.SetCopperLayerCount(2);apply_native_rules(b,COMPACT_VIAS)
def v(x,y):return p.VECTOR2I(round(x*1e6),round(y*1e6))
pts=[(0,0),(30,0),(30,45),(0,45)]
for a,z in zip(pts,pts[1:]+pts[:1]):
 e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(v(*a));e.SetEnd(v(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
def pad(f,num,net,pos,layer):
 d=p.PAD(f);d.SetNumber(num);d.SetNet(b.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(v(.3,.7));d.SetPosition(v(*pos));ls=p.LSET();ls.AddLayer(layer);d.SetLayerSet(ls);f.Add(d);return d

def trace(net,points,layer):
 ids=[]
 for a,z in zip(points,points[1:]):
  t=p.PCB_TRACK(b);t.SetStart(v(*a));t.SetEnd(v(*z));t.SetWidth(150000);t.SetNet(b.FindNet(net));t.SetLayer(layer);b.Add(t);ids.append(t.m_Uuid.AsString())
 return ids
chip=p.FOOTPRINT(b);chip.SetReference('U_MCU');chip.SetPosition(v(10.5,22));b.Add(chip);ports=[];field=[]
for index,(name,x,tree,viaxy,path,anchorxy) in enumerate([
 ('west',10,[(1,4),(5,8)],(1,4),[(1,4),(1,20.5),(10.5,30)],(10.5,30)),
 ('east',11,[(15,10),(25,20)],(25,20),[(25,20),(25,25.5),(10.5,40)],(10.5,40))]):
 b.Add(p.NETINFO_ITEM(b,name));d=pad(chip,str(index+1),name,(x,21),p.B_Cu);source=trace(name,[(x,21),(x,20)],p.B_Cu)
 ports.append({'net':name,'kind':'same_layer','pad_ids':[d.m_Uuid.AsString()],'added_ids':source,'terminal_id':source[-1],'via_id':None,'via_nm':None,'exit_nm':[round(x*1e6),20000000],'heading_origin_nm':[round(x*1e6),21000000],'exit_layer':p.B_Cu,'collector_heading':[0,-1]})
 f=p.FOOTPRINT(b);f.SetReference('FIELD'+str(index));b.Add(f);pad(f,'1',name,anchorxy,p.F_Cu)
 field+=trace(name,tree,p.B_Cu)+trace(name,path,p.F_Cu)
 via=p.PCB_VIA(b);via.SetPosition(v(*viaxy));via.SetWidth(500000);via.SetDrill(200000);via.SetLayerPair(p.F_Cu,p.B_Cu);via.SetNet(b.FindNet(name));b.Add(via);field.append(via.m_Uuid.AsString())
cfg={'parts':[{'ref':'U_MCU','region':'controller','nets':{'1':'west','2':'east'}}],'layout':{'gpio':{'rows':[],'cols':['A0','A1']}},'mcu_gpio_pads':{'A0':'1','A1':'2'}}
before=snapshot(b)
with generation_profile('dsn-safe-v1'):baseline=route_collectors(b,cfg,ports,deadline=time.monotonic()+5)
assert not baseline['accepted'] and snapshot(b)==before,baseline
started=time.monotonic()
with generation_profile('dsn-safe-v1'):result=route_collectors(b,cfg,ports,deadline=started+20,field_owned_ids=field)
live=snapshot(b);graph=copper_graph(b,{'west','east'},time.monotonic()+5);roots={net:{graph['find'](i) for i,n in enumerate(graph['nodes']) if isinstance(n['item'],p.PAD) and n['net']==net} for net in ('west','east')}
newvias=[u for u,t in live['tracks'].items() if u not in before['tracks'] and t['class']=='PCB_VIA']
record={'pad_only_accepted':baseline['accepted'],'result':result,'all_pads_connected':all(len(rs)==1 for rs in roots.values()),'new_vias':newvias,'source_exact':live['footprints']==before['footprints'] and all(live['tracks'].get(u)==t for u,t in before['tracks'].items()),'elapsed_s':time.monotonic()-started}
Path(sys.argv[1]).write_text(json.dumps(record,indent=2)+'\n')
