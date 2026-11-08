"""Two distinct same-net source roots; Native execution is root-only."""
import json
from pathlib import Path
import sys
import time
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,project_settings
from smilepcb.keyboard_routing.controller_plan import certify_source_batch
from smilepcb.keyboard_routing.controller_corridor_plan import plan_controller_corridors
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.local_router import vec
from smilepcb.keyboard_routing.native_graph import copper_graph

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
path=out/'same-net-roots.kicad_pcb';path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')))
b=p.BOARD();b.SetCopperLayerCount(2);apply_native_rules(b,'fr4-2l-1oz-0p5-0p2')
for net in ('shared-function','USB_FIXED'):b.Add(p.NETINFO_ITEM(b,net))
corners=[(0,0),(40000000,0),(40000000,30000000),(0,30000000)]
for a,z in zip(corners,corners[1:]+corners[:1]):
    edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetLayer(p.Edge_Cuts);edge.SetStart(vec(a));edge.SetEnd(vec(z));edge.SetWidth(50000);b.Add(edge)
def footprint(ref):
    f=p.FOOTPRINT(b);f.SetReference(ref);f.SetFPID(p.LIB_ID('Fixture','SourceIdentity'));b.Add(f);return f
def pad(f,number,net,point):
    d=p.PAD(f);d.SetNumber(number);d.SetNet(b.FindNet(net));d.SetPosition(vec(point));d.SetAttribute(p.PAD_ATTRIB_SMD)
    d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(vec((600000,300000)));layers=p.LSET();layers.AddLayer(p.F_Cu);d.SetLayerSet(layers);f.Add(d);return d
def track(net,a,z,*,locked=False):
    item=p.PCB_TRACK(b);item.SetStart(vec(a));item.SetEnd(vec(z));item.SetLayer(p.F_Cu);item.SetWidth(b.GetDesignSettings().m_TrackMinWidth);item.SetNet(b.FindNet(net));item.SetLocked(locked);b.Add(item);return item
chip=footprint('U_MCU');local=footprint('R_LOCAL');external=footprint('J_EXT');usb=footprint('J_USB')
pad(chip,'1','shared-function',(5000000,5000000));pad(chip,'2','shared-function',(20000000,15000000))
pad(local,'1','shared-function',(15000000,3000000));pad(external,'1','shared-function',(15000000,10000000))
pad(chip,'3','USB_FIXED',(4000000,25000000));pad(usb,'1','USB_FIXED',(20000000,25000000))
track('shared-function',(15000000,3000000),(15000000,10000000),locked=True)
track('USB_FIXED',(4000000,25000000),(20000000,25000000),locked=True)
cfg=dict(parts=[dict(ref='U_MCU',region='controller',nets={'1':'shared-function','2':'shared-function','3':'USB_FIXED'}),
    dict(ref='R_LOCAL',region='controller',nets={'1':'shared-function'}),dict(ref='J_EXT',region='auxiliary',nets={'1':'shared-function'}),
    dict(ref='J_USB',region='controller',nets={'1':'USB_FIXED'})],plane_nets=[],layout=dict(gpio=dict(rows=[],cols=[]),rgb=False),mcu_gpio_pads={},qmk=dict(matrix_pins=dict(rows=[],cols=[])))
assert p.SaveBoard(str(path),b)
b=p.LoadBoard(str(path));before=snapshot(b)
pads={d.GetNumber():d for f in b.GetFootprints() if f.GetReference()=='U_MCU' for d in f.Pads()}
ports=[]
for number,a,z in [('1',(5000000,5000000),(7000000,5000000)),('2',(20000000,15000000),(20000000,13000000))]:
    head=track('shared-function',a,z);uid=head.m_Uuid.AsString()
    ports.append(dict(net='shared-function',pad_ids=[pads[number].m_Uuid.AsString()],added_ids=[uid],terminal_id=uid,
        exit_nm=z,heading_origin_nm=a,exit_layer=p.F_Cu,bank=None,kind='same_layer',via_id=None))
b.BuildConnectivity();entry=snapshot(b);deadline=time.monotonic()+20
with generation_profile('dsn-process-v2'):
    batch=certify_source_batch(b,cfg,before,dict(accepted=True,ports=ports,added_ids=[q['terminal_id'] for q in ports]),
        matrix_nets=set(),requested_nets={'shared-function'},deadline=deadline)
    plan=plan_controller_corridors(b,cfg,batch,deadline=deadline)
    assert snapshot(b)==entry
    routes=[row for group in plan.receipt['groups'] for row in group['routes']]
    expected={tuple(sorted(q['pad_ids'])) for q in ports}
    assert len(routes)==2 and {tuple(row['source_key']) for row in routes}==expected
    assert {tuple(row['heading']) for row in routes}=={(1,0),(0,-1)}
    assert all(row['net']=='shared-function' for row in routes)
    for q in plan.binding['ports']:
        delta=tuple(q['exit_nm'][i]-q['heading_origin_nm'][i] for i in (0,1))
        step=tuple(q['departure_waypoint_nm'][i]-q['exit_nm'][i] for i in (0,1))
        assert delta[0]*step[1]==delta[1]*step[0] and sum(delta[i]*step[i] for i in (0,1))>0
    result=plan.commit(b,cfg,deadline=deadline)
    after=snapshot(b);assert after['footprints']==before['footprints']
    assert all(after['tracks'][uid]==shape for uid,shape in entry['tracks'].items())
    assert p.SaveBoard(str(path),b)
    saved=p.LoadBoard(str(path));assert snapshot(saved)==after
    graph=copper_graph(saved,{'shared-function'},deadline,include_zones=False)
    physical=[i for i,n in enumerate(graph['nodes']) if isinstance(n['item'],p.PAD) and n['item'].GetNumber()]
    assert len(physical)==4 and len({graph['find'](i) for i in physical})==1
report=dict(accepted=result['accepted'],same_net_roots=2,source_keys=[list(key) for key in sorted(expected)],
    independent_headings=True,local_and_external_targets=True,saved_all_four_pad_closure=True,usb_and_source_exact=True,plan=result)
(out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
