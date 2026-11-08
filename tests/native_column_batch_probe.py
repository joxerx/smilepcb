"""Real column routing retains only an append-only native geometry world."""
import argparse,json,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.routing_quality import using_quality_profile
from smilepcb.keyboard_routing import field,matrix
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory
from smilepcb.keyboard_routing.conditional_geometry import FrozenProofRouter,saved_via_clear_cached

def v(x,y):return p.VECTOR2I(round(x*1000000),round(y*1000000))
def make(case):
    board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,COMPACT_VIAS)
    for net in ('COL_A','COL_B','wall'):board.Add(p.NETINFO_ITEM(board,net))
    corners=[(0,0),(16,0),(16,22),(0,22)]
    for a,b in zip(corners,corners[1:]+corners[:1]):
        e=p.PCB_SHAPE(board);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(v(*a));e.SetEnd(v(*b));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);board.Add(e)
    def pad(ref,at,net,size=(.3,.3),face=p.F_Cu,hole=False):
        fp=p.FOOTPRINT(board);fp.SetReference(ref);fp.SetPosition(v(*at));board.Add(fp)
        d=p.PAD(fp);d.SetNumber('1');d.SetPosition(v(*at));d.SetSize(v(*size));d.SetShape(p.PAD_SHAPE_CIRCLE if hole else p.PAD_SHAPE_RECT)
        d.SetAttribute(p.PAD_ATTRIB_NPTH if hole else p.PAD_ATTRIB_SMD)
        layers=p.LSET();layers.AddLayer(face)
        if hole:layers.AddLayer(p.B_Cu);d.SetDrillSize(v(*size))
        d.SetLayerSet(layers)
        if net:d.SetNet(board.FindNet(net))
        fp.Add(d);return d
    pad('W',(8,11),'wall',(15.2,.4));pad('H',(7,9),None,(2,2),hole=True)
    # The middle pair has equal X/Y displacement: its sole monotone template
    # crosses the actual drill. The native fallback must detour around it.
    columns={'COL_A':[pad('A'+str(i),point,'COL_A') for i,point in enumerate(((4,3),(4,6),(10,12),(10,19)))]}
    points=((3,4),(5,6),(12,15),(12,19)) if case in ('failure','exception') else ((12,3),(12,6),(12,15),(12,19))
    columns['COL_B']=[pad('B'+str(i),point,'COL_B',face=p.B_Cu if case=='access' and i==0 else p.F_Cu) for i,point in enumerate(points)]
    return board,columns

def main():
    ap=argparse.ArgumentParser();ap.add_argument('case',choices=['connected','access','failure','exception']);ap.add_argument('output',type=Path);args=ap.parse_args()
    select_generation_profile('dsn-process-v2');board,columns=make(args.case);before=snapshot(board);deadline=time.monotonic()+25
    original_commit=field.commit_plan;original_connect=matrix.KeyboardRouter.connect
    worlds=[];events=[];first_track_ids=set();first_via_ids=set();result={};report=None
    def dirty_launches():
        inventory=current_launch_inventory();wire=next(t for t in board.GetTracks() if t.m_Uuid.AsString() in first_track_ids)
        key=(columns['COL_A'][0].m_Uuid.AsString(),)
        inventory.ports[key]={'kind':'same_layer','net':'COL_A','pad_ids':list(key),'added_ids':[wire.m_Uuid.AsString()],
            'terminal_id':wire.m_Uuid.AsString(),'exit_nm':[wire.GetEnd().x,wire.GetEnd().y],
            'heading_origin_nm':[wire.GetStart().x,wire.GetStart().y],'exit_layer':wire.GetLayer()}
        inventory.resolved.add(key);inventory.repair_reserved.add(key)
    def remember(router,net,kind):
        if all(router is not q for q in worlds):worlds.append(router)
        row={'net':net,'kind':kind,'world':next(i for i,q in enumerate(worlds) if router is q),'deadline':router.deadline}
        events.append(row)
        if net=='COL_B':
            inventory=current_launch_inventory();assert not inventory.ports and not inventory.resolved and not inventory.repair_reserved,'Failed column leaked source retirement'
            if args.case not in ('failure','exception'):
                tracks={t.m_Uuid.AsString():t for t in board.GetTracks()}
                assert first_track_ids and first_via_ids,'Fixture needs actual earlier column track and via emission'
                wire=tracks[next(iter(first_track_ids))];via=tracks[next(iter(first_via_ids))]
                assert not router.clear(p.SHAPE_SEGMENT(wire.GetStart(),wire.GetEnd(),wire.GetWidth()),wire.GetLayer(),net)
                assert not router.via_clear((via.GetPosition().x,via.GetPosition().y),net)
                row['previous_foreign_track_and_drill_visible']=True
    def commit(router,plan,net):
        remember(router,net,'finite')
        if net=='COL_A' and args.case=='exception' and first_via_ids:
            dirty_launches();raise ValueError('Injected column search exception')
        value=original_commit(router,plan,net)
        if net=='COL_A':
            for t in board.GetTracks():
                if t.m_Uuid.AsString() in before['tracks']:continue
                (first_via_ids if isinstance(t,p.PCB_VIA) else first_track_ids).add(t.m_Uuid.AsString())
        return value
    def connect(router,a,b):
        remember(router,a.GetNetname(),'fallback')
        if a.GetNetname()=='COL_A' and args.case=='failure':
            assert first_track_ids,'Failure must follow real first-interval copper'
            dirty_launches()
            return False
        value=original_connect(router,a,b)
        if a.GetNetname()=='COL_A':
            for t in board.GetTracks():
                if t.m_Uuid.AsString() in before['tracks']:continue
                (first_via_ids if isinstance(t,p.PCB_VIA) else first_track_ids).add(t.m_Uuid.AsString())
        return value
    field.commit_plan=commit;matrix.KeyboardRouter.connect=connect
    with reserve_launches([]):
        try:
            with using_quality_profile('turns'):
                report=field._seed_groups(board,rows={},columns=columns,row_key_y={},row_layer=p.B_Cu,column_layer=p.F_Cu,deadline=deadline)
        except ValueError as error:
            if args.case!='exception' or str(error)!='Injected column search exception':raise
            result['exception']=str(error)
        finally:field.commit_plan=original_commit;matrix.KeyboardRouter.connect=original_connect
        inventory=current_launch_inventory();assert not inventory.ports and not inventory.resolved and not inventory.repair_reserved
    after=snapshot(board)
    if args.case=='exception':assert first_via_ids and after==before;result['rollback_exact']=True
    else:
        expected={'COL_B'} if args.case=='failure' else set(columns)
        assert {q['net'] for q in report['columns'] if q['complete']}==expected,report
        if args.case=='access':
            accessed=next(q for q in report['columns'] if q['net']=='COL_B')
            assert accessed['access_vias'] and accessed['access_ids'],'External access must emit native copper before rebuilding'
        if args.case=='failure':
            assert not first_track_ids&set(after['tracks'])
            first=next(q for q in report['columns'] if q['net']=='COL_B')['column_intervals'][0]
            assert 'fallback' not in first,'Removed previous-column copper must not obstruct this exact diagonal'
        graph=copper_graph(board,expected,deadline,include_zones=False)
        ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes']) if not n['zone']}
        for net in expected:assert len({graph['find'](ids[d.m_Uuid.AsString()]) for d in columns[net]})==1
        proof=FrozenProofRouter(board,deadline=deadline,track_width_nm=150000,graph=graph,covered_nets=expected)
        for item in board.GetTracks():
            if item.m_Uuid.AsString() in before['tracks']:continue
            if isinstance(item,p.PCB_VIA):assert saved_via_clear_cached(proof,item)
            else:assert proof.clear(p.SHAPE_SEGMENT(item.GetStart(),item.GetEnd(),item.GetWidth()),item.GetLayer(),item.GetNetname())
        proof.assert_unchanged();result.update(native_graph=True,native_geometry=True,report=report)
    assert before['footprints']==after['footprints'] and all(after['tracks'].get(uid)==shape for uid,shape in before['tracks'].items())
    result.update(events=events,world_count=len(worlds),retained_exact=True,launch_inventory_exact=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    assert any(q['kind']=='fallback' for q in events),'The real drill must force native fallback'
    expected_worlds=2 if args.case in ('access','failure') else 1
    assert len(worlds)==expected_worlds,(len(worlds),events)
    assert all(isinstance(q,matrix.KeyboardRouter) for q in worlds)
    print(json.dumps({'case':args.case,'worlds':len(worlds),'passed':True}),flush=True)

if __name__=='__main__':main()
