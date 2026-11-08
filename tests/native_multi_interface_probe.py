"""All physical interfaces of one net retain independent contact boundaries."""
import json,sys
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.port_policy import PortPolicy
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.local_router import vec
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);b=p.BOARD();b.GetDesignSettings().m_MinClearance=150000;b.GetDesignSettings().m_TrackMinWidth=150000;b.Add(p.NETINFO_ITEM(b,'shared_supply'));f=p.FOOTPRINT(b);f.SetReference('U_MCU');b.Add(f);ports=[]
for i,y in enumerate((10000000,12000000),1):
 d=p.PAD(f);d.SetNumber(str(i));d.SetPosition(vec((15000000,y)));d.SetSize(vec((1000000,300000)));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet('shared_supply'));f.Add(d);t=p.PCB_TRACK(b);t.SetStart(vec((15000000,y)));t.SetEnd(vec((20000000,y)));t.SetWidth(150000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet('shared_supply'));b.Add(t);ports.append({'net':'shared_supply','pad_ids':[d.m_Uuid.AsString()],'added_ids':[t.m_Uuid.AsString()],'terminal_id':t.m_Uuid.AsString(),'kind':'same_layer','exit_nm':(20000000,y),'heading_origin_nm':(15000000,y),'exit_layer':p.F_Cu})
policy=PortPolicy(b,ports);before=snapshot(b);report={'first_pad_side_rejected':not policy.accepts_segment((15000000,10000000),(14000000,10000000),p.F_Cu,'shared_supply'), 'second_pad_side_rejected':not policy.accepts_segment((15000000,12000000),(14000000,12000000),p.F_Cu,'shared_supply'), 'first_internal_via_rejected':not policy.accepts_via((17000000,10000000),'shared_supply',500000)}
path=[(20000000,10000000),(21000000,10000000),(21000000,12000000),(20000000,12000000)];report['outward_join_allowed']=all(policy.accepts_segment(a,z,p.F_Cu,'shared_supply',150000) for a,z in zip(path,path[1:]))
if report['outward_join_allowed']:
 for a,z in zip(path,path[1:]):
  t=p.PCB_TRACK(b);t.SetStart(vec(a));t.SetEnd(vec(z));t.SetWidth(150000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet('shared_supply'));b.Add(t)
 g=copper_graph(b,{'shared_supply'});report['real_pad_group_joined']=len(g['groups'])==1
else:report['real_pad_group_joined']=False
after=snapshot(b);report['source_exact']=all(after['tracks'].get(u)==s for u,s in before['tracks'].items()) and after['footprints']==before['footprints'];(out/'report.json').write_text(json.dumps(report,indent=2))

# Identical XY on disjoint copper faces must not veto an outward departure.
b2=p.BOARD();b2.GetDesignSettings().m_MinClearance=150000;b2.GetDesignSettings().m_TrackMinWidth=150000;b2.Add(p.NETINFO_ITEM(b2,'shared_supply'));opposite=[]
for i,face in enumerate((p.F_Cu,p.B_Cu),1):
 f=p.FOOTPRINT(b2);f.SetReference('U'+str(i));f.SetLayer(face);b2.Add(f)
 d=p.PAD(f);d.SetNumber('1');d.SetPosition(vec((15000000,10000000)));d.SetSize(vec((1000000,300000)));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls);d.SetNet(b2.FindNet('shared_supply'));f.Add(d)
 t=p.PCB_TRACK(b2);t.SetStart(vec((15000000,10000000)));t.SetEnd(vec((20000000,10000000)));t.SetWidth(150000);t.SetLayer(face);t.SetNet(b2.FindNet('shared_supply'));b2.Add(t)
 opposite.append({'net':'shared_supply','pad_ids':[d.m_Uuid.AsString()],'added_ids':[t.m_Uuid.AsString()],'terminal_id':t.m_Uuid.AsString(),'kind':'same_layer','exit_nm':(20000000,10000000),'heading_origin_nm':(15000000,10000000),'exit_layer':face})
policy2=PortPolicy(b2,opposite);before2=snapshot(b2);graph2=copper_graph(b2,{'shared_supply'})
report['coincident_exits_are_physically_independent']=len(graph2['groups'])==2
for label,face in (('front',p.F_Cu),('back',p.B_Cu)):
 report['coincident_'+label+'_outward_allowed']=policy2.accepts_segment((20000000,10000000),(21000000,10000000),face,'shared_supply')
 report['coincident_'+label+'_inward_rejected']=not policy2.accepts_segment((20000000,10000000),(19000000,10000000),face,'shared_supply')
 report['coincident_'+label+'_pad_side_rejected']=not policy2.accepts_segment((15000000,10000000),(14000000,10000000),face,'shared_supply')
report['coincident_source_exact']=snapshot(b2)==before2
(out/'report.json').write_text(json.dumps(report,indent=2))
