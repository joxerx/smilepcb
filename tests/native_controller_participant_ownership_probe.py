"""Real creator ownership on opposite copper faces and shifted geometry."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules
from smilepcb.keyboard_process import project_settings
from smilepcb.keyboard_routing.power import connect_controller_return_tree
from smilepcb.keyboard_routing.controller_plan import close_controller_obligations
from smilepcb.keyboard_routing.collector_preparation import verified_return_tree_ownership,verified_controller_local_ownership,merge_provisional_ownership,transient_access_ids
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.routing_quality import using_quality_profile
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);manager=p.GetSettingsManager();owners=[manager];report={}
def make(face,shift,aliases):
 b=p.BOARD();other=p.B_Cu if face==p.F_Cu else p.F_Cu
 for net in ('earth','signal-control','USB_DP'):b.Add(p.NETINFO_ITEM(b,net))
 def v(x,y):return p.VECTOR2I(p.FromMM(x+shift[0]),p.FromMM(y+shift[1]))
 for a,z in (((0,0),(22,0)),((22,0),(22,22)),((22,22),(0,22)),((0,22),(0,0))):
  edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(v(*a));edge.SetEnd(v(*z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);b.Add(edge)
 def footprint(ref,rows):
  f=p.FOOTPRINT(b);f.SetReference(ref);f.SetLayer(face);b.Add(f)
  for number,net,position,layer in rows:
   d=p.PAD(f);d.SetNumber(number);d.SetNet(b.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(500000,500000));layers=p.LSET();layers.AddLayer(layer);d.SetLayerSet(layers);d.SetPosition(v(*position));f.Add(d)
  return f
 footprint('U_MCU',[('1','earth',(5,5),face),('2','signal-control',(5,7),face)]+[('1','earth',(5,5),face) for _ in range(aliases)])
 footprint('C_RETURN',[('1','earth',(5,10),other)])
 footprint('R_CONTROL',[('1','signal-control',(11,7),face)])
 footprint('LED7',[('1','earth',(18,18),face)])
 t=p.PCB_TRACK(b);t.SetStart(v(12,13));t.SetEnd(v(14,13));t.SetLayer(face);t.SetWidth(150000);t.SetNet(b.FindNet('USB_DP'));t.SetLocked(True);b.Add(t)
 cfg=dict(plane_nets=['earth'],layout=dict(gpio=dict(rows=[],cols=[]),rgb=False),mcu_gpio_pads={},parts=[dict(ref=f.GetReference(),region='matrix' if f.GetReference()=='LED7' else 'controller',nets={d.GetNumber():d.GetNetname() for d in f.Pads()}) for f in b.GetFootprints()])
 return b,cfg
for name,face,shift,aliases in [('front',p.F_Cu,(0,0),0),('back-shifted',p.B_Cu,(3,2),27)]:
 path=out/(name+'.kicad_pcb');path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')));board,cfg=make(face,shift,aliases);assert p.SaveBoard(str(path),board);owners.append(board);assert manager.LoadProject(str(path.with_suffix('.kicad_pro')));owners.append(manager.GetProject(str(path.with_suffix('.kicad_pro'))));board=p.LoadBoard(str(path));owners.extend([board,board.GetDesignSettings()]);apply_native_rules(board,'fr4-2l-1oz-0p5-0p2');entry=snapshot(board)
 with generation_profile('dsn-process-v2'),using_quality_profile('turns'):
  before=snapshot(board);returns=connect_controller_return_tree(board,cfg,budget_s=8);assert returns['accepted'],returns
  returned=verified_return_tree_ownership(board,cfg,before,returns,deadline=time.monotonic()+5)
  assert set(returned.tracks)==set(returns['added_ids'])
  local_before=snapshot(board);local=close_controller_obligations(board,cfg,{'signal-control'},matrix_ports=[],deadline=time.monotonic()+30)
  controlled=verified_controller_local_ownership(board,cfg,local_before,local,deadline=time.monotonic()+5)
  assert set(controlled.tracks)==set(local['added_ids'])
  merged=merge_provisional_ownership(snapshot(board),set(),returned,controlled)
  graph=copper_graph(board,{'earth'},time.monotonic()+5);ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])};pads={f.GetReference():next(iter(f.Pads())).m_Uuid.AsString() for f in board.GetFootprints() if f.GetReference() in ('U_MCU','C_RETURN','LED7')}
  assert graph['find'](ids[pads['U_MCU']])==graph['find'](ids[pads['C_RETURN']])!=graph['find'](ids[pads['LED7']])
  after=snapshot(board);assert after['footprints']==entry['footprints'] and all(after['tracks'].get(u)==q for u,q in entry['tracks'].items());assert p.SaveBoard(str(path),board)
  cold=p.LoadBoard(str(path));owners.append(cold);assert snapshot(cold)==after;assert transient_access_ids(snapshot(cold),merged,set())==set(returned.tracks)|set(controlled.tracks)
  report[name]=dict(return_owned=len(returned.tracks),control_owned=len(controlled.tracks),required_return_pads=len(returned.pads),unrelated_return_island_preserved=True,entry_copper_exact=True,footprints_exact=True,cold_ownership_exact=True)
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
