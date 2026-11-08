"""A real one-gap matrix retains its frame when borrowing source search time."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import snapshot,p
from smilepcb.keyboard_routing import matrix_stage as stage
from smilepcb.keyboard_routing.field_contract import capture,verify_native
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_phases import ordered_nets
from native_collector_flow_fixture import fixture
original_route,original_repair=stage.route_components,stage.repair_feeders
records={}
for borrow in (False,True):
 b,cfg=fixture(p.B_Cu);before=snapshot(b);chosen=dict(ordered_nets(cfg))['rows'][0];calls=[]
 def partial(board,router,nets,**kw):return original_route(board,router,set(nets)-{chosen},**kw)
 def repair(board,nets,protected,**kw):
  calls.append(kw['deadline'])
  if len(calls)==1:
   missing=stage.matrix_pad_state(board,nets,kw['deadline'])['missing']
   assert missing=={chosen:1},missing
   return [{'before':missing,'accepted':False,'retained_pending':missing,'plan':{'diagnostic':{'attempts':[{'search':{'reason':'search_budget'}}]}}}]
  return original_repair(board,nets,protected,**kw)
 stage.route_components,stage.repair_feeders=partial,repair
 start=time.monotonic();limit=start+70
 try:
  with generation_profile('dsn-safe-v1'):
   kwargs={'recovery_deadline':limit} if borrow else {}
   result=stage.prepare_matrix(b,cfg,row_layer=p.B_Cu,column_layer=p.F_Cu,deadline=start+35,**kwargs)
 finally:stage.route_components,stage.repair_feeders=original_route,original_repair
 proof=False
 if result['complete']:
  verify_native(b,capture(snapshot(b),result),deadline=time.monotonic()+5);proof=True
 records[str(borrow)]={'complete':result['complete'],'proof':proof,'repair_calls':len(calls),'later_window':len(calls)>1 and calls[1]>calls[0], 'within_outer':all(t<=limit for t in calls),'footprints_exact':snapshot(b)['footprints']==before['footprints'],'pending':result.get('pending_matrix')}
Path(sys.argv[1]).write_text(json.dumps(records,indent=2)+'\n')
