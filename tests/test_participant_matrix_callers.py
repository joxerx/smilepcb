"""Actual caller bodies; native proof/geometry is a separate qualification."""
import ast,json,sys,time,traceback
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from test_owned_feeder_transaction import NativeStateSeams
ROOT=Path(__file__).resolve().parents[1]

def load_function(module,name,env):
 path=ROOT/'smilepcb/keyboard_routing'/(module+'.py');tree=ast.parse(path.read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
 scope=dict(__name__='smilepcb.keyboard_routing.'+module,__package__='smilepcb.keyboard_routing',**env);exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),scope);return scope[name]


def collector_scene(monkeypatch,*,matrix_failure=False,late_failure=False):
 class Item:
  def __init__(self,uid,net):self.uid=uid;self.net=net;self.m_Uuid=NS(AsString=lambda:uid)
  def GetNetname(self):return self.net
  def SetNet(self,n):self.net=n
  def GetNumber(self):return '1'
  def Duplicate(self):return Item(self.uid,self.net)
 class Board:
  def __init__(self):self.items=[Item('imported','earth'),Item('raw','row'),Item('local-body','control')];self.pad=Item('source-pad','row');self.fp=NS(Pads=lambda:[self.pad],GetReference=lambda:'U_MCU',GetLayer=lambda:2)
  def GetTracks(self):return list(self.items)
  def GetFootprints(self):return [self.fp]
  def Delete(self,item):self.items.remove(item)
  def Add(self,item):self.items.append(item)
  def FindNet(self,n):return n
  def BuildConnectivity(self):pass
  def GetDesignSettings(self):return NS(m_TrackMinWidth=150000,m_MinClearance=173000)
 b=Board();snap=lambda board:dict(tracks={t.uid:dict(net=t.net) for t in board.items},footprints={'chip':{'ref':'U_MCU','pads':[{'uuid':'source-pad','net':board.pad.net}]}})
 cfg=dict(parts=[dict(ref='U_MCU',region='controller',nets={'1':'row'})],plane_nets=['earth']);entry=snap(b);events=[];seen={};port=dict(net='row',pad_ids=['source-pad'],added_ids=['raw'])
 local=NS(tracks={'local-body':{'net':'control'}},pads={});returned=NS(tracks={'return-body':{'net':'earth'}},pads={});combined=NS(tracks={**local.tracks,**returned.tracks},pads={})
 class Source:
  controller_ownership=local
  def take(self,board,model,nets,**kw):events.append('take');assert nets=={'row'};return dict(accepted=True,ports=[deepcopy(port)],added_ids=['raw'])
  def matrix_delta(self,board,model,ports,**kw):events.append('authenticate');assert ports[0]['added_ids']==['raw'];return {'raw'}
 source=Source()
 def certify(*a,**kw):raise AssertionError('Retained authenticated sources entered destructive replacement')
 def returns(board,model,**kw):events.append('returns');seen['before-return']=snap(board);board.items.append(Item('return-body','earth'));return dict(accepted=True,added_ids=['return-body'])
 def verify_return(board,model,before,proof,**kw):
  events.append('verify-return');assert before==seen['before-return'] and proof['added_ids']==['return-body'];return returned
 def merge(state,nets,*owners):assert nets=={'row'} and owners==(local,returned);events.append('merge');return combined
 def transient(state,owned,nets):return set() if owned is None else set(owned.tracks)
 def stage(board,model,**kw):
  seen['matrix-args']=kw;events.append('matrix')
  assert kw.get('participant_ownership') is combined and kw.get('generated_heads') is None and kw['collectors'] is False
  if matrix_failure:return dict(complete=False,controller_connected=False,reason='controlled rejection')
  board.items.remove(next(t for t in board.items if t.uid=='return-body'));board.items.append(Item('return-descendant','earth'))
  if late_failure:board.items.remove(next(t for t in board.items if t.uid=='local-body'));board.items.append(Item('local-descendant','control'))
  return dict(complete=True,controller_connected=True,matrix_ports=[deepcopy(port)])
 def stub(name,**values):monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.'+name,NS(**values))
 stub('generation',current_generation_profile=lambda:NS(name='dsn-process-v2'))
 stub('conditional_banks',certify_generated_heads=certify,copy_native_tracks=lambda board:{t.uid:t.Duplicate() for t in board.items},_source_physics=lambda board,ids:json.dumps(snap(board),sort_keys=True))
 stub('modules',connect_key_modules=lambda *a,**kw:dict(accepted=True))
 stub('power',connect_controller_return_tree=returns)
 def verify_interface(*a,**kw):
  if late_failure:raise ValueError('Injected post-matrix physical proof failure')
  return dict(resolved=[['source-pad']])
 stub('interface_contract',capture_interfaces=lambda *a,**kw:dict(schema=1,ports=[deepcopy(port)]),verify_interfaces=verify_interface)
 env=dict(time=NS(monotonic=lambda:1000.),snapshot=snap,deepcopy=deepcopy,p=NS(F_Cu=0,B_Cu=2),traceback=traceback,Path=Path,ordered_nets=lambda c:[('rows',['row']),('columns',[])],reserve_launches=lambda *a:nullcontext(),transient_access_ids=transient,verified_return_tree_ownership=verify_return,merge_provisional_ownership=merge,matrix_stage=NS(prepare_matrix=stage),collector_assignment=lambda *a:({'row':'row'},[dict(collector_heading=(1,0),signal_peel_order=['row'])]),apply_exit_assignment=lambda board,model,ports,**kw:(deepcopy(model),dict(ports=deepcopy(ports))))
 fn=load_function('collector_preparation','prepare_collector_matrix',env)
 return fn,b,cfg,source,entry,snap,events,seen


@pytest.mark.parametrize('reject',[False,True])
def test_authenticated_additive_source_reaches_participant_recovery_and_captures_only_true_immutable_prerequisites(monkeypatch,reject):
 fn,b,cfg,source,entry,snap,events,seen=collector_scene(monkeypatch,matrix_failure=reject)
 updated,result=fn(b,cfg,controller_refs={'U_MCU'},nets={'row'},targets={},fixed_ports=[],deadline=1200,preplanned_source=source)
 assert events[:2]==['take','authenticate'] and events[-4:]==['returns','verify-return','merge','matrix']
 if reject:assert not result['candidate_accepted'] and snap(b)==entry
 else:
  assert result['candidate_accepted'] and result['prerequisite_ids']==['imported']
  assert 'return-body' not in snap(b)['tracks'] and 'return-descendant' in snap(b)['tracks']


@pytest.mark.parametrize('failure',['reject','exception'])
def test_participant_matrix_wrapper_restores_all_entry_copper_and_launch_state(monkeypatch,failure):
 from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory
 Track=NativeStateSeams.Track;Board=NativeStateSeams.Board
 board=Board([Track('source',dict(net='row',locked=True,**{'class':'PCB_TRACK'})),Track('owned',dict(net='earth',locked=False,**{'class':'PCB_TRACK'}))]);board.GetFootprints=lambda:[]
 snap=lambda b:dict(tracks={u:deepcopy(t.data) for u,t in b.items.items()},footprints={})
 physical=lambda b,ids:json.dumps({u:[t.data,t.raw] for u,t in b.items.items()},sort_keys=True)
 before=snap(board);token=object()
 def native_stage(actual,cfg,**kw):
  assert kw['participant_ownership'] is token
  actual.Delete(actual.items['owned']);actual.Add(Track('new',dict(net='earth',locked=False,**{'class':'PCB_TRACK'})));current_launch_inventory().ports.clear();current_launch_inventory().resolved.clear();current_launch_inventory().repair_reserved.clear()
  if failure=='exception':raise RuntimeError('Injected participant matrix failure')
  return dict(complete=False,controller_connected=False)
 monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.conditional_banks',NS(copy_native_tracks=lambda b:{u:t.Duplicate() for u,t in b.items.items()},_source_physics=physical))
 fn=load_function('matrix_stage','prepare_matrix',dict(snapshot=snap,_prepare_matrix=native_stage))
 ports=[dict(net='row',pad_ids=['pad'],added_ids=['source'])]
 with reserve_launches(ports,resolved={('pad',)},repair_reserved={('pad',)}):
  inventory=current_launch_inventory();old=deepcopy(inventory.__dict__)
  if failure=='exception':
   with pytest.raises(RuntimeError,match='matrix failure'):fn(board,{},row_layer=0,column_layer=2,deadline=1200,participant_ownership=token)
  else:assert not fn(board,{},row_layer=0,column_layer=2,deadline=1200,participant_ownership=token)['complete']
  assert snap(board)==before and inventory.__dict__==old


def test_late_collector_proof_failure_restores_preexisting_movable_controller_body(monkeypatch):
 fn,b,cfg,source,entry,snap,events,seen=collector_scene(monkeypatch,late_failure=True)
 updated,result=fn(b,cfg,controller_refs={'U_MCU'},nets={'row'},targets={},fixed_ports=[],deadline=1200,preplanned_source=source)
 assert not result['candidate_accepted'] and snap(b)==entry
 assert 'post-matrix' in result['reason']


@pytest.mark.parametrize('supplier',['conditional','corridor'])
def test_complete_matrix_can_publish_new_protected_bodies_absent_from_entry(monkeypatch,supplier):
 # Regresses a successful production _prepare_matrix publication indexing a
 # newly protected UUID in the entry snapshot. Native geometry remains a
 # separate fixture; this runs the actual complete caller body.
 class Pad:
  def __init__(self,uid,number,net):self.m_Uuid=NS(AsString=lambda:uid);self.number=number;self.net=net
  def GetNumber(self):return self.number
  def GetNetname(self):return self.net
 pads=[Pad('r-pad','1','row'),Pad('c-pad','2','col')]
 chip=NS(GetReference=lambda:'U_MCU',Pads=lambda:pads)
 board=NS(GetFootprints=lambda:[chip]);tracks={'raw-row':dict(net='row',locked=True),'raw-col':dict(net='col',locked=True)}
 snap=lambda b:dict(tracks=deepcopy(tracks),footprints={})
 ports=[dict(net='row',pad_ids=['r-pad'],added_ids=['raw-row']),dict(net='col',pad_ids=['c-pad'],added_ids=['raw-col'])]
 cfg=dict(parts=[dict(ref='U_MCU',nets={'1':'row','2':'col'})],layout=dict(gpio=dict(rows=['RP'],cols=['CP'])),mcu_gpio_pads={'RP':'1','CP':'2'})
 def commit(*a,**kw):
  tracks.update({'new-source':dict(net='row',locked=False),'new-head':dict(net='col',locked=False)})
  return dict(accepted=True,supported=True,source_ids=['new-source'],head_ids=['new-head'],added_ids=['new-source','new-head'],matrix_ports=deepcopy(ports))
 def stub(name,**values):monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.'+name,NS(**values))
 stub('conditional_banks',prepare_conditional_banks=commit)
 stub('interface_launches',interface_identity=lambda q:tuple(q['pad_ids']),current_launches=lambda:[],current_launch_inventory=lambda:NS(resolved=set()),publish_launches=lambda *a,**kw:None,reserve_launches=lambda *a:nullcontext())
 stub('matrix_polish',polish_matrix=lambda *a,**kw:{},prune_matrix_contacts=lambda *a,**kw:{})
 env=dict(time=NS(monotonic=lambda:0.),snapshot=snap,pad_partitions=lambda *a:[['r-pad'],['c-pad']],ordered_nets=lambda c:[('rows',['row']),('columns',['col'])],build_field=lambda *a,**kw:dict(complete=True,owned_ids=[],generated_owned_ids=[],roles={}),_field_cutoff=lambda *a:100.,matrix_pad_state=lambda *a:dict(missing={},pad_ids={'r-pad','c-pad'},groups={}),preserves_contacts=lambda *a:True,remove_covered=lambda *a:[],cleanup_generated=lambda *a,**kw:{},clean_matrix_tracks=lambda *a,**kw:dict(removed_ids=[]))
 fn=load_function('matrix_stage','_prepare_matrix',env)
 result=fn(board,cfg,row_layer=0,column_layer=2,deadline=100.,ports=ports,generated_heads=NS(tracks={}) if supplier=='conditional' else None,corridor_plan=NS(commit=commit) if supplier=='corridor' else None)
 assert result['complete'] and result['controller_connected'] and result['immutable_source_copper_preserved']
 assert {'new-source','new-head','raw-row','raw-col'}<=set(result['source_ids'])
 assert tracks['new-source']==dict(net='row',locked=False) and tracks['new-head']==dict(net='col',locked=False)


def test_an_accepted_epoch_prevents_borrowing_with_the_original_owner(monkeypatch):
 class Pad:
  def __init__(self,uid,num,net):self.m_Uuid=NS(AsString=lambda:uid);self.num=num;self.net=net
  def GetNumber(self):return self.num
  def GetNetname(self):return self.net
 pads=[Pad('r-pad','1','row'),Pad('c-pad','2','col')]
 chip=NS(GetReference=lambda:'U_MCU',Pads=lambda:pads)
 board=NS(GetFootprints=lambda:[chip],GetDesignSettings=lambda:NS(m_TrackMinWidth=150000))
 cfg=dict(parts=[dict(ref='U_MCU',nets={'1':'row','2':'col'})],layout=dict(gpio=dict(rows=['RP'],cols=['CP'])),mcu_gpio_pads={'RP':'1','CP':'2'})
 snap=lambda b:dict(tracks={},footprints={});owner=object();calls=[]
 def stub(name,**values):monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.'+name,NS(**values))
 stub('collector_preparation',transient_access_ids=lambda *a:set())
 stub('interface_launches',interface_identity=lambda q:tuple(q['pad_ids']),current_launches=lambda:[],current_launch_inventory=lambda:NS(resolved=set()),publish_launches=lambda *a,**kw:None,reserve_launches=lambda *a:nullcontext())
 def repair(*a,**kw):
  calls.append(kw)
  return [dict(accepted=True,participant_epoch_advanced=True),dict(accepted=False,retained_pending={'row':1},plan={'diagnostic':{'attempts':[{'search':{'reason':'search_budget'}}]}})]
 def router(*a,**kw):return NS(added=[],launch_policy=None,graph_diagnostic={})
 env=dict(time=NS(monotonic=lambda:0.),snapshot=snap,pad_partitions=lambda *a:[],ordered_nets=lambda c:[('rows',['row']),('columns',['col'])],
  build_field=lambda *a,**kw:dict(complete=True,owned_ids=[],generated_owned_ids=[],roles={}),_field_cutoff=lambda *a:100.,
  matrix_pad_state=lambda *a:dict(missing={'row':1},pad_ids={'r-pad','c-pad'},groups={}),
  KeyboardRouter=router,route_components=lambda *a,**kw:0,repair_feeders=repair)
 fn=load_function('matrix_stage','_prepare_matrix',env)
 result=fn(board,cfg,row_layer=0,column_layer=2,deadline=100.,recovery_deadline=200.,participant_ownership=owner)
 assert not result['complete'] and result['pending_matrix']=={'row':1}
 assert len(calls)==1,'Do not re-enter the repair engine with a stale initial owner'
