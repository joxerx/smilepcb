import json,time,sys
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.controller_ports import route_controller_ports
from smilepcb.keyboard_routing.generation import generation_profile
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);report={}
def make(face):
 b=p.BOARD();b.SetCopperLayerCount(2);rules=b.GetDesignSettings();rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000;rules.m_MinThroughDrill=300000;rules.m_ViasMinSize=600000;rules.m_ViasMinAnnularWidth=150000
 for a,z in zip([(0,0),(40000000,0),(40000000,40000000),(0,40000000)],[(40000000,0),(40000000,40000000),(0,40000000),(0,0)]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetWidth(50000);b.Add(e)
 f=p.FOOTPRINT(b);f.SetReference('chip');f.SetLayer(face);f.SetPosition(p.VECTOR2I(16000000,15000000));b.Add(f)
 for i in range(3):
  n=p.NETINFO_ITEM(b,'signal'+str(i));b.Add(n);d=p.PAD(f);d.SetNumber(str(i+1));d.SetNet(n);d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(1500000,300000));ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls);d.SetPosition(p.VECTOR2I(20000000,14500000+i*500000));f.Add(d)
 t=p.PCB_TRACK(b);t.SetNet(b.FindNet('signal0'));t.SetLayer(p.B_Cu);t.SetWidth(200000);t.SetStart(p.VECTOR2I(3000000,3000000));t.SetEnd(p.VECTOR2I(5000000,3000000));t.SetLocked(True);b.Add(t)
 return b
for name,face in [('front',p.F_Cu),('back',p.B_Cu),('blocked',p.F_Cu),('expired',p.F_Cu),('bundle',p.F_Cu),('bundle-blocked',p.B_Cu),('bundle-expired',p.F_Cu),('state-budget',p.F_Cu),('pitch',p.F_Cu)]:
 b=make(face)
 if name=='blocked':
  f=p.FOOTPRINT(b);f.SetReference('mechanical-hole');b.Add(f);d=p.PAD(f);d.SetAttribute(p.PAD_ATTRIB_NPTH);d.SetShape(p.PAD_SHAPE_CIRCLE);d.SetSize(p.VECTOR2I(2000000,2000000));d.SetDrillSize(p.VECTOR2I(2000000,2000000));d.SetLayerSet(p.LSET.AllCuMask());d.SetPosition(p.VECTOR2I(22500000,15000000));f.Add(d)
 before=snapshot(b);error=None
 try:
  with generation_profile('dsn-safe-v1'):
   r=route_controller_ports(b,controller_refs={'chip'},mcu_refs={'chip'},max_states=1 if name=='state-budget' else 12000,deadline=time.monotonic()+(1e-6 if name=='expired' else 10),continuation_nm=500000,access_mode='prefer_same_layer',include_nets={'signal0','signal1','signal2'},bank_pitch_nm=350000 if name=='pitch' else None)
 except Exception as e:r={};error=type(e).__name__+': '+str(e)
 bundle=None
 if name.startswith('bundle') and r.get('accepted'):
  if name=='bundle-blocked':
   f=p.FOOTPRINT(b);f.SetReference('bundle-hole');b.Add(f);d=p.PAD(f);d.SetAttribute(p.PAD_ATTRIB_NPTH);d.SetShape(p.PAD_SHAPE_CIRCLE);d.SetSize(p.VECTOR2I(2000000,2000000));d.SetDrillSize(p.VECTOR2I(2000000,2000000));d.SetLayerSet(p.LSET.AllCuMask());d.SetPosition(p.VECTOR2I(25000000,16000000));f.Add(d)
  before_bundle=snapshot(b);before=before_bundle
  try:
   from smilepcb.keyboard_routing.controller_bundles import extend_controller_bundles
   with generation_profile('dsn-safe-v1'):
    bundle=extend_controller_bundles(b,r['ports'],deadline=time.monotonic()+(-1 if name=='bundle-expired' else 10),targets={p['net']:(32000000,32000000) for p in r['ports']})
   if bundle['accepted']:r['ports']=bundle['ports']
   bundle['unchanged_on_failure']=snapshot(b)==before_bundle
  except Exception as e:bundle={'error':type(e).__name__+': '+str(e)}
 after=snapshot(b);ports=r.get('ports',[]);contacts=True
 if r.get('accepted'):
  from smilepcb.keyboard_routing.native_graph import copper_graph
  path=out/(name+'.kicad_pcb');p.SaveBoard(str(path),b);saved=p.LoadBoard(str(path));g=copper_graph(saved,{'signal0','signal1','signal2'},time.monotonic()+5);index={n['item'].m_Uuid.AsString():i for i,n in enumerate(g['nodes'])}
  contacts=all(all(g['find'](index[u])==g['find'](index[port['terminal_id']]) for u in port['pad_ids']) for port in ports)
 report[name]={'rejected_search_states':(r.get('states',0)+r.get('preferred_bank_search',{}).get('states',0)) if r.get('accepted') is False else None,'bundle':bundle,'unchanged':after==before,'contacts_after_reopen':contacts,'error':error,'accepted':r.get('accepted'),'ports':ports,'new_vias':sum(t['class']=='PCB_VIA' for uid,t in after['tracks'].items() if uid not in before['tracks']),'source_preserved':before['footprints']==after['footprints'] and all(after['tracks'].get(u)==t for u,t in before['tracks'].items()),'added_tracks':len(after['tracks'])-len(before['tracks'])}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
