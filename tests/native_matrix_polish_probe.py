import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.matrix_polish import polish_matrix,prune_matrix_contacts
from smilepcb.keyboard_routing.generation import generation_profile
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);result={}
for case in ('jog','interior','expired','contact'):
 b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000
 for n in ('signal','power'):b.Add(p.NETINFO_ITEM(b,n))
 for a,z in zip([(0,0),(20000000,0),(20000000,15000000),(0,15000000)],[(20000000,0),(20000000,15000000),(0,15000000),(0,0)]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetWidth(50000);b.Add(e)
 for i,xy in enumerate([(2000000,5000000),(12000000,5000000)]+([(7000000,7000000)] if case=='interior' else [])):
  f=p.FOOTPRINT(b);f.SetReference('terminal'+str(i));b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(p.VECTOR2I(*xy));d.SetSize(p.VECTOR2I(500000,500000));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet('signal'));f.Add(d)
 path=[(2000000,5000000),(4000000,5000000),(6000000,7000000),(8000000,7000000),(10000000,5000000),(12000000,5000000)]
 for a,z in zip(path,path[1:]):
  t=p.PCB_TRACK(b);t.SetNet(b.FindNet('signal'));t.SetLayer(p.F_Cu);t.SetWidth(150000);t.SetStart(p.VECTOR2I(*a));t.SetEnd(p.VECTOR2I(*z));b.Add(t)
 if case=='contact':
  t=p.PCB_TRACK(b);t.SetNet(b.FindNet('signal'));t.SetLayer(p.F_Cu);t.SetWidth(150000);t.SetStart(p.VECTOR2I(2000000,5000000));t.SetEnd(p.VECTOR2I(2100000,5100000));b.Add(t)
 fixed=p.PCB_TRACK(b);fixed.SetNet(b.FindNet('power'));fixed.SetLayer(p.B_Cu);fixed.SetWidth(300000);fixed.SetStart(p.VECTOR2I(1000000,1000000));fixed.SetEnd(p.VECTOR2I(10000000,1000000));fixed.SetLocked(True);b.Add(fixed);uid=fixed.m_Uuid.AsString();before=snapshot(b)
 with generation_profile('dsn-safe-v1'):report=polish_matrix(b,protected_ids={uid},eligible_nets={'signal'},deadline=time.monotonic()-1 if case=='expired' else time.monotonic()+12)
 if case=='contact':report['contacts']=prune_matrix_contacts(b,protected_ids={uid},eligible_nets={'signal'},deadline=time.monotonic()+10)
 after=snapshot(b);result[case]={'report':report,'unchanged':before==after,'fixed_exact':after['tracks'][uid]==before['tracks'][uid],'footprints_exact':before['footprints']==after['footprints'],'track_count_before':len(before['tracks']),'track_count_after':len(after['tracks'])}
 p.SaveBoard(str(out/(case+'.kicad_pcb')),b)
(out/'report.json').write_text(json.dumps(result,indent=2))
