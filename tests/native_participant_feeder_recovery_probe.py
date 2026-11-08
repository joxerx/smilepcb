"""Mixed participant recovery with controlled real Native carrier creation.

The fixture's immediate track creator supplies Native-checked return ownership;
this is a geometry/transaction coupon, not a full keyboard generation claim.
"""
import json,sys,time
from copy import deepcopy
from pathlib import Path
from native_provisional_collector_fixture import build_native,blueprint
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import project_settings,apply_native_rules
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.routing_quality import using_quality_profile
from smilepcb.keyboard_routing.collector_preparation import verified_return_tree_ownership,transient_access_ids
from smilepcb.keyboard_routing.feeder_repair import repair_feeders,matrix_pad_state
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.completion import pad_partitions
from smilepcb.keyboard_routing.handoff import preserves_contacts
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launches
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);manager=p.GetSettingsManager();owners=[manager];report={};return_net=next(reversed(blueprint()['closed_carriers']));matrix_nets=({'inner'}|set(blueprint()['closed_carriers']))-{return_net}
for name,face,shift in [('back',p.B_Cu,(0,0)),('front-shifted',p.F_Cu,(2000000,3000000))]:
 with generation_profile('dsn-process-v2'),using_quality_profile('turns'):
  captured=[]
  def created(board,net,before,added):
   if net!=return_net:return
   cfg={'plane_nets':[net],'parts':[dict(ref=f.GetReference(),region='controller',nets={d.GetNumber():d.GetNetname() for d in f.Pads()}) for f in board.GetFootprints()]}
   required=[d.m_Uuid.AsString() for f in board.GetFootprints() for d in f.Pads() if d.GetNumber() and d.GetNetname()==net]
   proof=dict(accepted=True,source_copper_unchanged=True,missing_checked=True,missing={},added_ids=list(added),required=len(required),required_pad_ids=required)
   captured.append(verified_return_tree_ownership(board,cfg,before,proof,deadline=time.monotonic()+5))
  board,fixture=build_native(p,face=face,translation=shift,on_carrier=created);assert len(captured)==1;owner=captured[0]
  # An unrelated old pad group shares the return net and must remain separate.
  f=p.FOOTPRINT(board);f.SetReference('OTHER_RETURN');board.Add(f);points=[]
  for i,x in enumerate((26000000,28000000),1):
   d=p.PAD(f);d.SetNumber(str(i));d.SetNet(board.FindNet(return_net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(500000,500000));ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls);d.SetPosition(p.VECTOR2I(x+shift[0],20000000+shift[1]));f.Add(d);points.append(d.GetPosition())
  t=p.PCB_TRACK(board);t.SetStart(points[0]);t.SetEnd(points[1]);t.SetWidth(150000);t.SetNet(board.FindNet(return_net));t.SetLayer(face);board.Add(t);unknown=t.m_Uuid.AsString()
  path=out/(name+'.kicad_pcb');path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')));assert p.SaveBoard(str(path),board);owners.append(board);assert manager.LoadProject(str(path.with_suffix('.kicad_pro')));owners.append(manager.GetProject(str(path.with_suffix('.kicad_pro'))));board=p.LoadBoard(str(path));owners.extend([board,board.GetDesignSettings()]);apply_native_rules(board,'fr4-2l-1oz-0p5-0p2');before=snapshot(board)
  assert transient_access_ids(before,owner,matrix_nets)==set(owner.tracks)
  ports=[deepcopy(q) for q in fixture['current_ports'] if q['net']!=return_net];sources=[deepcopy(q) for q in fixture['source_ports'] if q['net']!=return_net];hard=set(fixture['immutable_ids'])|{unknown};prior=pad_partitions(board,time.monotonic()+5);start=time.monotonic()
  with reserve_launches(ports):
   history=repair_feeders(board,matrix_nets,hard,deadline=time.monotonic()+38,ports=ports,source_ports=sources,participant_ownership=owner)
   final_ports=list(current_launches())
  after=snapshot(board);return_moved=any(u in set(row.get('plan',{}).get('removed',())) for row in history for u in owner.tracks);state=matrix_pad_state(board,matrix_nets,time.monotonic()+5);groups=pad_partitions(board,time.monotonic()+5)
  report[name]=dict(return_participant_displaced=return_moved,accepted=bool(history and history[-1]['accepted']),missing=state['missing'],history=history,prior_contacts_preserved=preserves_contacts([list(g) for g in prior],[list(g) for g in groups]),hard_exact=all(after['tracks'].get(u)==before['tracks'][u] for u in hard),footprints_exact=after['footprints']==before['footprints'],return_partitions=sum(bool(set(owner.pads)&set(g)) or any(d.m_Uuid.AsString() in g for d in f.Pads()) for g in groups),elapsed_s=time.monotonic()-start)
  assert p.SaveBoard(str(path),board);cold=p.LoadBoard(str(path));owners.append(cold);report[name]['cold_exact']=snapshot(cold)==after
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({n:{k:v for k,v in q.items() if k!='history'} for n,q in report.items()}))
