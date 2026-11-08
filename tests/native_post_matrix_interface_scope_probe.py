"""Real full-matrix retirement with an unfinished two-face control interface."""
import hashlib,json,sys,time
from copy import deepcopy
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb import keyboard_native as api
from smilepcb.keyboard_process import project_settings
from smilepcb.keyboard_routing.interface_contract import capture_interfaces,verify_interfaces,MARKER as INTERFACES
from smilepcb.keyboard_routing.field_contract import capture,verify_native,MARKER as FIELD
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launches,current_launch_inventory,interface_identity
from smilepcb.keyboard_routing.local_router import LocalRouter,vec,xy
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing import grid_search

PROFILE='dsn-process-v2'
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)


def make(folder,face):
    folder.mkdir();b=p.BOARD();b.SetCopperLayerCount(2);other=p.B_Cu if face==p.F_Cu else p.F_Cu
    rules=b.GetDesignSettings();rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000
    rules.m_MinThroughDrill=200000;rules.m_ViasMinSize=500000;rules.m_ViasMinAnnularWidth=150000;rules.m_CopperEdgeClearance=250000
    for net in ('ROW0','COL0','control','ready','closed','foreign'):b.Add(p.NETINFO_ITEM(b,net))
    vertices=[(0,0),(30000000,0),(30000000,30000000),(0,30000000)]
    for a,z in zip(vertices,vertices[1:]+vertices[:1]):
        e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(a));e.SetEnd(vec(z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
    cfg={'parts':[],'layout':{'gpio':{'rows':['A0'],'cols':['B0']},'rgb':False},'mcu_gpio_pads':{'A0':'1','B0':'2'},'plane_nets':[],'routing_preparation':{'mode':'ordered-banks-v2'}}
    def fp(ref,region):
        f=p.FOOTPRINT(b);f.SetReference(ref);f.SetFPID(p.LIB_ID('Test','Pad'));f.SetLayer(face);b.Add(f)
        cfg['parts'].append({'ref':ref,'region':region,'footprint':'Test:Pad','nets':{}});return f
    def pad(f,num,net,at,layer):
        d=p.PAD(f);d.SetNumber(num);d.SetNet(b.FindNet(net));d.SetPosition(vec(at));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(vec((300000,300000)))
        ls=p.LSET();ls.AddLayer(layer);d.SetLayerSet(ls);f.Add(d);next(q for q in cfg['parts'] if q['ref']==f.GetReference())['nets'][num]=net;return d
    def track(net,a,z,layer):
        t=p.PCB_TRACK(b);t.SetNet(b.FindNet(net));t.SetStart(vec(a));t.SetEnd(vec(z));t.SetLayer(layer);t.SetWidth(150000);b.Add(t);return t
    chip=fp('U_MCU','controller');sources={}
    for num,net,at in [('1','ROW0',(5000000,5000000)),('2','COL0',(5000000,10000000)),('3','control',(5000000,20000000)),('4','ready',(5000000,25000000)),('5','closed',(5000000,23000000))]:sources[net]=[pad(chip,num,net,at,face)]
    # Two physical aliases are joined already; they do not close the net.
    sources['control'].append(pad(chip,'3','control',(5100000,20000000),face))
    for ref,net,at,layer in [('ROW_FIELD','ROW0',(15000000,5000000),face),('COL_FIELD','COL0',(5000000,15000000),face),('CONTROL_FIELD','control',(15000000,20000000),other),('READY_FIELD','ready',(15000000,25000000),face),('CLOSED_FIELD','closed',(15000000,23000000),face),('F1','foreign',(7450000,19500000),other),('F2','foreign',(7450000,20500000),other)]:pad(fp(ref,'matrix' if net in ('ROW0','COL0') else 'controller'),'1',net,at,layer)
    leads={net:track(net,a,z,face) for net,a,z in [('ROW0',(5000000,5000000),(7000000,5000000)),('COL0',(5000000,10000000),(5000000,12000000)),('ready',(5000000,25000000),(7000000,25000000)),('control',(5000000,20000000),(6000000,20000000)),('closed',(5000000,23000000),(7000000,23000000))]}
    via=p.PCB_VIA(b);via.SetNet(b.FindNet('control'));via.SetPosition(vec((6000000,20000000)));via.SetWidth(500000);via.SetDrill(200000);via.SetLayerPair(p.F_Cu,p.B_Cu);b.Add(via)
    terminal=track('control',(6000000,20000000),(7000000,20000000),other)
    ports=[]
    for net,t in leads.items():
        tip=terminal if net=='control' else t
        ports.append({'net':net,'pad_ids':[d.m_Uuid.AsString() for d in sources[net]],'added_ids':[t.m_Uuid.AsString()]+([via.m_Uuid.AsString(),terminal.m_Uuid.AsString()] if net=='control' else []),'terminal_id':tip.m_Uuid.AsString(),'kind':'via' if net=='control' else 'same_layer','via_id':via.m_Uuid.AsString() if net=='control' else None,'via_nm':[6000000,20000000] if net=='control' else None,'exit_nm':list(xy(tip.GetEnd())),'heading_origin_nm':list(xy(tip.GetStart())),'exit_layer':other if net=='control' else face})
    receipt=capture_interfaces(b,cfg,ports,deadline=time.monotonic()+5)
    retired=[];replacements={}
    for net,z in [('ROW0',(15000000,5000000)),('COL0',(5000000,15000000)),('ready',(15000000,25000000))]:
        t=leads[net];a=xy(t.GetStart());retired.append(t.m_Uuid.AsString());b.Delete(t);replacements[net]=track(net,a,z,face).m_Uuid.AsString()
    replacements['closed']=track('closed',(7000000,23000000),(15000000,23000000),face).m_Uuid.AsString()
    state=snapshot(b);roles={u:('row_trunk' if t['net']=='ROW0' else 'column_trunk') for u,t in state['tracks'].items() if t['net'] in ('ROW0','COL0')}
    terminals={d.m_Uuid.AsString():['rows' if d.GetNetname()=='ROW0' else 'columns',d.GetNetname()] for f in b.GetFootprints() for d in f.Pads() if d.GetNetname() in ('ROW0','COL0')}
    field=capture(state,{'complete':True,'scope':'matrix-with-controller','roles':roles,'owned_ids':list(roles),'terminals':terminals,'retired_source_ids':retired[:2]})
    (folder/INTERFACES).write_text(json.dumps(receipt));(folder/FIELD).write_text(json.dumps(field));(folder/'.smilepcb-keyboard.json').write_text(json.dumps(cfg))
    path=folder/'keyboard.kicad_pcb';path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')));assert p.SaveBoard(str(path),b)
    (folder/'fixture.json').write_text(json.dumps({'replacements':replacements,'headface':other}))
    return path,cfg,receipt,field,replacements,other


def extra_controls(path,cfg,receipt,field,replacements,headface,face):
    from smilepcb.keyboard_routing import native_graph
    from smilepcb.keyboard_routing.native_graph import Endpoint,copper_graph
    original=path.read_bytes();results={}
    for change in ('intact','moved','retired'):
        b=p.LoadBoard(str(path));proof=verify_interfaces(b,cfg,receipt,deadline=time.monotonic()+5,retire_completed=True)
        q=next(q for q in proof['ports'] if q['net']=='closed');key=interface_identity(q)
        with reserve_launches(proof['ports'],resolved=proof['resolved']):
            world=LocalRouter(b,deadline=time.monotonic()+5,compact=True,track_width_nm=150000)
            for item in list(b.GetTracks()):
                if item.m_Uuid.AsString()==replacements['closed']:b.Delete(item)
                elif item.m_Uuid.AsString()==q['terminal_id']:
                    if change=='moved':item.SetEnd(vec((7100000,23000000)))
                    if change=='retired':b.Delete(item)
            before=snapshot(b);calls=[];graph=native_graph.copper_graph
            def counted(*args,**kwargs):calls.append(1);return graph(*args,**kwargs)
            native_graph.copper_graph=counted
            try:
                try:
                    world.endpoints(Endpoint((5200000,23000000),face,'closed'),(15000000,23000000))
                    if change!='intact':results[change]={'rejected':False};continue
                    active=key not in world.port_policy.resolved
                    blocked=world.leg((5200000,23000000),(5500000,23000000),face,'closed') is None
                    count=len(calls)
                    for _ in range(20):world.clear(p.SHAPE_SEGMENT(vec((7000000,23000000)),vec((8000000,23000000)),150000),face,'closed')
                    per_edge=len(calls)==count
                    source=next(d for f in b.GetFootprints() if f.GetReference()=='U_MCU' for d in f.Pads() if d.GetNumber()=='5')
                    target=next(d for f in b.GetFootprints() if f.GetReference()=='CLOSED_FIELD' for d in f.Pads())
                    joined=world.connect(source,target)
                    world.refresh_interfaces(copper_graph(b,{'closed'},time.monotonic()+5))
                    results[change]={'reactivated':active,'interior_rejected':blocked,'no_per_edge_graph':per_edge,'actual_head_restored':joined and key in world.port_policy.resolved,'source_exact':all(snapshot(b)['tracks'].get(uid)==shape for uid,shape in before['tracks'].items())}
                except ValueError:
                    results[change]={'rejected':change!='intact','no_emission':not world.added,'source_exact':snapshot(b)==before}
            finally:native_graph.copper_graph=graph
    original_run=api._run;clock=time.monotonic
    for case in ('raw_bad','raw_good','closed_bad','closed_good','closed_subset','source_mutation','deadline'):
        path.write_bytes(original);(path.parent/FIELD).write_text(json.dumps(field))
        subset_before=None;old_full_item_rejected=None
        if case=='closed_subset':
            from smilepcb.keyboard_routing.completion import pad_partitions
            from smilepcb.keyboard_routing.port_policy import PortPolicy
            cover=p.LoadBoard(str(path));t=p.PCB_TRACK(cover);t.SetStart(vec((5000000,23000000)));t.SetEnd(vec((15000000,23000000)));t.SetWidth(150000);t.SetLayer(face);t.SetNet(cover.FindNet('closed'));cover.Add(t);assert p.SaveBoard(str(path),cover)
            proof=verify_interfaces(cover,cfg,receipt,deadline=time.monotonic()+5,retire_completed=True)
            key=interface_identity(next(q for q in proof['ports'] if q['net']=='closed'))
            old_policy=PortPolicy(cover,proof['ports'],resolved=set(map(tuple,proof['resolved']))-{key})
            old_full_item_rejected=not old_policy.accepts_segment((5000000,23000000),(6800000,23000000),face,'closed',150000)
            subset_before=(snapshot(cover),{frozenset(g) for g in pad_partitions(cover,time.monotonic()+5)})
        def raw(request,**kwargs):
            board=p.LoadBoard(request['board']);net='closed' if case.startswith('closed') else 'control'
            if case.startswith('closed'):
                for item in list(board.GetTracks()):
                    if item.m_Uuid.AsString()==replacements['closed']:board.Delete(item)
            if case=='closed_subset':
                uid=next(q for q in receipt['ports'] if q['net']=='closed')['terminal_id']
                next(t for t in board.GetTracks() if t.m_Uuid.AsString()==uid).SetEnd(vec((6800000,23000000)))
            elif case=='source_mutation':
                uid=next(q for q in receipt['ports'] if q['net']=='control')['terminal_id']
                next(t for t in board.GetTracks() if t.m_Uuid.AsString()==uid).SetWidth(150001)
            else:
                layer=face if net=='closed' or case=='raw_bad' else headface
                start=(5000000,23000000) if case=='closed_bad' else (7000000,23000000) if net=='closed' else (5000000,20000000) if case=='raw_bad' else (7000000,20000000)
                end=(15000000,23000000) if net=='closed' else (5400000,20000000) if case=='raw_bad' else (15000000,20000000)
                t=p.PCB_TRACK(board);t.SetStart(vec(start));t.SetEnd(vec(end));t.SetWidth(150000);t.SetLayer(layer);t.SetNet(board.FindNet(net));board.Add(t)
            assert p.SaveBoard(request['board'],board)
            if case=='deadline':time.monotonic=lambda:clock()+1000
            return {'accepted':True}
        api._run=raw;error=None
        try:answer=api.run({'operation':'field_verify','board':str(path),'generation_profile':PROFILE})
        except (ValueError,TimeoutError) as exc:error=type(exc).__name__
        finally:api._run=original_run;time.monotonic=clock
        if case=='closed_subset':
            saved=p.LoadBoard(str(path));state=snapshot(saved)
            results[case]={'accepted':error is None,'old_full_item_rejected':old_full_item_rejected,
                'native_partitions_exact':subset_before[1]=={frozenset(g) for g in pad_partitions(saved,time.monotonic()+5)},
                'no_added_copper':set(state['tracks'])<=set(subset_before[0]['tracks'])}
        elif case in ('raw_good','closed_good'):
            selected='closed' if case=='closed_good' else 'control'
            results[case]={'accepted':error is None,'native_full_pad_join':error is None and selected not in api.pad_gaps(p.LoadBoard(str(path)))}
        else:results[case]={'rejected':error is not None,'saved_source_exact':path.read_bytes()==original,'timeout':error=='TimeoutError' if case=='deadline' else True}
    path.write_bytes(original);(path.parent/FIELD).write_text(json.dumps(field))
    return results


def exercise(folder,face):
    path=folder/'keyboard.kicad_pcb';cfg=json.loads((folder/'.smilepcb-keyboard.json').read_text());receipt=json.loads((folder/INTERFACES).read_text());field=json.loads((folder/FIELD).read_text())
    metadata=json.loads((folder/'fixture.json').read_text());replacements=metadata['replacements'];headface=metadata['headface']
    before=path.read_bytes();b=p.LoadBoard(str(path));source=snapshot(b)
    assert b.GetDesignSettings().m_TrackMinWidth==150000
    assert b.GetDesignSettings().m_HoleClearance==250000
    result={'native_full_matrix':bool(verify_native(b,field)), 'profile':PROFILE}
    result['extra']=extra_controls(path,cfg,receipt,field,replacements,headface,face)
    control=next(q for q in receipt['ports'] if q['net']=='control');tip=tuple(control['exit_nm']);cross_a,cross_z=(7450000,19500000),(7450000,20500000)
    cross=p.SHAPE_SEGMENT(vec(cross_a),vec(cross_z),150000)
    # Exact old blanket-skip behavior, without mutating installed code.
    with reserve_launches([]):
        old=LocalRouter(b,deadline=time.monotonic()+5,compact=True,track_width_nm=150000)
        result['old_control']={'foreign_native_clear':old.clear(cross,headface,'foreign'),'source_interior_leg_accepted':old.leg((5200000,20000000),(5500000,20000000),face,'control') is not None,'prefix_annulus_via_accepted':old.via_clear((6800000,20200000),'control')}
    seen=[];original=api._run
    def inspect(request,**kwargs):
        live=p.LoadBoard(request['board']);world=LocalRouter(live,deadline=time.monotonic()+5,compact=True,track_width_nm=150000)
        pads=[d for f in live.GetFootprints() if f.GetReference()=='U_MCU' for d in f.Pads() if d.GetNumber()=='3']
        point,layers=world.endpoints(pads[0],(15000000,20000000))
        seen.append({'only_open_control_live':[q['net'] for q in current_launches() if interface_identity(q) not in current_launch_inventory().resolved]==['control'], 'foreign_runway_reserved':not world.clear(cross,headface,'foreign'),'opposite_face_clear':world.clear(cross,face,'foreign'),'source_interior_leg_rejected':world.leg((5200000,20000000),(5500000,20000000),face,'control') is None,'prefix_annulus_via_rejected':not world.via_clear((6800000,20200000),'control'),'direct_uses_actual_remote_head':point==tip and layers==[headface],'forward_head_leg_clear':world.leg(tip,(8000000,20000000),headface,'control') is not None})
        return original(request,**kwargs)
    api._run=inspect;attempts=[];search=grid_search.search_many
    def observe(router,starts,targets,net):
        attempts.append({'net':net,'starts':starts,'targets':targets})
        return search(router,starts,targets,net)
    grid_search.search_many=observe
    try:
        # Calling the real worker tests guard -> resolver -> router -> save.
        routed=api.run({'operation':'components','board':str(path),'generation_profile':PROFILE,'routing_quality':'turns','include_nets':['control'],'grid_step_nm':125000,'connection_budget_s':3,'track_width_nm':150000})
    finally:api._run=original;grid_search.search_many=search
    after=p.LoadBoard(str(path));after_state=snapshot(after)
    result['live']=seen[0];result['components']={'connected':routed['connections']==1,'only_real_head':bool(attempts) and attempts[0]['starts']==[(tip,headface)],'source_exact':all(after_state['tracks'].get(u)==t for u,t in source['tracks'].items()),'matrix_preserved':routed['matrix_field_preserved']}
    # Retired matrix and ready heads must be accepted only by current closure.
    resolved=verify_interfaces(after,cfg,receipt,deadline=time.monotonic()+5,retire_completed=True)
    result['all_native_completed_retired']=len(resolved['resolved'])==len(resolved['ports']) and not resolved['source_tracks']
    try:verify_interfaces(b,cfg,receipt,deadline=time.monotonic()+5)
    except ValueError:result['strict_capture_unchanged']=True
    else:result['strict_capture_unchanged']=False
    reopened=p.LoadBoard(str(path))
    for t in list(reopened.GetTracks()):
        if t.m_Uuid.AsString()==replacements['ready']:reopened.Delete(t)
    unchanged=snapshot(reopened)
    try:verify_interfaces(reopened,cfg,receipt,deadline=time.monotonic()+5,retire_completed=True)
    except ValueError:result['reopened_missing_head_rejected']=snapshot(reopened)==unchanged
    else:result['reopened_missing_head_rejected']=False
    for case in ('missing_live_head','deadline','invalid_matrix'):
        # Restore the original still-open candidate and metadata per control.
        path.write_bytes(before);(folder/FIELD).write_text(json.dumps(field));state=p.LoadBoard(str(path))
        if case=='missing_live_head':
            for t in list(state.GetTracks()):
                if t.m_Uuid.AsString()==control['terminal_id']:state.Delete(t)
            assert p.SaveBoard(str(path),state)
        if case=='invalid_matrix':
            invalid=deepcopy(field);invalid['terminals'].pop(next(iter(invalid['terminals'])));(folder/FIELD).write_text(json.dumps(invalid))
        raw=path.read_bytes()
        try:
            if case=='deadline':verify_interfaces(state,cfg,receipt,deadline=time.monotonic()-1,retire_completed=True)
            else:api.run({'operation':'field_verify','board':str(path),'generation_profile':PROFILE})
        except (ValueError,TimeoutError):result[case]={'rejected':True,'saved_source_exact':path.read_bytes()==raw}
        else:result[case]={'rejected':False}
    path.write_bytes(before);(folder/FIELD).write_text(json.dumps(field))
    result['initial_sha256']=hashlib.sha256(before).hexdigest();result['scratch_restored']=path.read_bytes()==before
    return result

if __name__=='__main__':
    with generation_profile(PROFILE):
        if '--prepare' in sys.argv:
            for name,face in [('front',p.F_Cu),('back',p.B_Cu)]:make(out/name,face)
        else:
            report={name:exercise(out/name,face) for name,face in [('front',p.F_Cu),('back',p.B_Cu)]}
            (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
