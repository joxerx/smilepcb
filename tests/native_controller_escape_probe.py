import importlib.util
import json
from pathlib import Path
import sys
import time

from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.keyboard_routing.local_router import LocalRouter,vec,xy
from smilepcb.keyboard_routing.bank_geometry import port_outside_bounds
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.routing_quality import using_quality_profile

case=sys.argv[1];out=Path(sys.argv[2])
if importlib.util.find_spec('smilepcb.keyboard_routing.controller_escape') is None:
    out.write_text(json.dumps({'available':False,'reason':'Transactional native source escape is not implemented'}));raise SystemExit(0)
from smilepcb.keyboard_routing.controller_escape import route_controller_escapes,_EscapeRouter
select_generation_profile('dsn-process-v2');quality=using_quality_profile('turns');quality.__enter__()
board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,COMPACT_VIAS)
for net in ('sense_a','sense_b','guard'):board.Add(p.NETINFO_ITEM(board,net))
corners=[(0,0),(30000000,0),(30000000,30000000),(0,30000000)]
for a,z in zip(corners,corners[1:]+corners[:1]):
    e=p.PCB_SHAPE(board);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(a));e.SetEnd(vec(z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);board.Add(e)
ref='U_MCU' if case=='contract' else 'CTRL_CUSTOM'
chip=p.FOOTPRINT(board);chip.SetReference(ref);chip.SetPosition(vec((15000000,16500000)));chip.SetLayer(p.F_Cu);board.Add(chip)
def pad(number,net,point):
    q=p.PAD(chip);q.SetNumber(number);q.SetNet(board.FindNet(net));q.SetAttribute(p.PAD_ATTRIB_SMD);q.SetShape(p.PAD_SHAPE_RECT)
    q.SetSize(vec((300000,1400000)));q.SetPosition(vec(point));ls=p.LSET();ls.AddLayer(p.F_Cu);q.SetLayerSet(ls);chip.Add(q);return q
pad('left','sense_a',(14750000,15000000));pad('left','sense_a',(14800000,15000000));pad('right','sense_b',(15250000,15000000))
frame=(12500000,12500000,17500000,18500000)
if case=='zero-neck':
    # Only the two straight normal approaches of sense_a are blocked. The
    # real diagonal boundary search must still route its physical pad aliases.
    for y in (14020000,15980000):
        t=p.PCB_TRACK(board);t.SetStart(vec((14700000,y)));t.SetEnd(vec((14800000,y)))
        t.SetLayer(p.F_Cu);t.SetWidth(150000);t.SetNet(board.FindNet('guard'));board.Add(t)
if case=='neck':
    # This native-clear via consumes a neighbor's reserved approach while a
    # trace on the opposite face does not. No production-only fake is used.
    a=(15250000,15000000);z=(15250000,15977000);neck={'owner':'neighbor','net':'sense_b','layer':p.F_Cu,
        'shape':p.SHAPE_SEGMENT(vec(a),vec(z),150000),'path_nm':[a,z],'direction':'inward'}
    point=(15000000,16250000);routed=set();base=LocalRouter(board,deadline=time.monotonic()+5,compact=True,track_width_nm=150000)
    world=_EscapeRouter(board,deadline=time.monotonic()+5,bounds=frame,necks=[neck],routed=routed,
                        clearance_nm=173000,search_budget_s=1)
    original=base.via_clear(point,'sense_a');blocked=not world.via_clear(point,'sense_a');own=world.via_clear(point,'sense_b')
    opposite=world.clear(p.SHAPE_SEGMENT(vec(a),vec(z),150000),p.B_Cu,'sense_a')
    routed.add('neighbor');released=world.via_clear(point,'sense_a');routed.clear();restored=not world.via_clear(point,'sense_a')
    out.write_text(json.dumps({'available':True,'checks':{'native_via_legal':original,'foreign_via_blocked':blocked,
        'same_net_exempt':own,'opposite_face_lead_clear':opposite,'release':released,'reactivation':restored}}));raise SystemExit(0)
if case in ('blocked','via','contract'):
    box=[(13000000,13000000),(17000000,13000000),(17000000,18000000),(13000000,18000000)]
    for face in ((p.F_Cu,p.B_Cu) if case=='blocked' else (p.F_Cu,)):
        for a,z in zip(box,box[1:]+box[:1]):
            t=p.PCB_TRACK(board);t.SetStart(vec(a));t.SetEnd(vec(z));t.SetLayer(face);t.SetWidth(150000);t.SetNet(board.FindNet('guard'));board.Add(t)
before=snapshot(board);caught=False
if case=='exception':
    original=_EscapeRouter.add_path
    def fault(self,*args,**kwargs):
        original(self,*args,**kwargs);raise RuntimeError('Injected failure after actual native copper emission')
    _EscapeRouter.add_path=fault
try:
    result=route_controller_escapes(board,controller_refs={ref},mcu_refs={ref},
        include_nets={'sense_a','sense_b'},exit_bounds_nm={ref:frame},
        deadline=time.monotonic()+12 if case!='expired' else time.monotonic()-1,
        clearance_nm=173000,search_budget_s=1,max_ripups=2)
except RuntimeError:
    caught=True;result={'accepted':False}
after=snapshot(board);report={'available':True,'accepted':result['accepted'],'unchanged':after==before,
    'caught_exception':caught,'reason':result.get('reason')}
if result['accepted']:
    ports=result['ports'];items={t.m_Uuid.AsString():t for t in board.GetTracks()}
    if case=='contract':
        from smilepcb.keyboard_routing.collector_assignment import collector_assignment
        from smilepcb.keyboard_routing.gpio import apply_exit_assignment
        from smilepcb.keyboard_routing.conditional_banks import certify_generated_heads
        from smilepcb.keyboard_routing.interface_contract import capture_interfaces,verify_interfaces
        cfg={'parts':[{'ref':ref,'region':'controller','nets':{'left':'sense_a','right':'sense_b'},'no_connect':[]}],
            'mcu_gpio_pads':{'A0':'left','B0':'right'},'layout':{'gpio':{'rows':['A0'],'cols':['B0']},'rgb':False},
            'qmk':{'matrix_pins':{'rows':['A0'],'cols':['B0']}},'plane_nets':[],
            'routing_preparation':{'mode':'ordered-banks-v2','generation_profile':'dsn-process-v2'}}
        mapping,plans=collector_assignment(ports,{'sense_a':(25000000,25000000),'sense_b':(26000000,25000000)},
                                            {'sense_a':'rows','sense_b':'columns'})
        updated,assignment=apply_exit_assignment(board,cfg,ports,deadline=time.monotonic()+5,net_mapping=mapping)
        owned=certify_generated_heads(board,updated,before,assignment['ports'],deadline=time.monotonic()+5)
        receipt=capture_interfaces(board,updated,assignment['ports'],deadline=time.monotonic()+5)
        verified=verify_interfaces(board,updated,json.loads(json.dumps(receipt)),deadline=time.monotonic()+5)
        report['contracts']={'collector_plan':bool(plans),'assignment':assignment['accepted'],
            'owned':set(owned.tracks)==set(result['added_ids']),'interface':verified['accepted']}
    graph=copper_graph(board,{'sense_a','sense_b'},time.monotonic()+3,include_zones=False)
    ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
    def outside(q):
        a,z=q['heading_origin_nm'],q['exit_nm'];h=tuple((z[i]>a[i])-(z[i]<a[i]) for i in (0,1))
        return port_outside_bounds(z,h,frame,150000)
    report.update(ports=len(ports),source_aliases=sum(len(q['pad_ids']) for q in ports),
        retained_exact=after['footprints']==before['footprints'] and all(after['tracks'].get(u)==t for u,t in before['tracks'].items()),
        exact_owned_delta=set(after['tracks'])-set(before['tracks'])=={u for q in ports for u in q['added_ids']},
        connected=all(graph['find'](ids[u])==graph['find'](ids[q['terminal_id']]) for q in ports for u in q['pad_ids']),
        real_final_tracks=all(items[q['terminal_id']].GetClass()=='PCB_TRACK' and
            {xy(items[q['terminal_id']].GetStart()),xy(items[q['terminal_id']].GetEnd())}=={tuple(q['heading_origin_nm']),tuple(q['exit_nm'])} for q in ports),
        headings_outside=all(outside(q) for q in ports),via_ports=sum(q['kind']=='via' for q in ports),
        selected_inward_necks=bool(result['selected_necks']) and all(q['direction']=='inward' for q in result['selected_necks']),
        zero_neck_source_retained=any(q['net']=='sense_a' for q in ports) and not any(q['net']=='sense_a' for q in result['selected_necks']))
out.write_text(json.dumps(report,sort_keys=True))
