"""Real field routing on a connected board with no global horizontal lane."""
import argparse,json,time
from pathlib import Path
from types import SimpleNamespace
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.routing_quality import using_quality_profile
from smilepcb.keyboard_routing.field import build_field
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.matrix import KeyboardRouter
from smilepcb.keyboard_routing.field_contract import capture,verify_native
from smilepcb.keyboard_routing.collector_peel import field_targets
from smilepcb.keyboard_routing.conditional_geometry import FrozenProofRouter,saved_via_clear_cached
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory

def vec(q):return p.VECTOR2I(*(round(v*1000000) for v in q))
def make(singleton=False):
    board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,COMPACT_VIAS)
    positions=[(3,5),(6,5),(16,17),(19,17)] if not singleton else [(3,5),(6,5),(18,17)]
    for net in ['row',*[f'column{i}' for i in range(len(positions))]]:board.Add(p.NETINFO_ITEM(board,net))
    outline=[(0,0),(10,0),(10,6),(14,6),(14,14),(22,14),(22,22),(12,22),(12,16),(8,16),(8,8),(0,8)]
    for a,z in zip(outline,outline[1:]+outline[:1]):
        edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vec(a));edge.SetEnd(vec(z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
    for i,(x,y) in enumerate(positions):
        for ref,point,net in [(f'D{i+1}',(x,y),'row'),(f'SW{i+1}',(x,y+1.5),f'column{i}')]:
            fp=p.FOOTPRINT(board);fp.SetReference(ref);fp.SetPosition(vec(point));board.Add(fp)
            for offset in ([0,.1] if ref=='D2' else [0]):
                d=p.PAD(fp);d.SetNumber('1');d.SetPosition(vec((point[0]+offset,point[1])));d.SetSize(vec((.4,.4)));d.SetShape(p.PAD_SHAPE_CIRCLE)
                d.SetAttribute(p.PAD_ATTRIB_SMD);layers=p.LSET();layers.AddLayer(p.F_Cu if ref.startswith('D') else p.B_Cu);d.SetLayerSet(layers);d.SetNet(board.FindNet(net));fp.Add(d)
    cfg={'layout':{'keys':[{'id':i+1,'matrix':[0,i]} for i in range(len(positions))],
        'gpio':{'rows':['R'],'cols':[f'C{i}' for i in range(len(positions))]},'rgb':False},
        'parts':[{'ref':'U_MCU','region':'controller','nets':{'1':'row',**{str(i+2):f'column{i}' for i in range(len(positions))}}}],
        'mcu_gpio_pads':{'R':'1',**{f'C{i}':str(i+2) for i in range(len(positions))}},'plane_nets':[]}
    return board,cfg

def main():
    ap=argparse.ArgumentParser();ap.add_argument('case');ap.add_argument('output',type=Path);args=ap.parse_args()
    select_generation_profile('dsn-process-v2');board,cfg=make(args.case=='singleton');before=snapshot(board);deadline=time.monotonic()+25
    if args.case=='retained-connection':
        points=[(6,5),(10,9),(10,13),(14,17),(16,17)]
        for a,z in zip(points,points[1:]):
            track=p.PCB_TRACK(board);track.SetStart(vec(a));track.SetEnd(vec(z));track.SetWidth(150000);track.SetLayer(p.F_Cu);track.SetNet(board.FindNet('row'));board.Add(track)
        before=snapshot(board)
    calls=[];original=KeyboardRouter.connect
    def injected(self,*a,**kw):
        calls.append(len(list(board.GetTracks())))
        assert calls[-1]>0,'Fault must follow real spine additions'
        inventory=current_launch_inventory();inventory.resolved.add(('speculative',));inventory.repair_reserved.add(('speculative',))
        if args.case=='failure':return False
        if args.case=='expiry':
            import smilepcb.keyboard_routing.row_clusters as module
            module.time=SimpleNamespace(monotonic=lambda:deadline+1);return False
        raise ValueError('Injected connector exception')
    if args.case in ('failure','expiry','exception'):KeyboardRouter.connect=injected
    if args.case=='global-expiry':
        from smilepcb.keyboard_routing import field
        original_seed=field._seed_straight_row
        def expire_after_global(*a,**kw):
            row=original_seed(*a,**kw);assert not row['complete']
            field.time=SimpleNamespace(monotonic=lambda:deadline+1);return row
        field._seed_straight_row=expire_after_global
        def forbidden(*a,**kw):raise AssertionError('Expired global attempt must not start a cluster link')
        KeyboardRouter.connect=forbidden
    if args.case=='global-metadata':
        from smilepcb.keyboard_routing import field,row_clusters
        original_seed=field._seed_straight_row;original_clusters=row_clusters.route_row_clusters
        def speculative_global(*a,**kw):
            row=original_seed(*a,**kw)
            if not row['complete']:
                current_launch_inventory().resolved.add(('speculative',))
                current_launch_inventory().repair_reserved.add(('speculative',))
            return row
        def check_fallback(*a,**kw):
            inventory=current_launch_inventory()
            assert not inventory.resolved and not inventory.repair_reserved,'Stale failed-global launch state reached fallback'
            return original_clusters(*a,**kw)
        field._seed_straight_row=speculative_global;row_clusters.route_row_clusters=check_fallback
    if args.case=='retained-connection':
        def forbidden(*a,**kw):raise AssertionError('Already connected native clusters need no new link search')
        KeyboardRouter.connect=forbidden
    if args.case=='field-exception':
        from smilepcb.keyboard_routing import row_clusters,native_graph
        original_clusters=row_clusters.route_row_clusters;original_graph=native_graph.copper_graph;finished=[]
        def accepted_then_dirty(*a,**kw):
            row=original_clusters(*a,**kw);assert row['complete'];finished.append(True)
            calls.append(len(list(board.GetTracks())))
            current_launch_inventory().resolved.add(('speculative',))
            current_launch_inventory().repair_reserved.add(('speculative',));return row
        def failed_final_graph(*a,**kw):
            if finished:raise ValueError('Injected field proof exception')
            return original_graph(*a,**kw)
        row_clusters.route_row_clusters=accepted_then_dirty;native_graph.copper_graph=failed_final_graph
    result={};report=None
    with reserve_launches([]):
        try:
            with using_quality_profile('turns'):
                report=build_field(board,cfg,row_layer=p.F_Cu,column_layer=p.B_Cu,deadline=deadline)
        except ValueError as error:
            if args.case not in ('exception','field-exception'):raise
            result['exception']=str(error)
        finally:KeyboardRouter.connect=original
        inventory=current_launch_inventory()
        assert not inventory.ports and not inventory.resolved and not inventory.repair_reserved
        result['launch_inventory_exact']=True
    after=snapshot(board)
    if args.case in ('failure','expiry','exception','global-expiry','field-exception'):
        result.update(calls=calls,rollback_exact=after==before,report=report)
        assert (calls or args.case=='global-expiry') and after==before
        if report is not None:assert not report['complete']
    else:
        assert report['complete'],report
        row=report['rows'][0];assert row['topology']=='clustered_spines' and 'lane_y_nm' not in row
        if args.case=='retained-connection':assert not row['link_ids'] and all(q['already_connected'] for q in row['links'])
        else:assert row['link_ids']
        assert row['geometry_stage']=='construction'
        assert all(after['tracks'][u]['class']=='PCB_VIA' or after['tracks'][u]['start'][1]==after['tracks'][u]['end'][1] for u in row['trunk_ids'])
        assert all(report['roles'][u]=='row_link' for u in row['link_ids'])
        assert set(row['object_ids'])==set(row['trunk_ids'])|set(row['link_ids'])|set(row['access_ids'])
        assert report['physical_pad_count']==len(cfg['layout']['keys'])*2+1
        assert row['links'] and all(link['length_nm']>0 or link.get('already_connected') for link in row['links'])
        if args.case=='singleton':assert any(s.get('singleton') for s in row['spines'])
        graph=copper_graph(board,{'row',*[f'column{i}' for i in range(len(cfg['layout']['keys']))]},deadline,include_zones=False)
        proof=FrozenProofRouter(board,deadline=deadline,track_width_nm=150000,graph=graph,covered_nets=set(report['field_groups']['rows'])|set(report['field_groups']['columns']))
        for item in board.GetTracks():
            if isinstance(item,p.PCB_VIA):assert saved_via_clear_cached(proof,item)
            else:assert proof.clear(p.SHAPE_SEGMENT(item.GetStart(),item.GetEnd(),item.GetWidth()),item.GetLayer(),item.GetNetname())
        proof.assert_unchanged();contract=capture(after,report,prerequisite_ids=before['tracks']);verify_native(board,contract,deadline=deadline)
        targets=field_targets(board,cfg,[{'net':'row'}],report['owned_ids'],deadline)
        target_ids={n['item'].m_Uuid.AsString() for _,n in targets['row']}
        assert set(row['link_ids'])<=target_ids
        if args.case=='hygiene':
            from smilepcb.keyboard_routing.field_hygiene import clean_matrix_tracks
            result['cleanup']=clean_matrix_tracks(board,owned_ids=report['owned_ids'],source_ids=set(before['tracks']),nets={'row'},deadline=deadline)
            current=snapshot(board);roles={u:role for u,role in report['roles'].items() if u in current['tracks']}
            contract=capture(current,dict(report,roles=roles,owned_ids=sorted(roles)))
            assert set(contract['tracks'])==set(current['tracks']) and 'rows' not in contract and 'spines' not in contract
            verify_native(board,contract,deadline=deadline);result['current_receipt_after_hygiene']=True
        saved=args.output.with_suffix('.kicad_pcb');assert p.SaveBoard(str(saved),board);verify_native(p.LoadBoard(str(saved)),contract,deadline=deadline)
        result.update(report=report,retained_exact=before['footprints']==after['footprints'],native_geometry=True,reloaded=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
if __name__=='__main__':main()
