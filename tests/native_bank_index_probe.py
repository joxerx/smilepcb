"""Execute the actual conditional feeder loop with native pads, tracks and holes."""
import argparse,ast,json,math,time
from copy import deepcopy
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing import conditional_banks as banks
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.conditional_geometry import FrozenProofRouter,saved_via_clear_cached
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory,interface_identity
from smilepcb.routing_quality import using_quality_profile


def v(x,y):return p.VECTOR2I(round(x*1000000),round(y*1000000))


def fixture(high_clearance=False):
    board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,COMPACT_VIAS)
    corners=[(0,0),(20,0),(20,20),(0,20)]
    for a,z in zip(corners,corners[1:]+corners[:1]):
        edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(v(*a));edge.SetEnd(v(*z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
    controller=p.FOOTPRINT(board);controller.SetReference('U_MCU');controller.SetLayer(p.F_Cu);board.Add(controller)
    ports=[];pads={}
    def pad(fp,number,at,size,net,through=False):
        item=p.PAD(fp);item.SetNumber(number);item.SetPosition(v(*at));item.SetSize(v(*size));item.SetNet(board.FindNet(net))
        item.SetShape(p.PAD_SHAPE_CIRCLE if through else p.PAD_SHAPE_RECT)
        item.SetAttribute(p.PAD_ATTRIB_PTH if through else p.PAD_ATTRIB_SMD)
        if through:item.SetDrillSize(v(.3,.3));item.SetLayerSet(p.LSET.AllCuMask())
        else:
            layers=p.LSET();layers.AddLayer(p.F_Cu);item.SetLayerSet(layers)
        fp.Add(item);return item
    for i,name in enumerate(('alpha','beta','gamma','delta')):
        board.Add(p.NETINFO_ITEM(board,name));y=4+i*3
        source=pad(controller,str(i+1),(2,y),(.4,.4),name)
        if high_clearance and i==0:source.SetLocalClearance(400000)
        fp=p.FOOTPRINT(board);fp.SetReference('FIELD'+str(i));board.Add(fp)
        target=pad(fp,'1',(17,y),(.9,.9),name,True);pads[name]=[source,target]
        track=p.PCB_TRACK(board);track.SetStart(v(2,y));track.SetEnd(v(4,y));track.SetWidth(150000);track.SetLayer(p.F_Cu);track.SetNet(board.FindNet(name));board.Add(track)
        uid=track.m_Uuid.AsString();ports.append({'net':name,'kind':'same_layer','pad_ids':[source.m_Uuid.AsString()],
            'added_ids':[uid],'terminal_id':uid,'via_id':None,'via_nm':None,'exit_nm':[4000000,round(y*1000000)],
            'heading_origin_nm':[2000000,round(y*1000000)],'exit_layer':p.F_Cu})
    board.Add(p.NETINFO_ITEM(board,'wall'));wall=p.FOOTPRINT(board);wall.SetReference('WALL');board.Add(wall)
    # The F.Cu wall reaches the edge-clearance boundary: a real via is needed.
    pad(wall,'1',(10,10),(.5,19.6),'wall')
    return board,ports,pads


def load_loop(namespace):
    path=Path(banks.__file__);tree=ast.parse(path.read_text())
    loops=[node for node in ast.walk(tree) if isinstance(node,ast.For) and isinstance(node.target,ast.Tuple)
           and [getattr(q,'id',None) for q in node.target.elts]==['grid','allowance']]
    assert len(loops)==1,'Test must execute the single production feeder-grid loop'
    args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=name) for name in ('check','selected','feeder_ports','width','stop','row')],vararg=None,kwonlyargs=[],kw_defaults=[],kwarg=None,defaults=[])
    function=ast.FunctionDef(name='run',args=args,body=[deepcopy(loops[0])],decorator_list=[])
    module=ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))
    exec(compile(module,str(path),'exec'),namespace)
    return namespace['run']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('case',choices=['connected','clearance','lifecycle','setup-expiry']);parser.add_argument('output',type=Path);args=parser.parse_args()
    select_generation_profile('dsn-process-v2');board,ports,pads=fixture(args.case=='clearance');before=snapshot(board)
    stop=time.monotonic()+25;worlds=[];calls=[];emitted={};parity=[];deferred=False
    real_router=banks.KeyboardRouter;real_route=banks.route_components
    class ObservedRouter(real_router):
        def __init__(self,*a,**kw):
            super().__init__(*a,**kw);worlds.append(self)
            if args.case=='setup-expiry':self.deadline=time.monotonic()-1
    def route(current,router,nets,**kw):
        nonlocal deferred
        net=next(iter(nets));record={'net':net,'grid_nm':router.grid_step,'world':worlds.index(router)};calls.append(record)
        if args.case=='setup-expiry':raise AssertionError('Expired constructed native world must not enter route_components')
        live=next(q for q in ports if q['net']==net);tip=tuple(live['exit_nm'])
        assert interface_identity(live) not in router.port_policy.resolved
        assert not router.port_policy.accepts_segment(tip,(tip[0]-500000,tip[1]),p.F_Cu,net,150000)
        if emitted:
            tracks={t.m_Uuid.AsString():t for t in board.GetTracks()}
            prior=next(name for name in emitted if name!=net)
            wires=[tracks[uid] for uid in emitted[prior] if not isinstance(tracks[uid],p.PCB_VIA)]
            vias=[tracks[uid] for uid in emitted[prior] if isinstance(tracks[uid],p.PCB_VIA)]
            assert wires and vias,'Fixture must emit actual track and via before the next net'
            wire=max(wires,key=lambda item:item.GetLength());via=vias[0]
            assert not router.clear(p.SHAPE_SEGMENT(wire.GetStart(),wire.GetEnd(),wire.GetWidth()),wire.GetLayer(),net)
            assert not router.via_clear((via.GetPosition().x,via.GetPosition().y),net)
            key=interface_identity(next(q for q in ports if q['net']==prior))
            assert key in current_launch_inventory().resolved and key in router.port_policy.resolved
            record['prior_foreign_track_and_drill_blocked']=True;record['prior_launch_native_resolved']=True
        if args.case=='lifecycle' and net=='alpha' and router.grid_step==500000 and not deferred:
            # Scheduling-only seam: native geometry is never fabricated/cleared.
            deferred=True;router.graph_diagnostic={'attempts':[],'controlled_coarse_deferral':True};record['controlled_deferral']=True;return 0
        old={t.m_Uuid.AsString() for t in current.GetTracks()}
        count=real_route(current,router,nets,**kw);record['connections']=count
        assert count==1,(record,router.graph_diagnostic)
        emitted[net]={t.m_Uuid.AsString() for t in current.GetTracks()}-old
        assert emitted[net]
        if net=='alpha':
            items={t.m_Uuid.AsString():t for t in current.GetTracks()}
            wires=[items[uid] for uid in emitted[net] if not isinstance(items[uid],p.PCB_VIA)]
            wire=max(wires,key=lambda item:item.GetLength());a=wire.GetStart();z=wire.GetEnd();dx,dy=z.x-a.x,z.y-a.y;length=math.hypot(dx,dy)
            assert length>4000000
            fresh=LocalRouter(board,deadline=stop,compact=True,track_width_nm=router.track_width)
            # Existing incremental indexing can conservatively retain174µm
            # where a fresh index has173µm. Do not normalize either oracle.
            seen=[]
            for offset in (0,323500,600000):
                mid=((a.x+z.x)//2,(a.y+z.y)//2);point=(round(mid[0]-dy/length*offset),round(mid[1]+dx/length*offset))
                shape=p.SHAPE_SEGMENT(p.VECTOR2I(*point),p.VECTOR2I(*point),150000)
                cached_ok=router.clear(shape,wire.GetLayer(),'beta');fresh_ok=fresh.clear(shape,wire.GetLayer(),'beta')
                assert not cached_ok or fresh_ok,'Incremental rules must not become weaker than the fresh oracle'
                if offset in (0,600000):assert cached_ok==fresh_ok
                seen.append({'offset_nm':offset,'cached_clear':cached_ok,'fresh_clear':fresh_ok})
            assert router.net_clearances['alpha']>=fresh.net_clearances['alpha']>=173000
            if args.case=='clearance':assert router.net_clearances['alpha']==fresh.net_clearances['alpha']>=400000
            parity.append({'cached_net_clearance_nm':router.net_clearances['alpha'],'fresh_net_clearance_nm':fresh.net_clearances['alpha'],'queries':seen})
        return count
    namespace=dict(banks.__dict__,KeyboardRouter=ObservedRouter,route_components=route);run=load_loop(namespace)
    rows=[]
    with reserve_launches(ports):
        with using_quality_profile('turns'):
            selections=[{'alpha','beta'},{'gamma','delta'}] if args.case=='lifecycle' else [{'alpha'}] if args.case=='setup-expiry' else [{'alpha','beta'}]
            for selected in selections:
                row={'routing_history':[]};run(board,selected,[q for q in ports if q['net'] in selected],150000,stop,row);rows.append(row)
        if args.case=='setup-expiry':
            assert not calls and snapshot(board)==before
            assert not current_launch_inventory().resolved
        else:
            expected={'alpha','beta','gamma','delta'} if args.case=='lifecycle' else {'alpha','beta'}
            graph=copper_graph(board,expected,stop,include_zones=False);ids={q['item'].m_Uuid.AsString():i for i,q in enumerate(graph['nodes']) if not q['zone']}
            for net in expected:assert len({graph['find'](ids[d.m_Uuid.AsString()]) for d in pads[net]})==1
            assert all(interface_identity(q) in current_launch_inventory().resolved for q in ports if q['net'] in expected)
            with reserve_launches([]):
                proof=FrozenProofRouter(board,deadline=stop,track_width_nm=150000,graph=graph,covered_nets=expected)
                for item in board.GetTracks():
                    if item.m_Uuid.AsString() in before['tracks']:continue
                    if isinstance(item,p.PCB_VIA):assert saved_via_clear_cached(proof,item)
                    else:assert proof.clear(item.GetEffectiveShape(item.GetLayer()),item.GetLayer(),item.GetNetname())
                proof.assert_unchanged()
    after=snapshot(board);assert before['footprints']==after['footprints'] and all(after['tracks'].get(uid)==value for uid,value in before['tracks'].items())
    expected_worlds=3 if args.case=='lifecycle' else 2 if args.case=='setup-expiry' else 1
    result={'case':args.case,'worlds':len(worlds),'expected_worlds':expected_worlds,'calls':calls,'rows':rows,'parity':parity,
        'retained_exact':True,'native_connected':args.case!='setup-expiry','launch_refreshed':args.case!='setup-expiry'}
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    assert len(worlds)==expected_worlds,result
    if args.case=='lifecycle':assert calls[-1]['world']==calls[-2]['world'] and calls[-1]['world']!=calls[2]['world']
    print(json.dumps({'case':args.case,'worlds':len(worlds),'passed':True}),flush=True)


if __name__=='__main__':main()
