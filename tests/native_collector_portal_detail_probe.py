"""Paired conservative-envelope controls on real two-lane native geometry."""
import json,math,os,sys,time,types
from copy import deepcopy
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.collector_capacity import Search,band,shifted
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.interface_launches import reserve_launches
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.port_policy import PortPolicy

# Optional exact pinned source for a matched RED control; native geometry and
# clearance APIs are unchanged, as in the other collector comparison probes.
if os.environ.get('SMILEPCB_PORTAL_CAPACITY_SOURCE'):
    source=Path(os.environ['SMILEPCB_PORTAL_CAPACITY_SOURCE'])
    control=types.ModuleType('smilepcb.keyboard_routing._portal_capacity_control');control.__package__='smilepcb.keyboard_routing'
    exec(compile(source.read_text(),str(source),'exec'),control.__dict__);Search=control.Search


def fixture(face,case):
    board=p.BOARD();board.SetCopperLayerCount(2);rules=board.GetDesignSettings()
    rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000;rules.m_MinThroughDrill=200000
    rules.m_ViasMinSize=500000;rules.m_ViasMinAnnularWidth=150000;rules.m_CopperEdgeClearance=200000
    for net in ('lower','upper','obstacle','foreign'):board.Add(p.NETINFO_ITEM(board,net))
    other=p.B_Cu if face==p.F_Cu else p.F_Cu;obstacles=[]
    def xy(a):return tuple(round(x*1e6) for x in a)
    def trace(net,a,z,layer,width=150000):
        item=p.PCB_TRACK(board);item.SetStart(vec(xy(a)));item.SetEnd(vec(xy(z)));item.SetWidth(width);item.SetLayer(layer);item.SetNet(board.FindNet(net));board.Add(item)
        uid=item.m_Uuid.AsString()
        if net=='obstacle':obstacles.append(uid)
        return uid
    def pad(net,position,layer,number):
        fp=p.FOOTPRINT(board);fp.SetReference('P'+number);board.Add(fp)
        item=p.PAD(fp);item.SetNumber(number);item.SetAttribute(p.PAD_ATTRIB_SMD);item.SetShape(p.PAD_SHAPE_RECT)
        item.SetSize(vec((300000,150000)));item.SetPosition(vec(xy(position)));item.SetNet(board.FindNet(net))
        layers=p.LSET();layers.AddLayer(layer);item.SetLayerSet(layers);fp.Add(item);return item.m_Uuid.AsString()
    corners=[(0,0),(30000000,0),(30000000,30000000),(0,30000000)]
    for a,z in zip(corners,corners[1:]+corners[:1]):
        edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vec(a));edge.SetEnd(vec(z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
    ports=[]
    for i,(net,y) in enumerate((('lower',10),('upper',10.6))):
        source=pad(net,(9,y),face,str(i+1));track=trace(net,(9,y),(10,y),face)
        ports.append({'net':net,'kind':'same_layer','pad_ids':[source],'added_ids':[track],'terminal_id':track,'via_id':None,'via_nm':None,
                      'exit_nm':xy((10,y)),'heading_origin_nm':xy((9,y)),'exit_layer':face})
        pad(net,(17,y),other,str(i+3))
    if case in ('unused_entry','rollback'):trace('obstacle',(12.05,10),(12.15,10),other)
    if case=='unused_exit':trace('obstacle',(14.85,10),(14.95,10),face)
    if case=='track':trace('obstacle',(12.25,9.95),(12.25,10.05),face)
    if case=='via_opposite':trace('obstacle',(12.95,10),(13.05,10),other)
    if case=='same_net_land':pad('lower',(13,10),other,'5')
    foreign=None
    if case in ('current_launch','future_launch'):
        source=pad('foreign',(12.5,9),face,'6');track=trace('foreign',(12.5,9),(12.5,9.6),face)
        foreign={'net':'foreign','kind':'same_layer','pad_ids':[source],'added_ids':[track],'terminal_id':track,'via_id':None,'via_nm':None,
                 'exit_nm':xy((12.5,9.6)),'heading_origin_nm':xy((12.5,9)),'exit_layer':face}
    return board,ports,foreign,obstacles


def connected(board):
    graph=copper_graph(board,{'lower','upper'},time.monotonic()+1)
    return all(len({graph['find'](i) for i,node in enumerate(graph['nodes']) if node['net']==net and isinstance(node['item'],p.PAD)})==1 for net in ('lower','upper'))


def probe(out,face,case):
    board,ports,foreign,obstacles=fixture(face,case);before=snapshot(board);original=deepcopy(ports);nets=['lower','upper']
    supplied=ports+[foreign] if case=='current_launch' else ports
    inventory=ports+([foreign] if foreign else [])
    start=time.monotonic();deadline=start+6
    with reserve_launches(inventory):
        search=Search(board,supplied,nets,{n:(20000000,10300000) for n in nets},deadline=deadline-1,reach_nm=4000000,grid_nm=1000000,allow_vias=True)
        state=(-1 if case=='own_contact' else 2,0,0,face,3);a=search.at(state);z=(a[0]+3000000,a[1]);other=p.B_Cu if face==p.F_Cu else p.F_Cu
        radius=math.ceil(max(abs(q) for q in search.offsets.values())+search.world.via_width/2)
        coarse_blockers=[list(search.world.collisions(band(a,z,radius,search.width/2+1),layer,'__cohort_capacity_probe__')) for layer in (face,other)]
        coarse=[not ids for ids in coarse_blockers]
        checks=[]
        for rank,net in enumerate(nets):
            entry,exit=shifted([a,z],search.offsets[net]);via=(entry[0]+1000000*(1+rank%2),entry[1])
            checks.append({'via':search.detail.via_clear(via,net),'entry':search.detail.clear(p.SHAPE_SEGMENT(vec(entry),vec(via),search.width),face,net),
                           'exit':search.detail.clear(p.SHAPE_SEGMENT(vec(via),vec(exit),search.width),other,net)})
        raw=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=150000)
        raw.launch_policy=None;raw._port_policy=None
        raw_entry=raw.clear(p.SHAPE_SEGMENT(vec((12000000,10000000)),vec((13000000,10000000)),150000),face,'lower')
        proposal=search.portal(state);row={'case':case,'face':face,'coarse':coarse,'coarse_blockers':coarse_blockers,
                                         'only_unused_obstacle_blocks_envelope':set(uid for ids in coarse_blockers for uid in ids)==set(obstacles) and bool(obstacles),'actual_checks':checks,'raw_entry_clear':raw_entry,
                                         'proposed':proposal is not None,'stats':dict(search.stats),'unchanged_before_commit':snapshot(board)==before}
        row['collector_elapsed_s']=time.monotonic()-start;proof_start=None
        if proposal:
            first=(0,0,0,face,0);after=(5,0,0,other,3);end=(7,0,0,other,3)
            edges=[(first,state,'track',None),(state,after,'portal',proposal),(after,end,'track',None)]
            emitted=[];add_via=LocalRouter.add_via
            if case=='rollback':
                # A real late obstacle is absent from the proposal's index, but
                # the fresh commit world must reject it after emitting lane one.
                item=p.PCB_TRACK(board);item.SetStart(vec((12450000,10600000)));item.SetEnd(vec((12550000,10600000)))
                item.SetWidth(150000);item.SetLayer(face);item.SetNet(board.FindNet('obstacle'));board.Add(item);before=snapshot(board)
                def observe_via(world,position,net):
                    add_via(world,position,net);emitted.append(net)
                LocalRouter.add_via=observe_via
            try:updated,added,error=search.commit(edges)
            finally:LocalRouter.add_via=add_via
            row['collector_elapsed_s']=time.monotonic()-start;proof_start=time.monotonic();after_state=snapshot(board)
            row.update(committed=updated is not None,error=error,added=len(added),rollback_exact=after_state==before,
                       emitted_via_nets=emitted,ports_exact=ports==original,
                       source_exact=after_state['footprints']==before['footprints'] and all(after_state['tracks'].get(uid)==item for uid,item in before['tracks'].items()))
            if updated:
                row['connected']=connected(board);row['vias']=sum(isinstance(t,p.PCB_VIA) and t.m_Uuid.AsString() in added for t in board.GetTracks())
                row['new_track_length_nm']=sum(t.GetLength() for t in board.GetTracks() if t.m_Uuid.AsString() in added and not isinstance(t,p.PCB_VIA));row['bends']=0
                departure=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=150000)
                departure.port_policy=PortPolicy(board,list(updated.values()),track_width_nm=150000)
                row['departures_clear']=all(departure.clear(p.SHAPE_SEGMENT(vec(q['exit_nm']),vec((q['exit_nm'][0]+500000,q['exit_nm'][1])),150000),q['exit_layer'],n) for n,q in updated.items())
                row['endpoints']={n:list(q['exit_nm']) for n,q in updated.items()}
                path=out/(case+'-'+str(face)+'.kicad_pcb');assert p.SaveBoard(str(path),board)
                saved=p.LoadBoard(str(path));row['saved_connected']=connected(saved);row['saved_exact']=snapshot(saved)==after_state
        row['proof_serialization_elapsed_s']=time.monotonic()-proof_start if proof_start is not None else 0
        row['fixture_elapsed_s']=time.monotonic()-start
        return row


def main():
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);rows=[]
    with generation_profile('dsn-safe-v1'):
        for face in (p.F_Cu,p.B_Cu):
            for case in ('unused_entry','unused_exit','track','via_opposite','same_net_land','current_launch','future_launch','own_contact','rollback'):
                rows.append(probe(out,face,case));(out/'report.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps({'cases':len(rows),'proposed':sum(row['proposed'] for row in rows)}))

if __name__=='__main__':main()
