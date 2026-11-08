"""Joint short controller interfaces, using native shapes and bounded search.

Physical groups may use coherent same-face tracks or selective real vias.
Proposals and their forward continuations coexist before commit. This is a local
interface recipe; full native CAD/connectivity/firmware acceptance is separate.
"""
import math
import time
from smilepcb.native_worker import p,snapshot
from smilepcb.routing_style import octile_paths
from .local_router import LocalRouter,xy,vec,distance
from .native_graph import copper_graph
from .safety import require_via_land_clearance
from .bank_geometry import escape_path,compatible_bank,clean_port_path,port_outside_bounds


def _select(domains,compatible,deadline,max_states):
    """Score and forward-check every remaining physical interface."""
    cache={};states=0;stats={'max_assigned':0,'deepest_groups':[],'last_conflict':None,'backjumps':0,'zero_support_values':0}
    def allowed(i,a,j,mask):
        key=(i,a,j)
        if key not in cache:
            bits=0
            for k,b in enumerate(domains[j]):
                if time.monotonic()>=deadline:raise TimeoutError('Port compatibility budget exhausted')
                if compatible(domains[i][a],b):bits|=1<<k
            cache[key]=bits
        return cache[key]&mask
    def visit(masks,causes,chosen):
        nonlocal states
        if len(chosen)>stats['max_assigned']:
            stats.update(max_assigned=len(chosen),deepest_groups=sorted(chosen))
        if not masks:return chosen,set()
        if time.monotonic()>=deadline or states>=max_states:
            raise TimeoutError('Joint port search budget exhausted')
        i=min(masks,key=lambda k:(bin(masks[k]).count('1'),k));bits=masks[i]
        conflicts=set(causes[i])
        neighbours=sorted((j for j in masks if j!=i),key=lambda j:(bin(masks[j]).count('1'),j))
        values=[]
        while bits:
            bit=bits & -bits;bits^=bit;a=bit.bit_length()-1
            supports={j:allowed(i,a,j,masks[j]) for j in neighbours}
            blocked=next((j for j in masks if j!=i and not supports[j]),None)
            if blocked is not None:
                stats['zero_support_values']+=1
                stats['last_conflict']=[i,blocked]
                conflicts.update(causes[blocked]);continue
            loss=sum(1-bin(supports[j]).count('1')/bin(masks[j]).count('1') for j in neighbours)
            values.append((loss,a))
        if time.monotonic()>=deadline:raise TimeoutError('Joint port search budget exhausted')
        for _,a in sorted(values):
            if time.monotonic()>=deadline or states>=max_states:
                raise TimeoutError('Joint port search budget exhausted')
            states+=1
            remaining={};reasons={};valid=True
            for j,mask in masks.items():
                if j==i:continue
                filtered=allowed(i,a,j,mask)
                if not filtered:
                    stats['last_conflict']=[i,j]
                    conflicts.update(causes[j]);valid=False;break
                remaining[j]=filtered
                reasons[j]=causes[j]|({i} if filtered!=mask else set())
            if valid:
                result,failed=visit(remaining,reasons,{**chosen,i:a})
                if result is not None:return result,set()
                if i not in failed:
                    stats['backjumps']+=1
                    return None,failed
                conflicts.update(failed-{i})
        return None,conflicts
    try:
        masks={i:(1<<len(rows))-1 for i,rows in enumerate(domains)}
        result,_=visit(masks,{i:set() for i in masks},{})
    except TimeoutError as error:
        result=None;stats.update(budget_exhausted=True,budget_reason=str(error))
    stats.update(states=states,compatibility_masks=len(cache))
    return result,stats


def _select_access_domains(domains,compatible,deadline,max_states,prefer_same_layer,select):
    """Allocate one bounded search portfolio and retain selected domain indices."""
    selected=None;preferred_stats=None;states_left=max_states;skipped_identical=False
    if prefer_same_layer and max_states>=2:
        preferred=[[c for c in rows if c.get('kind')=='same_layer'] or rows for rows in domains]
        identical=all(len(a)==len(b) and all(x is y for x,y in zip(a,b)) for a,b in zip(preferred,domains))
        if identical:
            # A second fresh search would repeat the same half-budget prefix.
            skipped_identical=True
        else:
            preferred_stop=min(deadline,time.monotonic()+min(5,max(0,deadline-time.monotonic())/3))
            selected,preferred_stats=select(preferred,compatible,preferred_stop,max(1,max_states//2))
            states_left=max_states-preferred_stats['states']
            if selected is not None:domains=preferred;stats=preferred_stats
    if selected is None:
        selected,stats=select(domains,compatible,deadline,states_left)
    stats=dict(stats)
    if preferred_stats is not None:stats['preferred_bank_search']=preferred_stats
    if skipped_identical:stats['preferred_bank_search_skipped']='identical_domains'
    return selected,domains,stats


def _port_collides(a,b,clearance_nm,bounds):
    """Reject separated immutable candidate bounds before exact native checks."""
    def box(shape):
        key=id(shape)
        if key not in bounds:
            bb=shape.BBox()
            bounds[key]=(bb.GetLeft(),bb.GetTop(),bb.GetRight(),bb.GetBottom())
        return bounds[key]
    aa,bb=box(a),box(b)
    if (aa[0]>bb[2]+clearance_nm or bb[0]>aa[2]+clearance_nm or
        aa[1]>bb[3]+clearance_nm or bb[1]>aa[3]+clearance_nm):return False
    return p.SHAPE.Collide(a,b,clearance_nm)


def _compatible_ports(world,a,z,clearance_nm,*,bounds=None):
    """Check actual planned copper and drills before committing a bank."""
    gap=world.generation.via_to_hole_clearance_nm
    bounds={} if bounds is None else bounds
    if ((a.get('circle') is not None and z.get('hole') is not None and _port_collides(a['circle'],z['hole'],gap,bounds)) or
        (z.get('circle') is not None and a.get('hole') is not None and _port_collides(z['circle'],a['hole'],gap,bounds))):return False
    if a['net']==z['net']:return True
    clearance_nm=max(clearance_nm,world.generation.foreign_copper_clearance_nm)
    return not any(face==other and _port_collides(shape,b,clearance_nm,bounds)
                   for face,shape in a['shapes'] for other,b in z['shapes'])


def _coherent_via_candidate(world,geometry,pad_ids,lands,clearance_nm,continuation_nm,prefer_same_layer,deadline,diagnostic):
    """Build the normal via row through the existing Native candidate predicates."""
    def reject(reason):diagnostic[reason]=diagnostic.get(reason,0)+1;return None
    if time.monotonic()>=deadline:raise TimeoutError('Coherent via candidate deadline exhausted')
    net=geometry['net'];end=geometry['via_nm'];face=geometry['layer'];other=geometry['exit_layer'];exit_point=geometry['exit_nm']
    path=clean_port_path(geometry['path'])
    if path is None or tuple(path[-1])!=tuple(end):return reject('source-shape')
    if not world.via_clear(end,net):return reject('via-clearance')
    circle=p.SHAPE_CIRCLE(vec(end),world.via_width//2)
    if any(p.SHAPE.Collide(circle,land,clearance_nm) for land in lands):return reject('solder-land-clearance')
    runway=p.SHAPE_SEGMENT(vec(end),vec(exit_point),world.track_width)
    if not world.clear(runway,other,net):return reject('runway-clearance')
    future=None
    if continuation_nm:
        future=p.SHAPE_SEGMENT(vec(exit_point),vec(geometry['future_end_nm']),world.track_width)
        if not world.clear(future,other,net):return reject('continuation-clearance')
    segments=[p.SHAPE_SEGMENT(vec(a),vec(z),world.track_width) for a,z in zip(path,path[1:])]
    for shape in segments:
        if time.monotonic()>=deadline:raise TimeoutError('Coherent via candidate deadline exhausted')
        if not world.clear(shape,face,net):return reject('source-lead-clearance')
    diagnostic['native-legal']=diagnostic.get('native-legal',0)+1
    return dict(geometry,pad_ids=list(pad_ids),path=path,circle=circle,hole=p.SHAPE_CIRCLE(vec(end),world.drill//2),
        shapes=[(face,shape) for shape in segments]+[(p.F_Cu,circle),(p.B_Cu,circle),(other,runway)]+([] if future is None else [(other,future)]),
        cost=(20000000 if prefer_same_layer else 0)+sum(distance(a,z) for a,z in zip(path,path[1:]))+distance(end,exit_point))


def route_controller_ports(board,*,controller_refs,mcu_refs,plane_nets=(),usb_nets=(),
                           deadline,clearance_nm=200000,max_distance_nm=4500000,
                           runway_nm=1000000,max_candidates=96,max_states=12000,
                           include_auxiliary_returns=False,include_nets=None,
                           access_mode='via',continuation_nm=0,coherent_banks=True,bank_pitch_nm=None,exit_bounds_nm=None,head_constraint=None,source_budget=None,coherent_via_banks=False):
    """Select feasible legacy sources, optionally prefer whole coherent groups.

    The opt-in refines the selected set in memory; no cohort is required to
    use vias when its compatible coherent menu is empty.
    """
    if type(coherent_via_banks) is not bool:raise ValueError('Coherent via banks must be boolean')
    if coherent_via_banks and include_nets is None:raise ValueError('Coherent via banks require explicit selected signal nets')
    if access_mode not in ('via','prefer_same_layer'):raise ValueError('Unknown access mode')
    if bank_pitch_nm is not None and (type(bank_pitch_nm) is not int or not 300000<=bank_pitch_nm<=1000000):raise ValueError('Unsupported native bank pitch')
    if type(coherent_banks) is not bool:raise ValueError('Bank coherence must be boolean')
    if type(continuation_nm) is not int or not 0<=continuation_nm<=2000000:raise ValueError('Unsupported forward continuation')
    if include_nets is not None and (not isinstance(include_nets,(list,tuple,set,frozenset)) or any(type(n) is not str or not n for n in include_nets)):
        raise ValueError('Controller selection requires explicit net names')
    started=time.monotonic();before=snapshot(board);added=[]
    refs=set(controller_refs);mcus=set(mcu_refs);fps={f.GetReference():f for f in board.GetFootprints()}
    if not mcus or not mcus<=refs or not refs<=set(fps):raise ValueError('Controller interfaces require existing MCU/controller references')
    if exit_bounds_nm is not None and (not isinstance(exit_bounds_nm,dict) or set(exit_bounds_nm)-mcus or
        any(not isinstance(box,(list,tuple)) or len(box)!=4 or any(type(v) is not int for v in box) or box[0]>=box[2] or box[1]>=box[3] for box in exit_bounds_nm.values())):
        raise ValueError('Outward controller bounds require native MCU rectangles')
    if type(include_auxiliary_returns) is not bool:raise ValueError('Auxiliary return selection must be boolean')
    if deadline<=started:raise ValueError('Controller interfaces require a positive deadline')
    if (any(type(value) is not int for value in (clearance_nm,max_distance_nm,runway_nm,max_candidates,max_states)) or
        clearance_nm<board.GetDesignSettings().m_MinClearance or
        not 150000<=clearance_nm<=500000 or not 800000<=max_distance_nm<=6000000 or
        not 500000<=runway_nm<=2000000 or not 1<=max_candidates<=1024 or not 1<=max_states<=100000):
        raise ValueError('Unsupported bounded controller interface dimensions/search limits')
    if any(not z.GetIsRuleArea() for z in board.Zones()):raise ValueError('Controller interfaces require a fresh board without plane fills')
    report={'accepted':False,'ports':[],'added_ids':[],'already_accessible':[],'groups':0}
    try:
        nets={d.GetNetname() for name in mcus for d in fps[name].Pads()
              if d.GetNumber() and d.GetNetname() and not d.GetNetname().startswith(('unconnected-','NC_'))}
        nets=(nets|set(plane_nets))-set(usb_nets)
        if include_nets is not None:nets &= set(include_nets)
        graph=copper_graph(board,nets,deadline);groups=[]
        for indices in graph['groups'].values():
            pads=[graph['nodes'][i]['item'] for i in indices if isinstance(graph['nodes'][i]['item'],p.PAD)
                  and graph['nodes'][i]['item'].GetNumber() and graph['nodes'][i]['item'].GetParentFootprint().GetReference() in refs]
            if not pads:continue
            net=pads[0].GetNetname()
            # This stage reserves MCU interfaces. Auxiliary SMD returns can
            # contact their native plane directly; forcing a through-via on
            # every such pad needlessly consumes the GPIO escape area.
            if not any(d.GetParentFootprint().GetReference() in mcus for d in pads):
                if not include_auxiliary_returns or net not in plane_nets:continue
            # An old via alone may still be trapped inside the chip's local
            # copper. Only an actual external pad already reached by the
            # physical group substitutes for a new outward interface.
            if any(isinstance(graph['nodes'][i]['item'],p.PAD) and graph['nodes'][i]['item'].GetNumber() and
                   graph['nodes'][i]['item'].GetParentFootprint().GetReference() not in refs for i in indices):
                report['already_accessible'].append({'net':net,'pad_ids':[d.m_Uuid.AsString() for d in pads]});continue
            groups.append((net,pads))
        groups.sort(key=lambda row:(row[0],sorted((d.GetParentFootprint().GetReference(),d.GetNumber()) for d in row[1])))
        report['groups']=len(groups)
        world=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
        world.minimum_clearance=max(world.minimum_clearance,clearance_nm)
        world.net_clearances={key:max(value,clearance_nm) for key,value in world.net_clearances.items()}
        bounds={}
        for name in mcus:
            boxes=[d.GetEffectiveShape(face).BBox() for d in fps[name].Pads()
                   for face in (p.F_Cu,p.B_Cu) if d.IsOnLayer(face)]
            bounds[name]=(min(b.GetLeft() for b in boxes),min(b.GetTop() for b in boxes),
                          max(b.GetRight() for b in boxes),max(b.GetBottom() for b in boxes))
        outlet_bounds={} if exit_bounds_nm is None else exit_bounds_nm
        for ref,box in outlet_bounds.items():
            bb=bounds[ref]
            if box[0]>bb[0] or box[1]>bb[1] or box[2]<bb[2] or box[3]<bb[3]:
                raise ValueError('Outward frame must enclose MCU pad copper')
        lands=[d.GetEffectiveShape(face) for f in board.GetFootprints() for d in f.Pads()
               if d.GetAttribute()==p.PAD_ATTRIB_SMD for face in (p.F_Cu,p.B_Cu) if d.IsOnLayer(face)]
        shifts=sorted(((normal,tangent) for normal in [sign*v for v in range(800000,max_distance_nm+1,400000) for sign in (-1,1)]
                       for tangent in [0]+[sign*v for v in range(300000,2100001,300000) for sign in (-1,1)]
                       if math.hypot(normal,tangent)<=max_distance_nm),
                      key=lambda row:(abs(row[0])*(2 if row[0]<0 else 1)+(math.sqrt(2)-1)*abs(row[1]),row))
        bank_members={}
        for signal,group_pads in groups:
            for d in group_pads:
                ref=d.GetParentFootprint().GetReference()
                if ref not in mcus:continue
                point=xy(d.GetPosition());center=xy(fps[ref].GetPosition());delta=(point[0]-center[0],point[1]-center[1]);axis=0 if abs(delta[0])>abs(delta[1]) else 1;sign=1 if delta[axis]>=0 else -1
                bank_members.setdefault((ref,axis,sign),{})[signal]=point
        domains=[]
        for net,pads in groups:

            pad_ids=[d.m_Uuid.AsString() for d in pads]
            candidate_limit=max_candidates if source_budget is None else source_budget.candidate_limit(pad_ids,max_candidates)
            if candidate_limit<=0:
                report.update(reason='Shared source candidate budget exhausted',missing_net=net,budget_exhausted=True);return report
            choices=[];seen=set();diagnostic={'via_clear':0,'solder_land_clear':0,'runway_clear':0,'lead_clear':0}
            anchors=sorted(pads,key=lambda d:(d.GetParentFootprint().GetReference() in mcus,
                            len(list(d.GetParentFootprint().Pads())),d.GetParentFootprint().GetReference(),d.GetNumber()))
            for pad in anchors:
                origin=xy(pad.GetPosition());nearest=min(mcus,key=lambda name:distance(origin,xy(fps[name].GetPosition())))
                center=xy(fps[nearest].GetPosition())
                delta=(origin[0]-center[0],origin[1]-center[1]);axis=0 if abs(delta[0])>abs(delta[1]) else 1;sign=1 if delta[axis]>=0 else -1
                faces=[face for face in (p.F_Cu,p.B_Cu) if pad.IsOnLayer(face)]
                if len(faces)!=1:continue
                face=faces[0];other=p.B_Cu if face==p.F_Cu else p.F_Cu
                if access_mode=='prefer_same_layer' and net not in plane_nets:
                    edge=bounds[nearest][axis+(2 if sign>0 else 0)]
                    box=pad.GetEffectiveShape(face).BBox()
                    pad_edge=((box.GetRight() if axis==0 else box.GetBottom()) if sign>0 else (box.GetLeft() if axis==0 else box.GetTop()))
                    neck_nm=abs(pad_edge-origin[axis])+max(clearance_nm,world.net_clearances.get(net,0))+world.track_width//2+1000
                    members=bank_members.get((nearest,axis,sign),{})
                    pitch=bank_pitch_nm or max(350000,world.track_width+clearance_nm+20000)
                    shifts_with_profiles=[] if bank_pitch_nm is not None else [(t,('shift',t)) for t in (0,-500000,500000,-1000000,1000000)]
                    if net in members:
                        order=sorted(members,key=lambda n:(members[n][1-axis],n));middle=(members[order[0]][1-axis]+members[order[-1]][1-axis])/2
                        desired=round(middle+(order.index(net)-(len(order)-1)/2)*pitch)
                        shifts_with_profiles.append((desired-origin[1-axis],('pitch',pitch)))
                    for gap in (600000,1000000,1400000,2000000,2600000,3400000,4200000):
                        for tangent,bank_profile in shifts_with_profiles:
                            path=escape_path(origin,axis,sign,edge+sign*gap,tangent,neck_nm)
                            if path is None:continue
                            path=clean_port_path(path)
                            if path is None:continue
                            segments=[p.SHAPE_SEGMENT(vec(a),vec(z),world.track_width) for a,z in zip(path,path[1:])]
                            heading=tuple((path[-1][i]>path[-2][i])-(path[-1][i]<path[-2][i]) for i in (0,1))
                            if nearest in outlet_bounds and not port_outside_bounds(path[-1],heading,outlet_bounds[nearest],world.track_width):continue
                            future_end=tuple(path[-1][i]+heading[i]*continuation_nm for i in (0,1))
                            future=p.SHAPE_SEGMENT(vec(path[-1]),vec(future_end),world.track_width)
                            if not all(world.clear(shape,face,net) for shape in segments+[future]):continue
                            choices.append({'net':net,'pad_ids':[d.m_Uuid.AsString() for d in pads],
                                'kind':'same_layer','bank':(nearest,axis,sign),'profile':(gap,bank_profile),
                                'path':path,'layer':face,'via_nm':None,'heading_origin_nm':path[-2],
                                'exit_nm':path[-1],'exit_layer':face,'axis':axis,'sign':sign,'circle':None,'hole':None,
                                'shapes':[(face,shape) for shape in segments+[future]],
                                'cost':sum(distance(a,z) for a,z in zip(path,path[1:]))})
                for normal,tangent in shifts:
                    if time.monotonic()>=deadline:raise TimeoutError('Port candidate generation budget exhausted')
                    end=list(origin);end[axis]+=sign*normal;end[1-axis]+=tangent;end=tuple(end)
                    if not world.via_clear(end,net):continue
                    diagnostic['via_clear']+=1
                    circle=p.SHAPE_CIRCLE(vec(end),world.via_width//2)
                    if any(p.SHAPE.Collide(circle,land,clearance_nm) for land in lands):continue
                    diagnostic['solder_land_clear']+=1
                    # A via may sit under a leaded package: escape on its
                    # opposite face all the way outside the pad envelope.
                    # Ground-plane access needs a real via, not an artificial
                    # outward signal trace through the plane.
                    exit_point=list(end);plane=net in plane_nets
                    if not plane:
                        edge=outlet_bounds.get(nearest,bounds[nearest])[axis+(2 if sign>0 else 0)]
                        margin=world.track_width//2+1 if nearest in outlet_bounds else runway_nm
                        exit_point[axis]=(max(end[axis]+runway_nm,edge+margin) if sign>0 else
                                          min(end[axis]-runway_nm,edge-margin))
                    exit_point=tuple(exit_point)
                    exits=[exit_point]
                    if not plane:
                        tangent_axis=1-axis
                        for tangent_sign in (-1,1):
                            point=list(end);edge=outlet_bounds.get(nearest,bounds[nearest])[tangent_axis+(2 if tangent_sign>0 else 0)]
                            point[tangent_axis]=(max(end[tangent_axis]+runway_nm,edge+margin) if tangent_sign>0 else
                                                 min(end[tangent_axis]-runway_nm,edge-margin))
                            exits.append(tuple(point))
                    paths=list(octile_paths(origin,end))
                    # Leave the narrow pad bank straight before changing its
                    # tangent coordinate. Diagonal-first and diagonal-last
                    # paths alone cannot reach laterally displaced vias when
                    # the required tangent shift exceeds the normal distance.
                    box=pad.GetEffectiveShape(face).BBox()
                    direction=sign if normal>0 else -sign
                    edge=((box.GetRight() if axis==0 else box.GetBottom()) if direction>0 else
                          (box.GetLeft() if axis==0 else box.GetTop()))
                    margin=max(world.minimum_clearance,world.net_clearances.get(net,0))+world.track_width//2+1000
                    launch=list(origin);launch[axis]=edge+direction*margin;launch=tuple(launch)
                    if (end[axis]-launch[axis])*direction>=0:
                        paths.extend([[origin,*path] for path in octile_paths(launch,end)])
                    for exit_point in exits:
                        runway=None if plane else p.SHAPE_SEGMENT(vec(end),vec(exit_point),world.track_width)
                        if runway is not None and not world.clear(runway,other,net):continue
                        future=None
                        if not plane and continuation_nm:
                            heading=tuple((exit_point[i]>end[i])-(exit_point[i]<end[i]) for i in (0,1))
                            future_end=tuple(exit_point[i]+heading[i]*continuation_nm for i in (0,1))
                            future=p.SHAPE_SEGMENT(vec(exit_point),vec(future_end),world.track_width)
                            if not world.clear(future,other,net):continue
                        diagnostic['runway_clear']+=1
                        for path in paths:
                            path=clean_port_path(path)
                            if path is None:continue
                            key=(tuple(path),face,end,exit_point)
                            if key in seen:continue
                            segments=[p.SHAPE_SEGMENT(vec(a),vec(z),world.track_width) for a,z in zip(path,path[1:])]
                            if not all(world.clear(shape,face,net) for shape in segments):continue
                            diagnostic['lead_clear']+=1
                            seen.add(key);choices.append({'net':net,'pad_ids':[d.m_Uuid.AsString() for d in pads],
                                'kind':'via','path':path,'layer':face,'via_nm':end,'exit_nm':exit_point,'exit_layer':other,'axis':axis,'sign':sign,
                                'circle':circle,'hole':p.SHAPE_CIRCLE(vec(end),world.drill//2),
                                'shapes':[(face,shape) for shape in segments]+[(p.F_Cu,circle),(p.B_Cu,circle)]+([] if plane else [(other,runway)])+([] if future is None else [(other,future)]),
                                'cost':(20000000 if access_mode=='prefer_same_layer' else 0)+sum(distance(a,z) for a,z in zip(path,path[1:]))+distance(end,exit_point)})
                    if len(choices)>=candidate_limit:break
                if len(choices)>=candidate_limit:break
            if not choices:
                report.update(reason='No legal outward interface candidates',missing_net=net,candidate_diagnostic=diagnostic);return report
            domains.append(sorted(choices,key=lambda c:(c['cost'],c['via_nm'] or c['exit_nm'],c['path']))[:candidate_limit])
            if source_budget is not None:source_budget.charge_candidates(pad_ids,len(domains[-1]))
        report['candidate_counts']=[len(rows) for rows in domains]
        report['group_labels']=[{'net':net,'pads':sorted(d.GetParentFootprint().GetReference()+':'+d.GetNumber() for d in pads)} for net,pads in groups]
        # All candidate shapes stay alive and immutable throughout selection.
        bounds_cache={}
        def compatible(a,z):
            return (compatible_bank(a,z,same_layer=coherent_banks) if coherent_via_banks else
                    (not coherent_banks or compatible_bank(a,z))) and _compatible_ports(world,a,z,clearance_nm,bounds=bounds_cache)
        select=_select;accepts=None
        if head_constraint is not None:
            from .conditional_sources import select_head_set,select_constrained_head_set,geometry_signature
            from .conditional_geometry import shapes_for_candidate_rows
            if (not isinstance(head_constraint,dict) or set(head_constraint)!={'original_geometry','unchanged_heads'} or
                    not isinstance(head_constraint['original_geometry'],str) or not isinstance(head_constraint['unchanged_heads'],dict)):
                raise ValueError('Invalid physical head geometry constraint')
            required=head_constraint['unchanged_heads']
            def signature(rows):
                return geometry_signature(shapes_for_candidate_rows(rows,world.track_width,world.via_width,world.drill))
            def accepts(rows):
                shapes=shapes_for_candidate_rows(rows,world.track_width,world.via_width,world.drill)
                if geometry_signature(shapes)==head_constraint['original_geometry']:return False
                for pad_ids,old in required.items():
                    selected_rows=[row for row in rows if set(row['pad_ids']) & set(pad_ids)]
                    if set().union(*(set(row['pad_ids']) for row in selected_rows))!=set(pad_ids):return False
                    if signature(selected_rows)==old:return False
                return True
            def select(ds,compatible,stop,states):
                if not required:return select_head_set(ds,compatible,stop,states,accept_complete=accepts)
                return select_constrained_head_set(ds,compatible,stop,states,required_heads=required,
                    signature=lambda row:signature([row]),accept_complete=accepts)
        if source_budget is not None:
            select=source_budget.selector(select);max_states=min(max_states,source_budget.states_left)
        selected,domains,stats=_select_access_domains(domains,compatible,deadline,max_states,access_mode=='prefer_same_layer',select)
        report.update(stats)
        if selected is None:
            report['reason']=stats.get('budget_reason','No mutually clear short interfaces within the candidate set');return report
        chosen={i:domains[i][selected[i]] for i in selected}
        if coherent_via_banks:
            now=time.monotonic();remaining=max(0.,deadline-now)
            # Refinement is optional. Keep at least half the existing tail,
            # with a one-second floor, for normal source commit/native proof.
            # This partitions the original window; it grants no new time.
            commit_reserve=max(1.,remaining/2)
            refinement_deadline=max(now,deadline-commit_reserve)
            report['coherent_via_banks']=dict(enabled=True,cohorts=[],legacy_nets={},
                semantics='precommit_whole_cohort_preference',source_deadline=deadline,
                refinement_deadline=refinement_deadline,commit_reserve_s=min(remaining,commit_reserve),
                complete_coherence=False,coherence_scope='discovered supported physical cohorts only')
            if remaining<=commit_reserve:
                report['coherent_via_banks']['outcome']='skipped_commit_reserve'
            else:
                try:
                    from .coherent_via_banks import discover_cohorts,cohort_geometry,refine_cohort_selection
                    records=[]
                    for ref in sorted(mcus):
                        footprint=fps[ref];center=xy(footprint.GetPosition())
                        angle=footprint.GetOrientationDegrees();cardinal=abs(angle/90-round(angle/90))<1e-6
                        for pad in footprint.Pads():
                            if time.monotonic()>=refinement_deadline:raise TimeoutError('Physical cohort inventory deadline exhausted')
                            point=xy(pad.GetPosition());delta=(point[0]-center[0],point[1]-center[1])
                            axis=0 if abs(delta[0])>abs(delta[1]) else 1;sign=1 if delta[axis]>=0 else -1
                            heading=(sign,0) if axis==0 else (0,sign)
                            faces=tuple(face for face in (p.F_Cu,p.B_Cu) if pad.IsOnLayer(face))
                            box=pad.GetEffectiveShape(faces[0]).BBox() if len(faces)==1 else None
                            records.append(dict(ref=ref,pad_id=pad.m_Uuid.AsString(),net=pad.GetNetname(),point=point,faces=faces,heading=heading,
                                supported=cardinal and bool(pad.GetNumber()),bounds_nm=None if box is None else
                                    (box.GetLeft(),box.GetTop(),box.GetRight(),box.GetBottom())))
                    eligible={net for net,pads in groups if net not in plane_nets and sum(n==net for n,_ in groups)==1}
                    cohorts,legacy=discover_cohorts(records,eligible,deadline=refinement_deadline)
                    report['coherent_via_banks'].update(cohorts=[],legacy_nets=legacy)
                    lookup={net:pads for net,pads in groups}
                    report['coherent_via_banks']['semantics']='precommit_whole_cohort_preference'
                    for net,pads in groups:
                        if not any(net in cohort['members'] for cohort in cohorts):
                            legacy.setdefault(net,'unsupported, nonselected, or singleton physical cohort')
                    def menu(cohort,limit,charge):
                        members=cohort['members'];ref=cohort['ref'];face=cohort['face'];other=p.B_Cu if face==p.F_Cu else p.F_Cu
                        geometry=cohort_geometry(cohort,body_face=other,width_nm=world.track_width,via_width_nm=world.via_width,
                            via_drill_nm=world.drill,clearance_nm=clearance_nm,
                            track_margin_nm=max(clearance_nm,world.generation.foreign_copper_clearance_nm),
                            via_land_margin_nm=max(clearance_nm,world.generation.via_to_smd_clearance_nm),drill_land_margin_nm=p.FromMM(.1),
                            runway_nm=runway_nm,exit_bounds_nm=outlet_bounds.get(ref,bounds[ref]),explicit_exit_bounds=ref in outlet_bounds,
                            maximum_distance_nm=max_distance_nm,continuation_nm=continuation_nm,deadline=refinement_deadline)
                        # All rows are vias, so the existing same-layer via penalty is
                        # constant within this whole-profile menu. Actual length gives
                        # the same stable cost ordering before Native consideration.
                        def length(profile):
                            return sum(sum(distance(a,b) for a,b in zip(row['path'],row['path'][1:]))+
                                distance(row['via_nm'],row['exit_nm']) for row in profile)
                        geometry=sorted(geometry,key=lambda profile:(length(profile),repr(profile[0]['cohort_profile'])))
                        profiles=[];diagnostic={n:{} for n in members}
                        for profile in geometry[:limit]:
                            rows=[]
                            for row in profile:
                                net=row['net'];charge(net)
                                choice=_coherent_via_candidate(world,row,[d.m_Uuid.AsString() for d in lookup[net]],lands,
                                    clearance_nm,continuation_nm,access_mode=='prefer_same_layer',refinement_deadline,diagnostic[net])
                                if choice is not None:rows.append(choice)
                            if len(rows)==len(members):profiles.append(rows)
                        return profiles,dict(generated_profiles=len(geometry),considered_profiles=min(limit,len(geometry)),
                            retained_profiles=len(profiles),native_diagnostic=diagnostic)
                    # Every candidate shape remains alive for the cohort's complete
                    # peer checks; a fresh cache avoids reusing IDs of discarded menus.
                    def build_menu(cohort,limit,charge):
                        refinement_bounds.clear()
                        return menu(cohort,limit,charge)
                    refinement_bounds={}
                    def refinement_compatible(a,b):
                        return compatible_bank(a,b,same_layer=coherent_banks) and _compatible_ports(world,a,b,clearance_nm,bounds=refinement_bounds)
                    indices=sorted(chosen)
                    refined,receipts=refine_cohort_selection([chosen[i] for i in indices],cohorts,build_menu,
                        compatible=refinement_compatible,deadline=refinement_deadline,max_candidates=max_candidates,source_budget=source_budget,
                        accept_complete=accepts)
                    chosen=dict(zip(indices,refined));report['coherent_via_banks']['cohorts']=receipts
                    report['coherent_via_banks']['complete_coherence']=bool(receipts) and all(
                        row['after']['coherence']!='mixed_or_unprofiled' for row in receipts)
                    report['coherent_via_banks']['coherence_scope']='discovered supported physical cohorts only'
                except TimeoutError as error:
                    # Discovery can also expire before the pure preference
                    # helper is entered. No selected copper has been emitted.
                    report['coherent_via_banks'].update(outcome='skipped_deadline',reason=str(error))
        for i in sorted(selected):
            if time.monotonic()>=deadline:raise TimeoutError('Port commit budget exhausted')
            row=chosen[i];mark=len(world.added)
            world.add_path(row['path'],row['layer'],row['net']);via_id=None
            if row.get('kind','via')=='via':
                world.add_via(row['via_nm'],row['net']);via_id=world.added[-1]
                if row['via_nm']!=row['exit_nm']:world.add_path([row['via_nm'],row['exit_nm']],row['exit_layer'],row['net'])
            added=world.added[:]
            report['ports'].append({key:row[key] for key in ('net','pad_ids','via_nm','exit_nm','exit_layer','axis','sign')})
            report['ports'][-1].update(kind=row.get('kind','via'),heading_origin_nm=row.get('heading_origin_nm',row['via_nm']),
                bank=row.get('bank'),profile=row.get('profile'),via_id=via_id,terminal_id=world.added[-1],added_ids=world.added[mark:])
            if row.get('cohort') is not None:
                report['ports'][-1].update({key:row[key] for key in ('cohort','cohort_pad_ids','cohort_members','cohort_profile','escape_side','rail')})
        require_via_land_clearance(board,deadline=deadline)
        after=snapshot(board)
        if after['footprints']!=before['footprints'] or any(after['tracks'].get(uid)!=shape for uid,shape in before['tracks'].items()):
            raise ValueError('Controller interfaces changed original components/copper')
        actual=copper_graph(board,nets,deadline);ids={node['item'].m_Uuid.AsString():i for i,node in enumerate(actual['nodes'])}
        for port in report['ports']:
            if any(actual['find'](ids[uid])!=actual['find'](ids[port['terminal_id']]) for uid in port['pad_ids']):
                raise ValueError('Saved native pad group does not reach its live access terminal')
        report.update(accepted=True,added_ids=added,access_mode=access_mode,planned_continuation_nm=continuation_nm)
    except TimeoutError as error:
        report['reason']=str(error)
    finally:
        if not report['accepted']:
            for track in list(board.GetTracks()):
                if track.m_Uuid.AsString() not in before['tracks']:board.Delete(track)
            report['ports']=[];report['added_ids']=[]
        report['elapsed_s']=time.monotonic()-started
        if 'coherent_via_banks' in report:report['coherent_via_banks']['source_accepted']=report['accepted']
        if source_budget is not None:report['source_budget']=source_budget.receipt()
    return report
