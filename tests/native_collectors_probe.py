"""Real native geometry regressions; no saved PCB or private hardware required."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.collector_capacity import Search,groups_for
from smilepcb.keyboard_routing.collector_peel import compress,pocket_offset
from smilepcb.keyboard_routing.collectors import route_collectors
select_generation_profile('dsn-safe-v1')
def fixture(wall=False):
 b=p.BOARD();r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000;r.m_MinThroughDrill=200000;r.m_ViasMinAnnularWidth=150000;r.m_ViasMinSize=500000;r.m_CopperEdgeClearance=200000
 corners=[(0,0),(30000000,0),(30000000,30000000),(0,30000000)]
 for a,z in zip(corners,corners[1:]+corners[:1]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
 f=p.FOOTPRINT(b);f.SetReference('U_MCU');f.SetPosition(p.VECTOR2I(10500000,7000000));b.Add(f);ports=[]
 for i in range(3):
  name='S'+str(i);net=p.NETINFO_ITEM(b,name);b.Add(net);x=10000000+i*500000
  d=p.PAD(f);d.SetNumber(str(i+1));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(300000,700000));ls=p.LSET();ls.AddLayer(p.B_Cu);d.SetLayerSet(ls);d.SetPosition(p.VECTOR2I(x,9000000));d.SetNet(net);f.Add(d)
  t=p.PCB_TRACK(b);t.SetStart(p.VECTOR2I(x,9000000));t.SetEnd(p.VECTOR2I(x,10000000));t.SetLayer(p.B_Cu);t.SetWidth(150000);t.SetNet(net);b.Add(t)
  ports.append({'net':name,'kind':'same_layer','pad_ids':[d.m_Uuid.AsString()],'added_ids':[t.m_Uuid.AsString()],'terminal_id':t.m_Uuid.AsString(),'via_id':None,'via_nm':None,'exit_nm':[x,10000000],'heading_origin_nm':[x,9000000],'exit_layer':p.B_Cu})
 if wall:
  net=p.NETINFO_ITEM(b,'OBSTACLE');b.Add(net);t=p.PCB_TRACK(b);t.SetStart(p.VECTOR2I(100000,13000000));t.SetEnd(p.VECTOR2I(29900000,13000000));t.SetLayer(p.B_Cu);t.SetWidth(500000);t.SetNet(net);b.Add(t)
 return b,ports

def with_targets(smd=False):
    board,ports=fixture()
    part={'ref':'U_MCU','region':'controller','nets':{str(i+1):q['net'] for i,q in enumerate(ports)}}
    cfg={'parts':[part],'layout':{'gpio':{'rows':['A0'],'cols':['A1','A2']}},'mcu_gpio_pads':{'A0':'1','A1':'2','A2':'3'}}
    for i,q in enumerate(ports):
        f=p.FOOTPRINT(board);f.SetReference('X'+str(i));board.Add(f)
        d=p.PAD(f);d.SetNumber('1');d.SetShape(p.PAD_SHAPE_CIRCLE);d.SetSize(p.VECTOR2I(900000,900000));d.SetPosition(p.VECTOR2I(25000000-i*2000000,24000000-i*2000000));d.SetNet(board.FindNet(q['net']))
        if smd:
            d.SetAttribute(p.PAD_ATTRIB_SMD);ls=p.LSET();ls.AddLayer(p.B_Cu);d.SetLayerSet(ls)
        else:
            d.SetAttribute(p.PAD_ATTRIB_PTH);d.SetDrillSize(p.VECTOR2I(300000,300000));d.SetLayerSet(p.LSET.AllCuMask())
        f.Add(d)
    return board,cfg,ports

records={}
b,ports=fixture();before=snapshot(b)
new_ports,r=compress(b,ports,[q['net'] for q in ports],deadline=time.monotonic()+4)
from smilepcb.keyboard_routing.port_policy import PortPolicy
policy=PortPolicy(b,new_ports,track_width_nm=150000)
records['compression']={'accepted':r['accepted'],'pitch_nm':r.get('pitch_nm'),'usable':all(policy.accepts_segment(tuple(q['exit_nm']),(q['exit_nm'][0],q['exit_nm'][1]+500000),q['exit_layer'],q['net'],150000) for q in new_ports),'source_exact':all(snapshot(b)['tracks'].get(u)==t for u,t in before['tracks'].items())}
assert pocket_offset(600000,200000,150000,350000,guard=0)==200000
for smd in (False,True):
    b,cfg,ports=with_targets(smd);before=snapshot(b)
    groups=groups_for(b,cfg,ports)
    r=route_collectors(b,cfg,ports,deadline=time.monotonic()+25)
    g=copper_graph(b,{q['net'] for q in ports},time.monotonic()+5)
    roots={q['net']:{g['find'](i) for i,n in enumerate(g['nodes']) if isinstance(n['item'],p.PAD) and n['net']==q['net']} for q in ports}
    records['smd' if smd else 'tht']={'result':r,'all_connected':all(len(v)==1 for v in roots.values()),'mixed_group_count':len(groups),'source_exact':all(snapshot(b)['tracks'].get(u)==t for u,t in before['tracks'].items())}
b,cfg,ports=with_targets()
for f in b.GetFootprints():
 if f.GetReference().startswith('X'):
  for d in f.Pads():d.SetPosition(p.VECTOR2I(d.GetPosition().x,4000000))
before=snapshot(b);r=route_collectors(b,cfg,ports,deadline=time.monotonic()+25)
g=copper_graph(b,{q['net'] for q in ports},time.monotonic()+5)
roots={q['net']:{g['find'](i) for i,n in enumerate(g['nodes']) if isinstance(n['item'],p.PAD) and n['net']==q['net']} for q in ports}
records['turned']={'result':r,'all_connected':all(len(v)==1 for v in roots.values()),'source_exact':all(snapshot(b)['tracks'].get(u)==t for u,t in before['tracks'].items())}
b,cfg,ports=with_targets()
for f in b.GetFootprints():
 if f.GetReference().startswith('X'):
  i=int(f.GetReference()[1:])
  for d in f.Pads():d.SetPosition(p.VECTOR2I(4000000 if i==0 else d.GetPosition().x,4000000))
for i,q in enumerate(ports):q['collector_heading']=[-1,0] if i==0 else [1,0]
r=route_collectors(b,cfg,ports,deadline=time.monotonic()+25)
records['opposite-collectors']={'result':r}
b,cfg,ports=with_targets();net=p.NETINFO_ITEM(b,'BARRIER');b.Add(net)
t=p.PCB_TRACK(b);t.SetNet(net);t.SetWidth(500000);t.SetLayer(p.B_Cu);t.SetStart(p.VECTOR2I(500000,13000000));t.SetEnd(p.VECTOR2I(29500000,13000000));b.Add(t)
before=snapshot(b);r=route_collectors(b,cfg,ports,deadline=time.monotonic()+25)
g=copper_graph(b,{q['net'] for q in ports},time.monotonic()+5)
roots={q['net']:{g['find'](i) for i,n in enumerate(g['nodes']) if isinstance(n['item'],p.PAD) and n['net']==q['net']} for q in ports}
records['layer-wall']={'result':r,'all_connected':all(len(v)==1 for v in roots.values()),'source_exact':snapshot(b)['footprints']==before['footprints'] and all(snapshot(b)['tracks'].get(u)==t for u,t in before['tracks'].items()),'axis_portals':sum(e['kind']=='portal' for h in r['history'] for e in h.get('axis',[]))}
b,cfg,ports=with_targets();before=snapshot(b);r=route_collectors(b,cfg,ports,deadline=time.monotonic()-1)
records['expired']={'result':r,'unchanged':snapshot(b)==before}
Path(sys.argv[1]).write_text(json.dumps(records,indent=2)+'\n')
