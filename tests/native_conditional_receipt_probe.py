"""Actual source replacement must survive the outer immutable interface receipt."""
import json,sys,time
from copy import deepcopy
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing import matrix_stage
from smilepcb.keyboard_routing.ordered_controller import prepare_ordered_controller
from smilepcb.keyboard_routing.field_contract import verify_native
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.conditional_geometry import emit_shapes
from native_collector_flow_fixture import fixture

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
b,cfg=fixture(p.B_Cu);original=matrix_stage.prepare_matrix;replacement={}
def replace_source(*args,**kwargs):
    report=original(*args,**kwargs)
    if not report.get('complete'):return report
    ports=deepcopy(report.get('matrix_ports',kwargs['ports']));matrix_nets={q for key in ('rows','columns') for q in dict(__import__('smilepcb.keyboard_phases',fromlist=['ordered_nets']).ordered_nets(cfg))[key]}
    port=next(q for q in ports if q['net'] in matrix_nets and q['added_ids'])
    uid=port['added_ids'][0];state=snapshot(b);shape=state['tracks'][uid]
    new_uid=p.KIID().AsString()
    for item in list(b.GetTracks()):
        if item.m_Uuid.AsString()==uid:b.Delete(item)
    emit_shapes(b,{new_uid:shape})
    for q in ports:
        q['added_ids']=[new_uid if value==uid else value for value in q['added_ids']]
        for key in ('terminal_id','via_id'):
            if q.get(key)==uid:q[key]=new_uid
    report['roles'][new_uid]=report['roles'].pop(uid)
    report['owned_ids']=[new_uid if value==uid else value for value in report['owned_ids']]
    report['matrix_ports']=[q for q in ports if q['net'] in matrix_nets]
    report['source_ids']=[u for q in report['matrix_ports'] for u in q['added_ids']]
    report['retired_source_ids']=sorted(set(report.get('retired_source_ids',[]))|{uid})
    replacement.update(old=uid,new=new_uid)
    return report
matrix_stage.prepare_matrix=replace_source
try:
    with generation_profile('dsn-safe-v1'):
        updated,result=prepare_ordered_controller(b,cfg,deadline=time.monotonic()+35,complete_matrix=True)
finally:matrix_stage.prepare_matrix=original
record={'replacement':replacement,'accepted':result['accepted'],'reason':result.get('reason'),'result':result}
(out/'report.json').write_text(json.dumps(record,indent=2)+'\n')
assert replacement,'Fixture did not exercise native source replacement'
assert result['accepted'],record
assert replacement['old'] not in snapshot(b)['tracks'] and replacement['new'] in snapshot(b)['tracks']
assert any(replacement['new'] in q['added_ids'] for q in result['assignment']['ports'])
verify_native(b,result['matrix_contract'],deadline=time.monotonic()+5)
print(json.dumps({'accepted':True,'replacement':replacement,'actual_receipt_verified':True}))
