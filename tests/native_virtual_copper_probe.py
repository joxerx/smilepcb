import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.virtual_copper import VirtualCopper

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
ns={'__name__':'fixture','__file__':str(Path(__file__))}
code=Path(__file__).with_name('native_joint_bundle_probe.py').read_text().split('result={}')[0]
exec(compile(code,'virtual-fixture','exec'),ns)
b,z=ns['board']()
before=snapshot(b);ignored=set(before['tracks']);w=VirtualCopper(b,ignored_copper=ignored,track_width_nm=150000,deadline=time.monotonic()+10,deferred_zone_ids={z})
baseline=w.checkpoint()
probe=lambda:p.SHAPE_SEGMENT(p.VECTOR2I(2_000_000,8_000_000),p.VECTOR2I(18_000_000,8_000_000),150000)
clear_before=w.clear(probe(),p.F_Cu,'second')
w.add_path([(2_000_000,8_000_000),(18_000_000,8_000_000)],p.F_Cu,'first')
blocks_foreign=not w.clear(probe(),p.F_Cu,'second')
allows_own=w.clear(probe(),p.F_Cu,'first')
w.add_via((10_000_000,8_000_000),'first')
via_on_back=not w.clear(p.SHAPE_CIRCLE(p.VECTOR2I(10_000_000,8_000_000),100000),p.B_Cu,'second')
unchanged_during=snapshot(b)==before
w.restore(baseline)
clear_restored=w.clear(probe(),p.F_Cu,'second')
from smilepcb.keyboard_routing.obstacle_bundles import _connect
from smilepcb.keyboard_routing.bundle_polish import retained_contacts_clear
v=VirtualCopper(b,ignored_copper=ignored,track_width_nm=150000,deadline=time.monotonic()+6,deferred_zone_ids={z})
v.add_path([(2_000_000,10_000_000),(1_000_000,10_000_000),(1_000_000,8_000_000),(10_000_000,8_000_000),(10_000_000,12_000_000)],p.F_Cu,'first')
mark=v.checkpoint();source_ids=set(v.added)
connected=_connect(v,(2_000_000,10_000_000),(18_000_000,10_000_000),p.F_Cu,p.F_Cu,'first',[0,0,20_000_000,15_000_000],time.monotonic()+5)
new_ids=set(v.added)-source_ids;old_items={u:t for u,t in v.track_items().items() if u not in new_ids}
contact_safe=all(retained_contacts_clear(b,[op['start'],op['end']],op['layer'],'first',op['width'],ignored_ids=ignored|new_ids,oracle=v,track_items=old_items) for op in v.operations[mark['operations']:] if op['kind']=='track')
loop_safe=not connected or contact_safe
from smilepcb.keyboard_routing.grid_search import search
g=VirtualCopper(b,ignored_copper=ignored,track_width_nm=150000,deadline=time.monotonic()+5,deferred_zone_ids={z})
g.search_budget_s=4;g.grid_step=250000;admissions=[]
def accept(legs,vias,net):
    yes=any(q[1]>10_500_000 for _,path in legs for q in path);admissions.append(yes);return yes
g.grid_plan_admission=accept
filtered=search(g,(2_000_000,10_000_000),(18_000_000,10_000_000),[p.F_Cu],[p.F_Cu],'first')
filter_safe=filtered and any(op['kind']=='track' and max(op['start'][1],op['end'][1])>10_500_000 for op in g.operations)
from smilepcb.keyboard_routing.obstacle_bundles import _join_clear
j=VirtualCopper(b,ignored_copper=ignored,track_width_nm=150000,deferred_zone_ids={z})
j.add_path([(2_000_000,5_000_000),(2_100_000,5_000_000)],p.F_Cu,'first')
pad_join_allowed=_join_clear(j,[(2_000_000,5_000_000),(3_000_000,5_000_000)],p.F_Cu,'first')
(out/'result.json').write_text(json.dumps(dict(clear_before=clear_before,blocks_foreign=blocks_foreign,allows_own=allows_own,via_on_back=via_on_back,unchanged_during=unchanged_during,unchanged_after=snapshot(b)==before,clear_restored=clear_restored,operations_after_restore=len(w.operations),loop_safe=loop_safe,loop_connected=connected,filter_safe=filter_safe,admissions=admissions,pad_join_allowed=pad_join_allowed))+'\n')
