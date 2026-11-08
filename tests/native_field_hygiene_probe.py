import json,runpy,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.field_hygiene import remove_covered
from smilepcb.keyboard_routing.completion import pad_partitions
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);sys.argv=[sys.argv[0],str(out/'fixture')]
fixture=runpy.run_path(str(Path(__file__).with_name('native_field_construction_probe.py')));b=p.LoadBoard(str(out/'fixture/field.kicad_pcb'))
source=snapshot(b);trunk=next(t for t in b.GetTracks() if t.GetClass()=='PCB_TRACK' and t.GetNetname()=='row' and t.GetStart().y==t.GetEnd().y)
a,z=trunk.GetStart(),trunk.GetEnd();dup=p.PCB_TRACK(b);dup.SetStart(p.VECTOR2I((3*a.x+z.x)//4,a.y));dup.SetEnd(p.VECTOR2I((a.x+3*z.x)//4,a.y));dup.SetWidth(trunk.GetWidth());dup.SetLayer(trunk.GetLayer());dup.SetNet(trunk.GetNet());b.Add(dup);uid=dup.m_Uuid.AsString()
before=pad_partitions(b,time.monotonic()+10);removed=remove_covered(b,{uid});after=snapshot(b)
report={'covered_fragment_removed':removed==[uid],'source_exact':after==source,'physical_contacts_exact':before==pad_partitions(b,time.monotonic()+10)}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
