"""Two-source coupon: real assignment, Native plan, guards, saved closure.

Run explicitly under the Native interpreter; creates only output copies.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,project_settings
from smilepcb.keyboard_routing.controller_plan import certify_source_batch,rebind_source_batch
from smilepcb.keyboard_routing.controller_corridor_plan import plan_controller_corridors
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.interface_launches import reserve_launches
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.native_graph import copper_graph

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
variant=sys.argv[2] if len(sys.argv)>2 else 'original'
if variant not in ('original','translated-renamed','accessible-aux'):raise ValueError('Unknown fixture variant')
shift=(5000000,7000000) if variant=='translated-renamed' else (0,0)
renames={'alpha':'signal-x','beta':'signal-y','USB_DP':'fixed-usb','foreign':'foreign-x'} if variant=='translated-renamed' else {}
def name(net):return renames.get(net,net)
def at(point):return tuple(point[i]+shift[i] for i in (0,1))
def remap(value):
    if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
    if isinstance(value,list):return [remap(v) for v in value]
    return name(value) if isinstance(value,str) else value
signals={name('alpha'),name('beta')};swap={name('alpha'):name('beta'),name('beta'):name('alpha')}

path=out/'controller-corridors.kicad_pcb'
path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')))
board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,'fr4-2l-1oz-0p5-0p2')
for net in ('alpha','beta','USB_DP','foreign'):board.Add(p.NETINFO_ITEM(board,name(net)))
corners=[(0,0),(40000000,0),(40000000,30000000),(0,30000000)]
for a,z in zip(corners,corners[1:]+corners[:1]):
    edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetLayer(p.Edge_Cuts)
    edge.SetStart(vec(at(a)));edge.SetEnd(vec(at(z)));edge.SetWidth(50000);board.Add(edge)
def footprint(ref):
    f=p.FOOTPRINT(board);f.SetReference(ref);f.SetFPID(p.LIB_ID('Fixture','ControllerCorridor'));f.SetPosition(vec(shift));board.Add(f);return f
def pad(f,number,net,point,face):
    d=p.PAD(f);d.SetNumber(number);d.SetNet(board.FindNet(name(net)));d.SetPosition(vec(at(point)))
    d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(vec((600000,300000)))
    layers=p.LSET();layers.AddLayer(face);d.SetLayerSet(layers);f.Add(d);return d
chip=footprint('U_MCU');field=footprint('KEYS');usb=footprint('J_USB')
source_pads=[pad(chip,str(i+1),net,(1000000,3000000+i*500000),p.F_Cu) for i,net in enumerate(('alpha','beta'))]
# The swap makes source order align with the final physical target order.
targets=[pad(field,str(i+1),net,(24000000,9500000-i*500000),p.B_Cu) for i,net in enumerate(('alpha','beta'))]
pad(chip,'3','USB_DP',(5000000,25000000),p.F_Cu);pad(usb,'1','USB_DP',(15000000,25000000),p.F_Cu)
t=p.PCB_TRACK(board);t.SetStart(vec(at((5000000,25000000))));t.SetEnd(vec(at((15000000,25000000))));t.SetWidth(200000);t.SetLayer(p.F_Cu);t.SetNet(board.FindNet(name('USB_DP')));t.SetLocked(True);board.Add(t)
cfg=dict(parts=[dict(ref='U_MCU',region='controller',nets={'1':'alpha','2':'beta','3':'USB_DP'},no_connect=[]),
    dict(ref='KEYS',region='matrix',nets={'1':'alpha','2':'beta'}),dict(ref='J_USB',region='controller',nets={'1':'USB_DP'})],
    mcu_gpio_pads={'A0':'1','A1':'2'},layout=dict(gpio=dict(rows=['A0','A1'],cols=[]),rgb=False),
    plane_nets=[],qmk=dict(matrix_pins=dict(rows=['A0','A1'],cols=[])))
if variant=='accessible-aux':
    board.Add(p.NETINFO_ITEM(board,'AUX_FIXED'))
    auxiliary=footprint('J_AUX')
    pad(chip,'4','AUX_FIXED',(5000000,20000000),p.F_Cu)
    pad(auxiliary,'1','AUX_FIXED',(15000000,20000000),p.F_Cu)
    fixed=p.PCB_TRACK(board);fixed.SetStart(vec(at((5000000,20000000))));fixed.SetEnd(vec(at((15000000,20000000))))
    fixed.SetWidth(200000);fixed.SetLayer(p.F_Cu);fixed.SetNet(board.FindNet('AUX_FIXED'));fixed.SetLocked(True);board.Add(fixed)
    cfg['parts'][0]['nets']['4']='AUX_FIXED'
    cfg['parts'].append(dict(ref='J_AUX',region='auxiliary',nets={'1':'AUX_FIXED'}))
cfg=remap(cfg)
assert p.SaveBoard(str(path),board)
board=p.LoadBoard(str(path));original=snapshot(board);before=deepcopy(original)
pads={d.GetNumber():d for f in board.GetFootprints() if f.GetReference()=='U_MCU' for d in f.Pads()}
ports=[]
for i,net in enumerate((name('alpha'),name('beta'))):
    a=at((1000000,3000000+i*500000));z=(a[0]+1000000,a[1]);head=p.PCB_TRACK(board)
    head.SetStart(vec(a));head.SetEnd(vec(z));head.SetWidth(board.GetDesignSettings().m_TrackMinWidth)
    head.SetLayer(p.F_Cu);head.SetNet(board.FindNet(net));board.Add(head)
    uid=head.m_Uuid.AsString()
    ports.append(dict(net=net,pad_ids=[pads[str(i+1)].m_Uuid.AsString()],added_ids=[uid],terminal_id=uid,
        kind='same_layer',via_id=None,exit_nm=z,heading_origin_nm=a,exit_layer=p.F_Cu,bank=['U_MCU',0,1]))
board.BuildConnectivity();report={'variant':variant,'translation_nm':shift,'net_renames':renames};deadline=time.monotonic()+35
with generation_profile('dsn-process-v2'):
    accessible=[dict(net='AUX_FIXED',pad_ids=[pads['4'].m_Uuid.AsString()])] if variant=='accessible-aux' else []
    requested=signals|({'AUX_FIXED'} if accessible else set())
    batch=certify_source_batch(board,cfg,before,dict(accepted=True,ports=ports,added_ids=[q['terminal_id'] for q in ports],already_accessible=accessible),
        matrix_nets=signals,requested_nets=requested,deadline=deadline)
    # Production assigns before planning. Build a physically real old plan
    # after that assignment, invalidate it with another real safe permutation,
    # then restore the final assignment and build a fresh current plan.
    updated,first_batch,first_assignment=rebind_source_batch(board,cfg,batch,net_mapping=swap,deadline=deadline)
    old_plan=plan_controller_corridors(board,updated,first_batch,deadline=deadline)
    crossed,second_batch,second_assignment=rebind_source_batch(board,updated,first_batch,net_mapping=swap,deadline=deadline)
    try:old_plan.verify(board,crossed,deadline=deadline)
    except ValueError:report['old_plan_rejects_real_assignment']=True
    else:raise AssertionError('Stale plan survived physical source reassignment')
    updated,new_batch,assignment=rebind_source_batch(board,crossed,second_batch,net_mapping=swap,deadline=deadline)
    assert new_batch.requested_nets==batch.requested_nets==frozenset(requested)
    assert new_batch.already_accessible==batch.already_accessible
    assert first_batch.lineage['parent_seal']==batch.seal
    assert second_batch.lineage['parent_seal']==first_batch.seal
    assert new_batch.lineage['parent_seal']==second_batch.seal
    report['issuance_obligations_preserved']=True
    report['assignment_sequence']=['swap-to-aligned','swap-invalidates-old-proof','swap-restores-aligned']
    assigned=list(new_batch.ports)
    plan=plan_controller_corridors(board,updated,new_batch,deadline=deadline)
    assert any(len(group['routes'])==2 for group in plan.receipt['groups']),plan.receipt
    via_records=[r for r in plan.envelopes if r.get('kind')=='via']
    assert via_records and all({q['layer'] for q in via_records if q['center_nm']==r['center_nm'] and q['nets']==r['nets']}=={0,2} for r in via_records)
    report['via_annuli_both_faces']=True
    report['via_dimensions']=dict(width=plan.receipt['via_width_nm'],drill=plan.receipt['drill_nm'])
    with plan.reserve(board,updated,deadline=deadline):
        oracle=LocalRouter(board,compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth)
        lane=plan.receipt['groups'][0]['routes'][0];other=plan.receipt['groups'][0]['routes'][1]['net']
        face,path0=lane['route']['legs'][0];a,z=next((a,z) for a,z in zip(path0,path0[1:]) if max(abs(z[i]-a[i]) for i in (0,1))>1000000)
        midpoint=tuple((a[i]+z[i])//2 for i in (0,1));shape=p.SHAPE_CIRCLE(vec(midpoint),1)
        report['foreign_field_blocked']=not oracle.clear(shape,face,name('foreign'))
        report['owner_allowed']=oracle.clear(shape,face,lane['net'])
        report['peer_forbidden']=not oracle.clear(shape,face,other)
        via=via_records[0];shape=p.SHAPE_CIRCLE(vec(via['center_nm']),1)
        report['future_via_back_face_blocked']=not oracle.clear(shape,p.B_Cu,name('foreign'))
        report['own_bridge_allowed']=oracle.clear(shape,p.B_Cu,via['nets'][0])
        with reserve_launches(assigned):committed=plan.commit(board,updated,deadline=deadline)
    report['commit']=committed
    assert all(report[k] for k in ('foreign_field_blocked','owner_allowed','peer_forbidden','future_via_back_face_blocked','own_bridge_allowed'))
    try:plan.commit(board,updated,deadline=deadline)
    except ValueError:report['one_time']=True
    else:raise AssertionError('Plan emitted twice')
    after=snapshot(board)
    assert all(after['tracks'][u]==q for u,q in original['tracks'].items())
    expected=deepcopy(original['footprints'])
    for f in expected.values():
        if f['ref']=='U_MCU':
            for d in f['pads']:d['net']=swap.get(d['net'],d['net'])
    assert after['footprints']==expected
    report['only_declared_pad_bindings_changed']=True
    assert p.SaveBoard(str(path),board)
    saved=p.LoadBoard(str(path));state=snapshot(saved)
    assert state==after
    graph=copper_graph(saved,signals,deadline,include_zones=False)
    for net in sorted(signals):
        physical=[i for i,n in enumerate(graph['nodes']) if isinstance(n['item'],p.PAD) and n['net']==net and n['item'].GetNumber()]
        assert len(physical)==2 and len({graph['find'](i) for i in physical})==1
    report['saved_actual_pad_closure']=True
    if accessible:
        assert list(new_batch.already_accessible)==accessible
        graph=copper_graph(saved,{'AUX_FIXED'},deadline,include_zones=False)
        physical=[i for i,n in enumerate(graph['nodes']) if isinstance(n['item'],p.PAD) and n['net']=='AUX_FIXED' and n['item'].GetNumber()]
        assert len(physical)==2 and len({graph['find'](i) for i in physical})==1
        report['auxiliary_issuance_preserved']=True
(out/'report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
