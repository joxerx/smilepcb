"""A small real repair must be usable within a short shared budget."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.feeder_repair import repair_feeders,pending_matrix
b=p.BOARD();b.SetCopperLayerCount(2);apply_native_rules(b,COMPACT_VIAS);net=p.NETINFO_ITEM(b,'signal');b.Add(net)
pts=[(0,0),(30000000,0),(30000000,20000000),(0,20000000)]
for a,z in zip(pts,pts[1:]+pts[:1]):
 e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
for i,xy in enumerate([(5000000,10000000),(20000000,10000000)]):
 f=p.FOOTPRINT(b);f.SetReference('P'+str(i));b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(p.VECTOR2I(*xy));d.SetAttribute(p.PAD_ATTRIB_PTH);d.SetShape(p.PAD_SHAPE_CIRCLE);d.SetSize(p.VECTOR2I(1000000,1000000));d.SetDrillSize(p.VECTOR2I(300000,300000));d.SetLayerSet(p.LSET.AllCuMask());d.SetNet(net);f.Add(d)
before=snapshot(b);start=time.monotonic()
with generation_profile('dsn-safe-v1'):history=repair_feeders(b,{'signal'},set(),deadline=start+12)
missing=pending_matrix(b,{'signal'},time.monotonic()+2)
r={'history':history,'missing':missing,'footprints_exact':snapshot(b)['footprints']==before['footprints'],'elapsed_s':time.monotonic()-start};Path(sys.argv[1]).write_text(json.dumps(r,indent=2)+'\n')
