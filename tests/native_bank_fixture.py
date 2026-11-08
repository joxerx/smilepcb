"""Final physical lane order must change only safe, owned pre-compose GPIO."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.controller_ports import route_controller_ports
from smilepcb.keyboard_routing.controller_bundles import extend_controller_bundles
from smilepcb.keyboard_routing.generation import generation_profile
def make():
 b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000;r.m_MinThroughDrill=300000;r.m_ViasMinSize=600000;r.m_ViasMinAnnularWidth=150000
 for net in ('alpha','beta','gamma','column','+3V3'):b.Add(p.NETINFO_ITEM(b,net))
 for a,z in zip([(0,0),(40000000,0),(40000000,40000000),(0,40000000)],[(40000000,0),(40000000,40000000),(0,40000000),(0,0)]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetWidth(50000);b.Add(e)
 def fp(ref,position):
  f=p.FOOTPRINT(b);f.SetReference(ref);f.SetPosition(p.VECTOR2I(*position));f.SetLayer(p.F_Cu);b.Add(f);return f
 def pad(f,number,net,x,y):
  d=p.PAD(f);d.SetNumber(number);d.SetNet(b.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(1500000,300000));ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetPosition(p.VECTOR2I(x,y));f.Add(d);return d
 chip=fp('U_MCU',(16000000,15000000))
 for i,net in enumerate(('alpha','beta','gamma')):pad(chip,str(i+1),net,20000000,14500000+i*500000)
 pad(chip,'1','alpha',20050000,14500000)
 pad(chip,'4','column',15000000,11000000);pad(chip,'5','+3V3',12000000,15000000)
 field=fp('keys',(30000000,5000000))
 for i,net in enumerate(('alpha','beta','gamma')):pad(field,str(i+1),net,32000000,3000000+i*1500000)
 pad(field,'4','column',8000000,8000000)
 t=p.PCB_TRACK(b);t.SetNet(b.FindNet('+3V3'));t.SetLayer(p.B_Cu);t.SetWidth(200000);t.SetStart(p.VECTOR2I(3000000,3000000));t.SetEnd(p.VECTOR2I(5000000,3000000));t.SetLocked(True);b.Add(t)
 cfg={'parts':[{'ref':'U_MCU','region':'controller','nets':{'1':'alpha','2':'beta','3':'gamma','4':'column','5':'+3V3'},'no_connect':[]},{'ref':'keys','region':'matrix','nets':{'1':'alpha','2':'beta','3':'gamma','4':'column'}}], 'mcu_gpio_pads':{'A0':'1','A1':'2','A2':'3','B0':'4'},'layout':{'gpio':{'rows':['A0','A1','A2'],'cols':['B0']},'rgb':False},'plane_nets':[],'qmk':{'matrix_pins':{'rows':['A0','A1','A2'],'cols':['B0']}}}
 with generation_profile('dsn-safe-v1'):
  result=route_controller_ports(b,controller_refs={'U_MCU'},mcu_refs={'U_MCU'},include_nets={'alpha','beta','gamma','column'},access_mode='prefer_same_layer',continuation_nm=500000,deadline=time.monotonic()+15)
  assert result['accepted'],result
  bundle=extend_controller_bundles(b,result['ports'],deadline=time.monotonic()+10,targets={n:(32000000,32000000) for n in ('alpha','beta','gamma')});assert bundle['accepted'],bundle
 return b,cfg,bundle['ports']
