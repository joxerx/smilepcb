"""Real native constructor/worker controls for the private named policy."""
import json,sys
from pathlib import Path
from dataclasses import asdict
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.generation import generation_profile,current_generation_profile
from smilepcb.keyboard_routing.safety import require_via_land_clearance
from smilepcb import keyboard_native as api
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False);report={}
def vec(x,y):return p.VECTOR2I(round(x*1000000),round(y*1000000))
def board(copper=150000,hole=250000):
 b=p.BOARD();rules=b.GetDesignSettings();rules.m_MinClearance=copper;rules.m_HoleClearance=hole;rules.m_TrackMinWidth=150000;rules.m_ViasMinSize=500000;rules.m_MinThroughDrill=200000;rules.m_ViasMinAnnularWidth=150000;rules.m_NetSettings.GetDefaultNetclass().SetClearance(copper)
 for n in ('signal','other'):b.Add(p.NETINFO_ITEM(b,n))
 for a,z in (((0,0),(40,0)),((40,0),(40,30)),((40,30),(0,30)),((0,30),(0,0))):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(*a));e.SetEnd(vec(*z));e.SetWidth(50000);e.SetLayer(p.Edge_Cuts);b.Add(e)
 fp=p.FOOTPRINT(b);fp.SetReference('U1');b.Add(fp);d=p.PAD(fp);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(vec(1,1));d.SetPosition(vec(10,10));d.SetNet(b.FindNet('other'));ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);fp.Add(d)
 return b
for name in ('native','dsn-safe-v1','dsn-process-v2'):
 b=board();before=snapshot(b)
 with generation_profile(name):
  router=LocalRouter(b,compact=True,track_width_nm=150000)
  report[name]={'resolved':asdict(router.generation),'pocket_allowed':router.via_clear((10975000,10000000),'signal'),'source_exact':snapshot(b)==before}
 assert current_generation_profile().name=='native'
b=board(300000,400000)
with generation_profile('dsn-process-v2'):
 router=LocalRouter(b,compact=True,track_width_nm=150000);report['larger_native_floor']={'resolved':asdict(router.generation),'near_foreign_pad_rejected':not router.via_clear((10975000,10000000),'signal')}
# Same-net solder-land protection remains independent of ordinary net collisions.
b=board();pad=next(d for f in b.GetFootprints() for d in f.Pads());pad.SetNet(b.FindNet('signal'))
with generation_profile('dsn-process-v2'):
 router=LocalRouter(b,compact=True,track_width_nm=150000);report['same_net_drill_land_rejected']=not router.via_clear((10000000,10000000),'signal')
 router.add_via((10000000,10000000),'signal')
 try:require_via_land_clearance(b)
 except ValueError:report['independent_saved_land_gate_rejected']=True
 else:report['independent_saved_land_gate_rejected']=False
# Genuine tiny key-field capture/worker proof, not a full-keyboard seed.
from smilepcb.keyboard_routing.field_contract import capture
b=board();path=out/'keyboard.kicad_pcb'
with generation_profile('dsn-process-v2'):
 router=LocalRouter(b,compact=True,track_width_nm=150000);router.add_path([(10000000,10000000),(10000000,12000000)],p.F_Cu,'other')
 pad=next(d for f in b.GetFootprints() for d in f.Pads())
 contract=capture(snapshot(b),{'complete':True,'roles':{uid:'column_trunk' for uid in router.added},'owned_ids':router.added,'terminals':{pad.m_Uuid.AsString():['columns','other']}})
(out/'.smilepcb-field.json').write_text(json.dumps(contract));p.SaveBoard(str(path),b)
(out/'.smilepcb-keyboard.json').write_text(json.dumps({'parts':[{'ref':f.GetReference(),'footprint':f.GetFPID().GetUniStringLibId(),'nets':{d.GetNumber():d.GetNetname() for d in f.Pads() if d.GetNumber()}} for f in b.GetFootprints()],'plane_nets':[]}))
report['worker']=api.run({'operation':'field_verify','board':str(path),'generation_profile':'dsn-process-v2'})
report['worker_context_reset']=current_generation_profile().name=='native'
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
