"""Real native routing: retained attempt history and an avoidable foreign cut."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.completion import repair_candidate
from smilepcb.routing_quality import using_quality_profile
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
def point(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
def board():
 b=p.BOARD();r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000;r.m_CopperEdgeClearance=200000;r.m_MinThroughDrill=200000;r.m_ViasMinSize=500000;r.m_ViasMinAnnularWidth=150000;r.m_HoleClearance=250000
 r.m_NetSettings.GetDefaultNetclass().SetClearance(150000)
 for a,z in (((0,0),(30,0)),((30,0),(30,30)),((30,30),(0,30)),((0,30),(0,0))):
  t=p.PCB_SHAPE(b);t.SetShape(p.SHAPE_T_SEGMENT);t.SetStart(point(*a));t.SetEnd(point(*z));t.SetLayer(p.Edge_Cuts);t.SetWidth(50000);b.Add(t)
 return b
def pad(b,ref,net,x,y):
 if b.FindNet(net) is None:b.Add(p.NETINFO_ITEM(b,net))
 f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(point(.5,.5));d.SetPosition(point(x,y));d.SetNet(b.FindNet(net));layers=p.LSET();layers.AddLayer(p.F_Cu);d.SetLayerSet(layers);f.Add(d)
b=board()
for i,net in enumerate(('A','B','C')):
 pad(b,net+'1',net,3,3+i*5);pad(b,net+'2',net,15,3+i*5)
with using_quality_profile('turns'):
 r=repair_candidate(b,'signal_negotiate',protected=set(),plane_nets=set(),request={'attempt_counts':{'C':2}},deadline=time.monotonic()+82)
plan=r['signal_negotiation'];fair={'order':[a['net'] for a in plan['diagnostic']['attempts']],'counts':r['graph'].get('attempt_counts'),'after':r['after']}
def weight_for(mode,*,exploratory=False):
 b=board();pad(b,'T1','TARGET',3,15);pad(b,'T2','TARGET',25,15);pad(b,'K1','KEEP',14,11);pad(b,'K2','KEEP',14,19)
 t=p.PCB_TRACK(b);t.SetStart(point(14,11));t.SetEnd(point(14,19));t.SetWidth(150000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet('KEEP'));b.Add(t)
 with using_quality_profile(mode):
  r=repair_candidate(b,'signal_negotiate',protected=set(),plane_nets=set(),request={},deadline=time.monotonic()+82,**({'exploratory':True} if exploratory else {}))
 plan=r['signal_negotiation'];return {'removed':len(plan['removed']),'after':r['after'],'connected':plan['connected']}
weight=weight_for('turns');weight_legacy=weight_for('legacy')
try:
 weight_exploratory=weight_for('turns',exploratory=True);weight_exploratory['supported']=True
except TypeError as error:
 weight_exploratory={'supported':False,'error':str(error)}
try:
 repair_candidate(board(),'signal_negotiate',protected=set(),plane_nets=set(),request={},deadline=time.monotonic()+82,exploratory=1)
 invalid_mode_rejected=False
except ValueError:invalid_mode_rejected=True
except TypeError:invalid_mode_rejected=False
report={'fair':fair,'weight':weight,'weight_legacy':weight_legacy,'weight_exploratory':weight_exploratory,'invalid_mode_rejected':invalid_mode_rejected};(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
