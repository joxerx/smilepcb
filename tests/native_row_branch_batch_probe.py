"""Native row attachments share an append-only world and roll back atomically."""
import argparse,json,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.routing_quality import using_quality_profile
from smilepcb.keyboard_routing import field,matrix
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory
from smilepcb.keyboard_routing.conditional_geometry import FrozenProofRouter,saved_via_clear_cached

def v(x,y):return p.VECTOR2I(round(x*1000000),round(y*1000000))

def make():
    board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,COMPACT_VIAS)
    for name in ('ROW_A','retained'):board.Add(p.NETINFO_ITEM(board,name))
    corners=[(0,0),(32,0),(32,20),(0,20)]
    for a,b in zip(corners,corners[1:]+corners[:1]):
        e=p.PCB_SHAPE(board);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(v(*a));e.SetEnd(v(*b));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);board.Add(e)
    pads=[]
    for i,x in enumerate((6,16,26)):
        fp=p.FOOTPRINT(board);fp.SetReference('D'+str(i));fp.SetPosition(v(x,10));board.Add(fp)
        pad=p.PAD(fp);pad.SetNumber('1');pad.SetPosition(v(x,10));pad.SetSize(v(.4,.4));pad.SetShape(p.PAD_SHAPE_CIRCLE)
        pad.SetAttribute(p.PAD_ATTRIB_SMD);layers=p.LSET();layers.AddLayer(p.F_Cu);pad.SetLayerSet(layers);pad.SetNet(board.FindNet('ROW_A'));fp.Add(pad);pads.append(pad)
    track=p.PCB_TRACK(board);track.SetStart(v(3,3));track.SetEnd(v(29,3));track.SetWidth(150000);track.SetLayer(p.B_Cu);track.SetNet(board.FindNet('retained'));board.Add(track)
    return board,pads

def main():
    ap=argparse.ArgumentParser();ap.add_argument('case',choices=['connected','late-failure','late-exception']);ap.add_argument('output',type=Path);args=ap.parse_args()
    select_generation_profile('dsn-process-v2');board,pads=make();before=snapshot(board);deadline=time.monotonic()+20
    original=matrix.KeyboardRouter;constructors=[];calls=[];first_added=[];result={};report=None
    class ObservedRouter(original):
        def __init__(self,*a,**kw):
            super().__init__(*a,**kw);constructors.append({'initial_added':len(self.added),'copper_count':len(list(board.GetTracks()))})
            assert constructors[-1]['copper_count']>len(before['tracks']),'The spine must be committed before the branch world is built'
        def connect(self,source,target):
            ordinal=len(calls);start_ids={t.m_Uuid.AsString() for t in board.GetTracks()}
            calls.append({'ordinal':ordinal,'prior_added_count':len(self.added),'deadline':self.deadline})
            assert self.deadline<=deadline and self.connection_budget_s==2 and self.grid_step==250000 and self.heuristic_weight==3
            if ordinal:
                tracks={t.m_Uuid.AsString():t for t in board.GetTracks()}
                earlier=[tracks[uid] for uid in first_added]
                via=next(t for t in earlier if isinstance(t,p.PCB_VIA))
                wire=next(t for t in earlier if not isinstance(t,p.PCB_VIA))
                # Newly emitted copper and physical drill must be indexed for
                # the next request, including same-net via spacing.
                assert not self.clear(p.SHAPE_SEGMENT(wire.GetStart(),wire.GetEnd(),wire.GetWidth()),wire.GetLayer(),'retained')
                assert not self.via_clear((via.GetPosition().x,via.GetPosition().y),'ROW_A')
                calls[-1]['previous_native_shapes_visible']=True
                calls[-1]['prior_added_ids']=list(self.added)
                if args.case!='connected':
                    inventory=current_launch_inventory();inventory.resolved.add(('speculative',));inventory.repair_reserved.add(('speculative',))
                    if args.case=='late-exception':raise ValueError('Injected late branch exception')
                    return False
            accepted=super().connect(source,target);calls[-1]['accepted']=accepted
            added={t.m_Uuid.AsString() for t in board.GetTracks()}-start_ids
            assert accepted and added,'Fixture requires actual opposite-face attachment copper'
            if not ordinal:first_added.extend(added)
            return accepted
    matrix.KeyboardRouter=ObservedRouter
    with reserve_launches([]):
        try:
            with using_quality_profile('turns'):
                report=field._seed_groups(board,rows={'ROW_A':pads},columns={},row_key_y={'ROW_A':[10000000]},row_layer=p.B_Cu,column_layer=p.F_Cu,deadline=deadline)
        except ValueError as error:
            if args.case!='late-exception':raise
            assert str(error)=='Injected late branch exception';result['exception']=str(error)
        finally:matrix.KeyboardRouter=original
        inventory=current_launch_inventory();assert not inventory.ports and not inventory.resolved and not inventory.repair_reserved
    after=snapshot(board)
    if args.case=='connected':
        assert report['rows'][0]['complete'] and report['rows'][0]['routed_branches']==len(pads)
        graph=copper_graph(board,{'ROW_A'},deadline,include_zones=False)
        ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes']) if not n['zone']}
        assert len({graph['find'](ids[d.m_Uuid.AsString()]) for d in pads})==1
        proof=FrozenProofRouter(board,deadline=deadline,track_width_nm=150000,graph=graph,covered_nets={'ROW_A'})
        for item in board.GetTracks():
            if item.m_Uuid.AsString() in before['tracks']:continue
            if isinstance(item,p.PCB_VIA):assert saved_via_clear_cached(proof,item)
            else:assert proof.clear(p.SHAPE_SEGMENT(item.GetStart(),item.GetEnd(),item.GetWidth()),item.GetLayer(),item.GetNetname())
        proof.assert_unchanged();assert all(after['tracks'][uid]==shape for uid,shape in before['tracks'].items())
        assert before['footprints']==after['footprints'];result.update(native_graph=True,native_geometry=True,retained_exact=True)
    else:
        assert len(calls)==2 and after==before
        if report is not None:assert not report['rows'][0]['complete']
        result['rollback_exact']=True
    result.update(constructors=constructors,calls=calls,launch_inventory_exact=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    # This is the performance regression: three native attachments need one
    # append-only branch index, including the path that later rolls back.
    assert len(constructors)==1,constructors
    assert all(set(first_added)<=set(call['prior_added_ids']) for call in calls[1:])
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
