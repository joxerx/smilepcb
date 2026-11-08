"""Captured failure and general heuristic/physical-boundary contracts."""
from pathlib import Path
import json
from smilepcb.keyboard_routing.ribbon_port_candidates import port_pairs
F=json.loads((Path(__file__).parent/'fixtures/infeasible_port_projection.json').read_text())

def test_sideways_terminal_does_not_create_impossible_forward_cap():
 assert port_pairs(**F['real'],free=lambda q:True)

def test_actual_occupancy_predicate_still_blocks_candidates():
 assert port_pairs(**F['real'],free=lambda q:False)==[]

def test_compatible_cap_preserves_old_candidate_order():
 actual=port_pairs(**F['feasible'],free=lambda q:True)
 assert actual==[(tuple(a),tuple(z)) for a,z in F['feasible_expected']]

def test_exactly_feasible_cap_is_not_disabled():
 actual=port_pairs(**F['boundary'],free=lambda q:True)
 assert actual==[(tuple(a),tuple(z)) for a,z in F['boundary_expected']]

def test_four_bank_directions_obey_bounds_occupancy_and_minimum_run():
 a=F['real']
 for turns in range(4):
  def rotate(q):
   x,y=q
   for _ in range(turns):x,y=-y,x
   return [x,y]
  corners=[rotate([a['bounds'][i],a['bounds'][j]]) for i in (0,2) for j in (1,3)]
  bounds=[min(q[0] for q in corners),min(q[1] for q in corners),max(q[0] for q in corners),max(q[1] for q in corners)]
  args=dict(a,source=rotate(a['source']),targets=[rotate(q) for q in a['targets']],heading=rotate(a['heading']),bounds=bounds)
  called=[];pairs=port_pairs(**args,free=lambda q:called.append(q) or True)
  assert pairs and len(pairs)<=args['limit']
  projection=lambda q:sum((q[k]-args['source'][k])*args['heading'][k] for k in (0,1))
  for start,end in pairs:
   assert start in called and end in called
   assert all(bounds[k]<=q[k]<=bounds[k+2] for q in (start,end) for k in (0,1))
   assert projection(start)>=args['lead_nm']
   assert projection(end)-projection(start)>=args['minimum_run_nm']
