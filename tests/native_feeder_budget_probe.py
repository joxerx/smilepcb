"""A short remaining stage budget must not start an already-expired subphase."""
import json,runpy,sys,time
from pathlib import Path
from smilepcb.native_worker import snapshot
from smilepcb.keyboard_routing.feeder_repair import repair_feeders
from smilepcb.keyboard_routing.cleanup import cleanup_generated
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
make=runpy.run_path(str(Path(__file__).with_name('native_field_construction_probe.py')),init_globals={'__name__':'probe'})['make']
b,cfg=make();before=snapshot(b);error=None;result=None
try:result=repair_feeders(b,{'row'},set(before['tracks']),deadline=time.monotonic()+30)
except Exception as e:error=type(e).__name__+': '+str(e)
report={'short_repair':{'error':error,'result':result,'unchanged':snapshot(b)==before,'protected_preserved':snapshot(b)['footprints']==before['footprints'] and all(snapshot(b)['tracks'].get(u)==t for u,t in before['tracks'].items())}}
b,cfg=make();before=snapshot(b);kind=None
try:cleanup_generated(b,protected=set(before['tracks']),deadline=time.monotonic()-1)
except Exception as e:kind=type(e).__name__
report['expired_cleanup']={'exception':kind,'unchanged':snapshot(b)==before}
from types import SimpleNamespace
from smilepcb.keyboard_routing import feeder_repair,native_graph
old_clock,old_pending,old_negotiate=feeder_repair.time,feeder_repair.pending_matrix,native_graph.negotiate_routes
bounds={};now=old_clock.monotonic()
try:
 feeder_repair.time=SimpleNamespace(monotonic=lambda:now)
 feeder_repair.pending_matrix=lambda *args:{'row':1}
 for remaining in (1,25,30,38,39):
  seen=[]
  def observe(board,nets,protected,deadline,**kwargs):
   seen.append(deadline);return {'connected':0,'removed':[]}
  native_graph.negotiate_routes=observe
  b,cfg=make();repair_feeders(b,{'row'},set(snapshot(b)['tracks']),deadline=now+remaining)
  bounds[str(remaining)]=[round(value-now,3) for value in seen]
finally:
 feeder_repair.time,feeder_repair.pending_matrix,native_graph.negotiate_routes=old_clock,old_pending,old_negotiate
report['allocation_boundaries']=bounds
(out/'budget-report.json').write_text(json.dumps(report,indent=2)+'\n')
