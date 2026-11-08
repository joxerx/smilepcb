"""A sub-grid straight lane must beat needless layer bridges on real copper."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.field import build_field
from smilepcb.keyboard_routing.native_graph import copper_graph

def v(x,y):return p.VECTOR2I(round(x*1e6),round(y*1e6))
def make(gap=8.125,key_y=5):
 b=p.BOARD();rules=b.GetDesignSettings();rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000
 for name in ('row','a','b','anchor'):b.Add(p.NETINFO_ITEM(b,name))
 vertices=[(.0,.0),(20,0),(20,20),(0,20),(0,0)]
 for a,z in zip(vertices,vertices[1:]):
  g=p.PCB_SHAPE(b);g.SetShape(p.SHAPE_T_SEGMENT);g.SetStart(v(*a));g.SetEnd(v(*z));g.SetWidth(50000);g.SetLayer(p.Edge_Cuts);b.Add(g)
 for lo,hi in ((.5,gap-.3),(gap+.3,19.5)):
  g=p.PCB_SHAPE(b);g.SetShape(p.SHAPE_T_RECT);g.SetStart(v(7,lo));g.SetEnd(v(13,hi));g.SetWidth(50000);g.SetFilled(True);g.SetLayer(p.F_Cu);b.Add(g)
 for i,x in enumerate((5,15),1):
  for ref,y,net in [('SW'+str(i),key_y,('a','b')[i-1]),('D'+str(i),7,'row')]:
   f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f);f.SetPosition(v(x,y));d=p.PAD(f);d.SetNumber('1');d.SetNet(b.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_PTH);d.SetShape(p.PAD_SHAPE_CIRCLE);d.SetSize(v(1,1));d.SetDrillSize(v(.4,.4));d.SetLayerSet(p.LSET.AllCuMask());d.SetPosition(v(x,y));f.Add(d)
   if ref.startswith('SW'):
    d=p.PAD(f);d.SetNumber('1');d.SetNet(b.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(v(1,1));layers=p.LSET();layers.AddLayer(p.F_Cu);d.SetLayerSet(layers);d.SetPosition(v(x+.3,y));f.Add(d)
 t=p.PCB_TRACK(b);t.SetStart(v(18,2));t.SetEnd(v(18,4));t.SetWidth(150000);t.SetLayer(p.B_Cu);t.SetNet(b.FindNet('anchor'));b.Add(t)
 cfg={'layout':{'keys':[{'id':1,'matrix':[0,0]},{'id':2,'matrix':[0,1]}],'gpio':{'rows':['R'],'cols':['C0','C1']},'rgb':False},'parts':[{'ref':'U_MCU','region':'controller','nets':{'1':'row','2':'a','3':'b'}}],'mcu_gpio_pads':{'R':'1','C0':'2','C1':'3'},'plane_nets':[]}
 return b,cfg
b,cfg=make();before=snapshot(b);r=build_field(b,cfg,row_layer=p.F_Cu,column_layer=p.B_Cu,deadline=time.monotonic()+15);after=snapshot(b)
g=copper_graph(b,{'row','a','b'},time.monotonic()+5);required={}
for i,n in enumerate(g['nodes']):
 if isinstance(n['item'],p.PAD) and n['item'].GetNumber():required.setdefault(n['net'],set()).add(g['find'](i))
offset_board,offset_cfg=make(gap=17.125,key_y=16);offset_result=build_field(offset_board,offset_cfg,row_layer=p.F_Cu,column_layer=p.B_Cu,deadline=time.monotonic()+15)
board2,cfg2=make();old=snapshot(board2);expired=build_field(board2,cfg2,row_layer=p.F_Cu,column_layer=p.B_Cu,deadline=time.monotonic()-1)
report={'offset_diodes_do_not_hide_the_key_row':offset_result['complete'] and all(row.get('lane_y_nm',0)>16000000 and row['inline_vias']==0 for row in offset_result['rows']),'field_complete':all(len(v)==1 for v in required.values()) and set(required)=={'row','a','b'},'actual_pad_count':sum(1 for n in g['nodes'] if isinstance(n['item'],p.PAD) and n['item'].GetNumber()),'row_stays_on_preferred_face':all(t['layer']==p.F_Cu and t['class']!='PCB_VIA' for u,t in after['tracks'].items() if u not in before['tracks'] and t['net']=='row'),'source_preserved':before['footprints']==after['footprints'] and all(after['tracks'].get(u)==t for u,t in before['tracks'].items()),'expired_adds_nothing':snapshot(board2)==old,'result':r,'expired':expired}
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);p.SaveBoard(str(out/'field.kicad_pcb'),b);(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
