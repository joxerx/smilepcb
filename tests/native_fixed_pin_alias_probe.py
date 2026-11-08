"""Actual KiCad aliases/poses with asymmetric physical pad positions."""
from pathlib import Path
import json,sys
from smilepcb.native_worker import p
from smilepcb.routing_exchange import canonical_dsn
from smilepcb.fixed_copper_exchange import fixed_round_pin_exchange
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False);results=[]
for back in (False,True):
 for angle in (0,90,180,270):
  b=p.BOARD();b.SetCopperLayerCount(2);net=p.NETINFO_ITEM(b,'lane');b.Add(net);front_layers=p.LSET();front_layers.AddLayer(p.F_Cu);fp=p.FOOTPRINT(b);fp.SetReference('U_TEST');fp.SetFPID(p.LIB_ID('Test','AsymmetricPins'));b.Add(fp);pads=[]
  for index,(x,y,plated) in enumerate([(-3000000,2000000,True),(5000000,1000000,True),(0,-1000000,False)]):
   d=p.PAD(fp);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_PTH if plated else p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_CIRCLE if plated else p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(2000000,2000000) if plated else p.VECTOR2I(900000,1200000));d.SetDrillSize(p.VECTOR2I(800000,800000) if plated else p.VECTOR2I(0,0));d.SetLayerSet(p.LSET.AllCuMask() if plated else front_layers);d.SetPosition(p.VECTOR2I(x,y));d.SetNet(net);fp.Add(d);pads.append(d)
  fp.SetPosition(p.VECTOR2I(50000000,50000000));fp.SetOrientationDegrees(angle)
  if back:fp.Flip(fp.GetPosition(),False)
  corners=[(30000000,30000000),(70000000,30000000),(70000000,70000000),(30000000,70000000)]
  for a,z in zip(corners,corners[1:]+corners[:1]):
   edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetLayer(p.Edge_Cuts);edge.SetStart(p.VECTOR2I(*a));edge.SetEnd(p.VECTOR2I(*z));b.Add(edge)
  # Actual connected pads, although only the alias decoder is tested here.
  for a,z in zip(pads,pads[1:]):
   track=p.PCB_TRACK(b);track.SetStart(a.GetPosition());track.SetEnd(z.GetPosition());track.SetWidth(200000);track.SetLayer(fp.GetLayer());track.SetNet(net);b.Add(track)
  name=('back' if back else 'front')+'-'+str(angle);board=out/(name+'.kicad_pcb');assert p.SaveBoard(str(board),b);dsn=out/(name+'.dsn');assert p.ExportSpecctraDSN(b,str(dsn));text=canonical_dsn(dsn.read_text())
  target=pads[1];point=target.GetPosition();native=[{'ref':'U_TEST','number':'1','uuid':target.m_Uuid.AsString(),'net':'lane','position':[point.x,point.y],'size':[2000000,2000000],'drill':[800000,800000]}]
  report={'coordinateUnits':'mm','violations':[{'severity':'error','items':[{'description':'Via [lane]','pos':{'x':60,'y':-60}},{'description':'Pin [lane]','pos':{'x':point.x/1000000,'y':-point.y/1000000}}]}]}
  try:
   changed,proof=fixed_round_pin_exchange(text,report,native,completed_nets={'lane'});assert len(proof['converted_pads'])==1 and proof['converted_pads'][0]['uuid']==target.m_Uuid.AsString();results.append({'case':name,'passed':True})
  except Exception as error:results.append({'case':name,'passed':False,'error':type(error).__name__+': '+str(error)})
(out/'result.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results));raise SystemExit(0 if all(q['passed'] for q in results) else 1)
