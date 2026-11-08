import json,sys,time
from pathlib import Path
from native_collector_flow_fixture import fixture
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.ordered_controller import prepare_ordered_controller
from smilepcb.keyboard_routing import matrix_stage,field_contract
from smilepcb.keyboard_routing import collectors as planner
from smilepcb.keyboard_routing.collector_capacity import groups_for
from smilepcb.keyboard_routing.collector_peel import compress
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_process import apply_native_rules

records={}
b,cfg=fixture(p.B_Cu);before=snapshot(b)
from smilepcb.keyboard_routing.feeder_repair import repair_feeders
try:
    result=repair_feeders(b,{'r0','r1','c0','c1'},set(),deadline=time.monotonic()-1)
    records['expired-repair']={'history':result,'unchanged':snapshot(b)==before,'error':None}
except TimeoutError as error:records['expired-repair']={'error':str(error),'unchanged':snapshot(b)==before}
b,cfg=fixture(p.B_Cu);apply_native_rules(b,'fr4-2l-1oz-0p5-0p2')
w=LocalRouter(b,deadline=time.monotonic()+5,compact=True,track_width_nm=150000)
records['profile']={'diameter':w.via_width,'drill':w.drill}

b,cfg=fixture(p.B_Cu);before=snapshot(b);old_verify=field_contract.verify_native
field_contract.verify_native=lambda *a,**k:(_ for _ in ()).throw(TimeoutError('Injected final receipt proof expiration'))
try:
 with generation_profile('dsn-safe-v1'):_,r=prepare_ordered_controller(b,cfg,deadline=time.monotonic()+35,complete_matrix=True)
finally:field_contract.verify_native=old_verify
records['late-proof']={'accepted':r['accepted'],'unchanged':snapshot(b)==before,'has_receipt':'matrix_contract' in r}

b,cfg=fixture(p.B_Cu);original_matrix=matrix_stage.prepare_matrix
with generation_profile('dsn-safe-v1'):
 # Native source preparation is isolated from the matrix test itself.
 matrix_stage.prepare_matrix=lambda *a,**k:{'complete':False,'reason':'source preparation only'}
 try:_,prepared=prepare_ordered_controller(b,cfg,deadline=time.monotonic()+35,complete_matrix=True)
 finally:matrix_stage.prepare_matrix=original_matrix
 # The rejected transaction correctly rolled back; create fresh actual source
 # interfaces without requiring the old radial bundle sampler.
 from smilepcb.keyboard_routing.gpio import apply_matrix_gpio,apply_exit_assignment
 from smilepcb.keyboard_routing.controller_ports import route_controller_ports
 cfg,_=apply_matrix_gpio(b,cfg,banked=True)
 source=route_controller_ports(b,controller_refs={'U_MCU'},mcu_refs={'U_MCU'},include_nets={'r0','r1','c0','c1'},access_mode='prefer_same_layer',continuation_nm=500000,deadline=time.monotonic()+8)
 cfg,assignment=apply_exit_assignment(b,cfg,source['ports'],deadline=time.monotonic()+8);ports=assignment['ports']
 expected=set();observations=[];old_plan=planner.route_collectors;old_route=matrix_stage.route_components
 def advanced(board,config,live,*,deadline,field_owned_ids=()):
  original={t.m_Uuid.AsString() for t in board.GetTracks()}
  for group in groups_for(board,config,live):
   if len(group)>1:
    live,r=compress(board,live,group,deadline=deadline)
    if r['accepted']:expected.update(u for q in live if q['net'] in group for u in q['added_ids'])
  return {'accepted':False,'closed_nets':[],'ports':live,'history':[],'added_ids':sorted({t.m_Uuid.AsString() for t in board.GetTracks()}-original)}
 def observe(board,router,nets,**kwargs):
  observations.append({'policy':expected<=router.port_policy.copper,'launch':router.launch_policy is not None and expected<=router.launch_policy.copper})
  return old_route(board,router,nets,**kwargs)
 planner.route_collectors=advanced;matrix_stage.route_components=observe
 try:r=matrix_stage.prepare_matrix(b,cfg,row_layer=p.B_Cu,column_layer=p.F_Cu,deadline=time.monotonic()+35,ports=ports,collectors=True)
 finally:planner.route_collectors=old_plan;matrix_stage.route_components=old_route
 records['live-ports']={'advanced':bool(expected),'observations':observations,'complete':r['complete']}
Path(sys.argv[1]).write_text(json.dumps(records,indent=2)+'\n')
