import hashlib,json,runpy,sys,time
from pathlib import Path
from smilepcb.native_worker import p
from smilepcb import keyboard_native as api
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.field_contract import load
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);sys.argv=[sys.argv[0],str(out/'fixture')]
make=runpy.run_path(str(Path(__file__).with_name('native_field_construction_probe.py')))['make']
report={}
for name,face in [('front',p.F_Cu),('back',p.B_Cu),('expired',p.F_Cu),('no-copper-terminal',p.F_Cu),('interrupted',p.F_Cu),('late-cleanup',p.F_Cu)]:
 b,cfg=make();f=p.FOOTPRINT(b);f.SetReference('U_MCU');f.SetLayer(face);f.SetPosition(p.VECTOR2I(17000000,14000000));b.Add(f)
 for i,net in enumerate(('row','a','b')):
  d=p.PAD(f);d.SetNumber(str(i+1));d.SetNet(b.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(400000,600000));layers=p.LSET();layers.AddLayer(face);d.SetLayerSet(p.LSET() if name=='no-copper-terminal' and i==0 else layers);d.SetPosition(p.VECTOR2I(17000000,14000000+1500000*i));f.Add(d)
 for component in b.GetFootprints():
  if component.GetReference()=='U_MCU':cfg['parts'][0]['footprint']=''
  else:cfg['parts'].append({'ref':component.GetReference(),'footprint':'','region':'matrix','nets':{d.GetNumber():d.GetNetname() for d in component.Pads() if d.GetNumber()}})
 folder=out/name;folder.mkdir();path=folder/'keyboard.kicad_pcb';p.SaveBoard(str(path),b);(folder/'.smilepcb-keyboard.json').write_text(json.dumps(cfg));before=hashlib.sha256(path.read_bytes()).hexdigest()
 old_prepare=None;old_cleanup=None;stage_deadline=None
 if name=='late-cleanup':
  from types import SimpleNamespace
  from smilepcb.keyboard_routing import matrix_stage,cleanup
  old_prepare=matrix_stage.prepare_matrix;old_cleanup=matrix_stage.cleanup_generated
  def prepare_late(*args,**kwargs):
   global stage_deadline
   stage_deadline=kwargs['deadline'];return old_prepare(*args,**kwargs)
  def cleanup_late(*args,**kwargs):
   clock=cleanup.time;cleanup.time=SimpleNamespace(monotonic=lambda:stage_deadline+1)
   try:return old_cleanup(*args,**kwargs)
   finally:cleanup.time=clock
  matrix_stage.prepare_matrix=prepare_late;matrix_stage.cleanup_generated=cleanup_late
 if name=='interrupted':
  from smilepcb.keyboard_routing import matrix_stage
  old_prepare=matrix_stage.prepare_matrix
  def expired_midphase(*args,**kwargs):raise TimeoutError('Native graph phase deadline exhausted')
  matrix_stage.prepare_matrix=expired_midphase
 try:
  r=api.run({'operation':'matrix_field','board':str(path),'include_controller':True,'budget_s':1e-6 if name=='expired' else 65});error=None
 except Exception as exc:r={};error=type(exc).__name__+': '+str(exc)
 finally:
  if old_prepare is not None:matrix_stage.prepare_matrix=old_prepare
  if old_cleanup is not None:matrix_stage.cleanup_generated=old_cleanup
 saved=p.LoadBoard(str(path));g=copper_graph(saved,{'row','a','b'},time.monotonic()+10);groups={}
 for i,n in enumerate(g['nodes']):
  if isinstance(n['item'],p.PAD) and n['item'].GetNumber():groups.setdefault(n['net'],set()).add(g['find'](i))
 contract=load(folder);mcu_ids={d.m_Uuid.AsString() for fp in saved.GetFootprints() if fp.GetReference()=='U_MCU' for d in fp.Pads()}
 report[name]={'accepted':r.get('accepted'),'all_groups_connected':all(len(v)==1 for v in groups.values()),'mcu_recorded':contract is not None and mcu_ids<=set(contract['terminals']),'source_file_unchanged':hashlib.sha256(path.read_bytes()).hexdigest()==before,'no_receipt':contract is None,'error':error,'diagnostic_exists':(folder/'.smilepcb-matrix-rejected.kicad_pcb').is_file(),'result':r}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
