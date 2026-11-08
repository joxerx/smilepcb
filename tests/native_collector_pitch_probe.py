"""Native pitch candidates are atomic across expiry and source retirement."""
import argparse,json,time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.keyboard_routing import collector_peel as module
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.port_policy import PortPolicy
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory,interface_identity


def v(x,y):return p.VECTOR2I(round(x*1000000),round(y*1000000))


def fixture(wide=False):
    board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,COMPACT_VIAS)
    corners=[(0,0),(20,0),(20,20),(0,20)]
    for a,z in zip(corners,corners[1:]+corners[:1]):
        edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(v(*a));edge.SetEnd(v(*z));edge.SetWidth(50000);edge.SetLayer(p.Edge_Cuts);board.Add(edge)
    ports=[]
    for i in range(4):
        name='signal'+str(i);net=p.NETINFO_ITEM(board,name);board.Add(net)
        x,y=(5+i*(1 if wide else .5),7) if i<3 else (14,4)
        fp=p.FOOTPRINT(board);fp.SetReference('P'+str(i));fp.SetPosition(v(x,y));board.Add(fp)
        pad=p.PAD(fp);pad.SetNumber('1');pad.SetAttribute(p.PAD_ATTRIB_SMD);pad.SetShape(p.PAD_SHAPE_RECT);pad.SetSize(v(.3,.7));pad.SetPosition(v(x,y));pad.SetNet(net)
        layers=p.LSET();layers.AddLayer(p.B_Cu);pad.SetLayerSet(layers);fp.Add(pad)
        track=p.PCB_TRACK(board);track.SetStart(v(x,y));track.SetEnd(v(x,y+1));track.SetWidth(150000);track.SetLayer(p.B_Cu);track.SetNet(net);board.Add(track)
        uid=track.m_Uuid.AsString()
        ports.append({'net':name,'kind':'same_layer','pad_ids':[pad.m_Uuid.AsString()],'added_ids':[uid],
                      'terminal_id':uid,'via_id':None,'via_nm':None,'exit_nm':[track.GetEnd().x,track.GetEnd().y],
                      'heading_origin_nm':[track.GetStart().x,track.GetStart().y],'exit_layer':p.B_Cu})
    # Earlier accepted foreign copper is outside this candidate's ownership.
    via=p.PCB_VIA(board);via.SetPosition(v(16,4));via.SetWidth(500000);via.SetDrill(200000);via.SetViaType(p.VIATYPE_THROUGH);via.SetLayerPair(p.F_Cu,p.B_Cu);via.SetNet(board.FindNet('signal3'));board.Add(via)
    return board,ports


def inventory_state():
    inventory=current_launch_inventory()
    return deepcopy((inventory.ports,inventory.resolved,inventory.repair_reserved))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('case',choices=['positive','retained','expired-entry','expired-retained','expired-plan','timeout-one','timeout-two','expired-final','value-retry','exception']);ap.add_argument('output',type=Path);args=ap.parse_args()
    select_generation_profile('dsn-process-v2');board,ports=fixture(args.case in ('retained','expired-retained'))
    before=snapshot(board);input_ports=deepcopy(ports);nets=[q['net'] for q in ports[:3]]
    original_router=module.LocalRouter;original_path=LocalRouter.add_path;original_transition=module.pitch_transition;original_time=module.time
    now=time.monotonic();deadline=now+10;clock=[now];constructors=[];emitted=[];policies=[];result={};faulted=[False]
    if args.case in ('expired-entry','expired-retained'):deadline=now-1
    if args.case in ('expired-plan','expired-final'):module.time=SimpleNamespace(monotonic=lambda:clock[0])
    def router(*a,**k):
        world=original_router(*a,**k);constructors.append(world);return world
    def transition(*a,**k):
        paths=original_transition(*a,**k)
        if args.case=='expired-plan':clock[0]=deadline+1
        return paths
    def dirty(world,net,path):
        inventory=current_launch_inventory();key=interface_identity(next(q for q in ports if q['net']==net))
        inventory.ports[key]['exit_nm']=list(path[-1]);inventory.ports[key]['added_ids'].extend(world.added)
        inventory.resolved.add(key);inventory.repair_reserved.add(key)
        world.port_policy.set_resolved(world.port_policy.resolved|{key})
    def add_path(world,path,layer,net):
        if faulted[0] and args.case=='value-retry':
            assert inventory_state()==entry,'A rejected candidate leaked launch inventory into its retry'
            assert all(world.port_policy is not policy for policy in policies),'Retry reused a retired native source policy'
        value=original_path(world,path,layer,net);emitted.append(list(world.added));count=len(emitted)
        trigger=(args.case in ('value-retry','exception','timeout-one') and count==1 or args.case=='timeout-two' and count==2)
        if trigger and not faulted[0]:
            dirty(world,net,path);policies.append(world.port_policy);faulted[0]=True
            if args.case=='value-retry':raise ValueError('Injected rejected pitch after native lane')
            if args.case=='exception':raise RuntimeError('Injected unexpected pitch failure')
            world.deadline=time.monotonic()-1
            world._refresh_source_net(net)  # The real native caller's observed timeout.
            raise AssertionError('Expired source refresh did not reject')
        if args.case=='expired-final' and count==3:clock[0]=deadline+1
        return value
    module.LocalRouter=router;module.pitch_transition=transition;LocalRouter.add_path=add_path
    with reserve_launches(ports):
        entry=inventory_state()
        try:
            try:
                updated,report=module.compress(board,ports,nets,deadline=deadline,preserve_portal_pitch=args.case in ('retained','expired-retained'))
            except RuntimeError as error:
                if args.case!='exception' or str(error)!='Injected unexpected pitch failure':raise
                result['exception']=str(error);updated=ports;report={'accepted':False}
        finally:
            module.LocalRouter=original_router;module.pitch_transition=original_transition;module.time=original_time;LocalRouter.add_path=original_path
        assert ports==input_ports,'Pitch transition mutated caller-owned port records'
        after=snapshot(board)
        assert before['footprints']==after['footprints'] and all(after['tracks'].get(uid)==shape for uid,shape in before['tracks'].items())
        if args.case in ('positive','retained','value-retry'):
            assert report['accepted'],report
            graph=copper_graph(board,nets,time.monotonic()+5,include_zones=False)
            ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes']) if not n['zone']}
            world=LocalRouter(board,deadline=time.monotonic()+5,compact=True,track_width_nm=150000)
            policy=PortPolicy(board,updated,track_width_nm=world.track_width);world.port_policy=policy
            for q in updated[:3]:
                assert len({graph['find'](ids[uid]) for uid in q['pad_ids']+[q['terminal_id']]})==1
                a=tuple(q['exit_nm']);z=(a[0],a[1]+500000)
                assert policy.accepts_segment(a,z,q['exit_layer'],q['net'],world.track_width)
                assert world.clear(p.SHAPE_SEGMENT(p.VECTOR2I(*a),p.VECTOR2I(*z),world.track_width),q['exit_layer'],q['net'])
            with reserve_launches([]):
                physical=LocalRouter(board,deadline=time.monotonic()+5,compact=True,track_width_nm=150000)
                for track in board.GetTracks():
                    if track.m_Uuid.AsString() not in before['tracks']:
                        assert physical.clear(p.SHAPE_SEGMENT(track.GetStart(),track.GetEnd(),track.GetWidth()),track.GetLayer(),track.GetNetname())
            assert inventory_state()==entry
            if args.case=='retained':assert after==before and not emitted
            if args.case=='value-retry':assert faulted[0] and not set(emitted[0])&set(after['tracks'])
            result.update(native_graph=True,native_geometry=True,continuations=True)
        else:
            assert not report['accepted'],report
            assert updated is ports and after==before,'Rejected pitch must restore exact entry copper and return original ports'
            assert inventory_state()==entry,'Rejected pitch must restore ports, resolved and repair reservations'
            if args.case!='exception':assert report.get('budget_exhausted') is True,report
            if args.case in ('expired-entry','expired-retained'):assert not constructors
            if args.case=='expired-plan':assert not emitted
            if args.case=='timeout-one':assert len(emitted)==1
            if args.case=='timeout-two':assert len(emitted)==2
            if args.case=='expired-final':assert len(emitted)==3
            result['rollback_exact']=True
        result.update(report=report,retained_exact=True,launch_inventory_exact=True,emitted_lanes=len(emitted),constructors=len(constructors))
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'case':args.case,'passed':True,'emitted_lanes':len(emitted)}),flush=True)


if __name__=='__main__':main()
