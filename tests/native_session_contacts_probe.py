"""Real SES imports must preserve old contacts even on incomplete nets."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.completion import pad_partitions
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);root=Path(__file__).resolve().parents[1];report={}
for name,points in [('split',[(20,10),(25,10)]),('merge',[(5,10),(25,10)])]:
 folder=out/name;folder.mkdir();b=p.BOARD();b.SetCopperLayerCount(2);n=p.NETINFO_ITEM(b,'bus');b.Add(n);fp=p.FOOTPRINT(b);fp.SetReference('CONTACTS');b.Add(fp)
 for i,x in enumerate((5,10,20,25),1):
  pad=p.PAD(fp);pad.SetNumber(str(i));pad.SetNet(n);pad.SetAttribute(p.PAD_ATTRIB_PTH);pad.SetShape(p.PAD_SHAPE_CIRCLE);pad.SetSize(p.VECTOR2I(1000000,1000000));pad.SetDrillSize(p.VECTOR2I(400000,400000));pad.SetLayerSet(p.LSET.AllCuMask());pad.SetPosition(p.VECTOR2I(x*1000000,10000000));fp.Add(pad)
 t=p.PCB_TRACK(b);t.SetNet(n);t.SetLayer(p.F_Cu);t.SetWidth(150000);t.SetStart(p.VECTOR2I(5000000,10000000));t.SetEnd(p.VECTOR2I(10000000,10000000));b.Add(t)
 path=folder/'keyboard.kicad_pcb';p.SaveBoard(str(path),b);(folder/'.smilepcb-keyboard.json').write_text(json.dumps({'parts':[{'ref':'CONTACTS','footprint':''}]}));old=path.read_bytes();before=pad_partitions(p.LoadBoard(str(path)),time.monotonic()+5)
 coordinates=' '.join(str(v) for x,y in points for v in (x*10000,-y*10000));ses=folder/'candidate.ses';ses.write_text('(session board (base_design board) (routes (resolution um 10) (library_out) (network_out (net "bus" (wire (path F.Cu 1500 '+coordinates+'))))))\n')
 r=subprocess.run([sys.executable,str(root/'recipes/stm32/import_session.py')],input=json.dumps({'board':str(path),'ses':str(ses),'protected_ids':[],'replace_editable':True}),text=True,capture_output=True,env={**os.environ,'PYTHONPATH':str(root)},timeout=20)
 after=pad_partitions(p.LoadBoard(str(path)),time.monotonic()+5)
 report[name]={'exit_code':r.returncode,'before_groups':len(before),'after_groups':len(after),'source_unchanged':path.read_bytes()==old,'stderr':r.stderr[-2000:]}
(out/'contacts-report.json').write_text(json.dumps(report,indent=2)+'\n')
