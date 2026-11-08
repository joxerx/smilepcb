"""Plan a physical shared ribbon in memory; mutate copper only at commit."""
from collections import Counter
import math,time

from smilepcb.native_worker import p,snapshot
from smilepcb.routing_quality import QualityProfile,copper_metrics
from .virtual_copper import VirtualCopper
from .complete_choice import CompleteChoice
from .local_router import LocalRouter,vec
from .obstacle_axis import search_corridor
from .collector_capacity import Search,DIRS,shifted,clean
from .corridor_geometry import offset_path
from .bundle_geometry import paths_clear
from .polish_geometry import valid_shape
from .column_bridges import bridge_carrier
from .layered_simple_route import safe_layered_route as safe_route
from .ribbon_port_candidates import port_pairs,departure_possible
from .attachment_policy import TerminalStyle,ranked_departures
from .bundle_polish import retained_contacts_clear,_outside_caps
from .via_junction_polish import _pad_contacts_clear
from .visual_polish import _parts,_copy,_refill


class _Style:
    def __init__(self,bounds):self.bounds=bounds
    def accepts_path(self,path):
        return valid_shape(path) and all(self.bounds[0]<=q[0]<=self.bounds[2] and self.bounds[1]<=q[1]<=self.bounds[3] for q in path)


class _Portals(Search):
    """Reuse the existing physically expanded/staggered group portal proof."""
    def __init__(self,world,nets,offsets,step,deadline):
        self.world=self.detail=world;self.nets=nets;self.offsets=offsets;self.step=step;self.deadline=deadline;self.allow_vias=True
        self.width=world.track_width;self.gap=max(world.minimum_clearance,*[world.net_clearances.get(n,0) for n in nets]);self.blockers=Counter()
        self.stats=dict(portal_proposals=0,portal_legal=0);self.portal_cache={};self.origin=(0,0)
    def propose(self,point,heading,face):
        self.origin=point;self.portal_cache={}
        q=self.portal((0,0,DIRS.index(heading),face,0),deadline=self.deadline)
        return None if q is None else dict(end=tuple(q['axis_end']),end_layer=q['end_layer'],cost_nm=400000*len(self.nets),payload=q)


def _terminal_rays(world,point,face,net,ignored_ids=()):
    if not hasattr(world,'_junction_pad_ids'):world._junction_pad_ids={d.m_Uuid.AsString() for fp in world.board.GetFootprints() for d in fp.Pads()}
    probe=p.SHAPE_CIRCLE(vec(point),1)
    if any(uid in world._junction_pad_ids and name==net and p.SHAPE.Collide(probe,shape,0) for cell in world.cells(probe.BBox()) for uid,shape,name,_ in world.buckets[face].get(cell,())):return set()
    rays=set()
    for item in world.track_items().values():
        if item.m_Uuid.AsString() in ignored_ids or item.GetClass()!='PCB_TRACK' or item.GetNetname()!=net or item.GetLayer()!=face:continue
        a,z=(item.GetStart().x,item.GetStart().y),(item.GetEnd().x,item.GetEnd().y)
        if tuple(point)==a:rays.add((z[0]-a[0],z[1]-a[1]))
        if tuple(point)==z:rays.add((a[0]-z[0],a[1]-z[1]))
    return rays


def _join_clear(world,path,face,net,ignored_ids=()):
    for point,next_point in ((path[0],path[1]),(path[-1],path[-2])):
        rays=_terminal_rays(world,point,face,net,ignored_ids)
        new=(next_point[0]-point[0],next_point[1]-point[1])
        if len(rays)==1:
            old=next(iter(rays));dot=sum(a*b for a,b in zip(old,new));norm=sum(v*v for v in old)*sum(v*v for v in new)
            if dot>=0 or 2*dot*dot<norm:return False
    return True


def _add_checked(world,op):
    net=op['net'];world.track_width=op.get('width',world.track_width)
    if op['kind']=='track':
        path=op['path'] if 'path' in op else [op['start'],op['end']]
        if not all(world.clear(p.SHAPE_SEGMENT(vec(a),vec(z),world.track_width),op['layer'],net) for a,z in zip(path,path[1:])):return False
        world.add_path(path,op['layer'],net)
    else:
        if not world.via_clear(op['position'],net):return False
        world.add_via(op['position'],net)
    return True


def _route_from_operations(operations,*,default_via_width=None):
    """Recover one ordered route; emission logs place vias after their legs."""
    legs=[];via_ops={}
    for op in operations:
        if op['kind']=='via':
            point=tuple(op['position'])
            if point in via_ops:return None
            via_ops[point]=op;continue
        path=list(map(tuple,op.get('path',[op.get('start'),op.get('end')])))
        if len(path)<2:continue
        face=op['layer']
        if legs and tuple(legs[-1][1][-1])!=path[0]:return None
        if legs and legs[-1][0]==face:legs[-1][1].extend(path[1:])
        else:legs.append((face,path))
    if not legs:return dict(legs=[],vias=[])
    legs=[(f,clean(path)) for f,path in legs]
    vias=[tuple(a[1][-1]) for a,b in zip(legs,legs[1:])]
    if any(q not in via_ops for q in vias):return None
    ends={tuple(legs[0][1][0]),tuple(legs[-1][1][-1])}
    if any(q not in vias and not (op.get('fixed') and q in ends) for q,op in via_ops.items()):return None
    widths=[via_ops[q].get('width',default_via_width) for q in vias]
    if any(not isinstance(w,(int,float)) or w<=0 for w in widths):return None
    return dict(legs=legs,vias=vias,via_widths=widths)


def _own_contacts_clear(world,route,net,width,*,ignored_ids=(),endpoints=None):
    """Permit only declared terminal contacts, including both faces of vias."""
    ignored=set(ignored_ids);items=world.track_items()
    if endpoints is None:endpoints=(route['legs'][0][1][0],route['legs'][-1][1][-1])
    def contacts(shape,face):
        seen=set()
        for cell in world.cells(shape.BBox()):
            for uid,other,name,_ in world.buckets[face].get(cell,()):
                if uid in seen or uid in ignored or name!=net:continue
                seen.add(uid)
                if p.SHAPE.Collide(shape,other,0):yield uid,other
    def caps(uid,shape,diameter):
        item=items.get(uid)
        if item is None:
            box=shape.BBox();radius=math.hypot(box.GetWidth(),box.GetHeight())/2+diameter/2+2000
        else:radius=(diameter+(item.GetWidth() if item.GetClass()=='PCB_TRACK' else item.GetWidth(p.F_Cu)))/2+2000
        return [(tuple(q),radius) for q in endpoints if p.SHAPE.Collide(p.SHAPE_CIRCLE(vec(q),1),shape,0)]
    for face,path in route['legs']:
        for a,z in zip(path,path[1:]):
            for uid,shape in contacts(p.SHAPE_SEGMENT(vec(a),vec(z),width),face):
                if any(p.SHAPE.Collide(p.SHAPE_SEGMENT(vec(u),vec(v),width),shape,0) for u,v in _outside_caps(a,z,caps(uid,shape,width))):return False
    for q in route['vias']:
        for face in (p.F_Cu,p.B_Cu):
            for uid,shape in contacts(p.SHAPE_CIRCLE(vec(q),world.via_width//2),face):
                if not any(math.dist(q,e)<=radius for e,radius in caps(uid,shape,world.via_width)):return False
    return True


def _physical_connection(world,route,a,z,net,bounds,*,emit=True,admit_continuation=None,diagnostics=None):
    """Shared complete _connect admission for searched and joint proposals."""
    width=world.track_width
    def reject(reason,**evidence):
        if hasattr(world,'connection_failures'):world.connection_failures[reason]+=1
        if diagnostics is not None:diagnostics.update(reason=reason,**evidence)
        return False
    if route is None:return reject('no-layer-plan')
    if not safe_route(route,width,world.via_width):return reject('self-contact')
    if admit_continuation is not None and not admit_continuation(route):return reject('continuation-self-contact')
    for face,path in route['legs']:
        if not _Style(bounds).accepts_path(path):return reject('geometry-policy')
        if not _join_clear(world,path,face,net):return reject('endpoint-heading')
        if not _pad_contacts_clear(world.board,path,face,net,width,world):return reject('pad-contact')
        if not retained_contacts_clear(world.board,path,face,net,width,ignored_ids=world.ignored_ids,oracle=world,track_items=world.track_items()):return reject('retained-track-contact')
        for x,y in zip(path,path[1:]):
            shape=p.SHAPE_SEGMENT(vec(x),vec(y),width)
            if not world.clear(shape,face,net):
                from itertools import islice
                collisions=list(islice(world.collisions(shape,face,net),8)) if diagnostics is not None else []
                return reject('physical-track-clearance',layer=face,segment=[x,y],collisions=collisions)
    if not _own_contacts_clear(world,route,net,width,ignored_ids=world.ignored_ids,endpoints=(a,z)):return reject('own-contact')
    for v in route['vias']:
        if not world.via_clear(v,net):
            from itertools import islice
            collisions=[]
            if diagnostics is not None:
                shape=p.SHAPE_CIRCLE(vec(v),world.via_width//2)
                for face in (p.F_Cu,p.B_Cu):
                    collisions.extend(list(islice(world.collisions(shape,face,net,via=True),max(0,8-len(collisions)))))
            return reject('physical-via-clearance',position=v,via_width_nm=world.via_width,drill_nm=world.drill,collisions=collisions)
    if emit:
        for face,path in route['legs']:world.add_path(path,face,net)
        for v in route['vias']:world.add_via(v,net)
    return True


def _connect(world,a,z,la,lz,net,bounds,deadline,departure_heading=None,admit_continuation=None):
    if tuple(a)==tuple(z) and la==lz:return True
    mark=world.checkpoint();width=world.track_width
    def reject(reason):
        if hasattr(world,'connection_failures'):world.connection_failures[reason]+=1
        return False
    def physical(route,emit=True):
        return _physical_connection(world,route,a,z,net,bounds,emit=emit,admit_continuation=admit_continuation)
    world.path_policy=None
    for path in ranked_departures(a,z,departure_heading):
        if time.monotonic()>=deadline:return False
        if not _Style(bounds).accepts_path(path):continue
        route=bridge_carrier(path,la,p.B_Cu if la==p.F_Cu else p.F_Cu,start_layers={la},end_layers={lz},secondary_length_factor=2,via_cost_nm=400000,
            clear=lambda x,y,face:world.clear(p.SHAPE_SEGMENT(vec(x),vec(y),width),face,net),via_clear=lambda v:world.via_clear(v,net),
            via_pair_clear=lambda x,y:math.dist(x,y)>=world.via_width+world.minimum_clearance and world.via_pair_clear(x,y),deadline=deadline)
        if physical(route):return True
        world.restore(mark)
    # A genuine local XY/layer search also handles source/target attachments;
    # it runs against ghost copper and cannot touch the real board.
    from .grid_search import search as grid_search
    search=getattr(world,'attachment_search',None) or grid_search
    world.path_policy=TerminalStyle(bounds,{tuple(a):_terminal_rays(world,a,la,net),tuple(z):_terminal_rays(world,z,lz,net)});world.quality=QualityProfile('corridor-attachment',1500000);world.grid_step=125000;world.search_budget_s=max(.001,min(5.,deadline-time.monotonic()));world.heuristic_weight=1.5;world.via_cost_nm=1000000;world.layer_change_heuristic_nm=0
    old_count=len(world.operations)
    okay=False
    old_filter=getattr(world,'grid_plan_admission',None)
    world.grid_plan_admission=lambda legs,vias,n:physical(dict(legs=legs,vias=vias),emit=False)
    try:
        if time.monotonic()<deadline:okay=search(world,tuple(a),tuple(z),[la],[lz],net)
    finally:world.grid_plan_admission=old_filter
    world.path_policy=None
    if okay:
        route=_route_from_operations([dict(op) for op in world.operations[old_count:]],default_via_width=world.via_width)
        world.restore(mark)
        if physical(route):return True
    world.restore(mark);return reject('attachment-exhausted')


def _expanded(edges,nets,offsets,width):
    ops={n:[] for n in nets};run=[tuple(edges[0]['start'])];face=edges[0]['layer']
    def flush():
        path=clean(run)
        if len(path)<2:return
        for n in nets:
            lane=path if not offsets[n] else offset_path(path,offsets[n])
            ops[n].append(dict(kind='track',net=n,layer=face,path=lane,width=width))
    for edge in edges:
        if edge['kind']=='track':run.append(tuple(edge['end']))
        else:
            flush()
            for n in nets:
                for op in edge['payload']['operations'][n]:
                    ops[n].append(dict(kind='track',net=n,layer=op[1],path=op[2],width=width) if op[0]=='track' else dict(kind='via',net=n,position=op[1]))
            run=[tuple(edge['end'])];face=edge['end_layer']
    flush();return ops


def _owned_prefix_terminal(world,row,pad):
    """Revalidate a retained first-via outlet using its real ownership witnesses."""
    from .source_prefixes import _cover
    try:
        witness=row['source_prefix_witness'];via_witness=witness['via'];window=row['window'];net=row['net']
        owned=set(row['ids']);track_ids=set(witness['track_ids']);uid=via_witness['id']
        if (not track_ids or len(track_ids)!=len(witness['track_ids']) or not track_ids<=owned or uid not in owned or uid in track_ids):
            raise ValueError('Invalid owned source prefix inventory')
        items={q.m_Uuid.AsString():q for q in world.board.GetTracks()}
        via=items[uid];point=tuple(window['source']);face=window['source_layer']
        xy=lambda q:(q.x,q.y)
        expected=dict(id=uid,net=net,position=point,width=via.GetWidth(p.F_Cu),drill=via.GetDrillValue(),
            via_type=via.GetViaType(),top=via.TopLayer(),bottom=via.BottomLayer())
        if (via.GetClass()!='PCB_VIA' or via.IsLocked() or via.GetNetname()!=net
                or xy(via.GetStart())!=point or xy(via.GetEnd())!=point or not via.IsOnLayer(face)
                or expected['via_type']!=p.VIATYPE_THROUGH or {expected['top'],expected['bottom']}!={p.F_Cu,p.B_Cu}
                or not 0<expected['drill']<expected['width'] or via.GetWidth(p.B_Cu)!=expected['width']
                or any((tuple(via_witness[k]) if k=='position' else via_witness[k])!=v for k,v in expected.items())
                or point not in map(tuple,window['fixed_vias'])):
            raise ValueError('Stale owned source via witness')
        prefix=[(f,list(map(tuple,path))) for f,path in window['prefix_legs']]
        fixed=[(f,list(map(tuple,path))) for f,path in window['fixed_legs']]
        if (not prefix or any(len(path)<2 for _,path in prefix) or prefix[-1][1][-1]!=point
                or prefix[0][1][0]!=tuple(row['pad_position']) or xy(pad.GetPosition())!=tuple(row['pad_position'])
                or not pad.IsOnLayer(prefix[0][0]) or face==prefix[0][0]
                or any(f!=prefix[0][0] or (f,path) not in fixed for f,path in prefix)
                or any(a[-1]!=b[0] for (_,a),(_,b) in zip(prefix,prefix[1:]))):
            raise ValueError('Stale owned source prefix geometry')
        segments=[]
        for track_id in track_ids:
            track=items[track_id]
            if (track.GetClass()!='PCB_TRACK' or track.IsLocked() or track.GetNetname()!=net
                    or track.GetLayer()!=prefix[0][0] or track.GetWidth()!=row['width']):
                raise ValueError('Stale owned source track witness')
            segments.append((track_id,xy(track.GetStart()),xy(track.GetEnd())))
        edges=[(str(i),a,z) for i,(a,z) in enumerate((edge for _,path in prefix for edge in zip(path,path[1:])))]
        if (any(_cover(a,z,segments) is None for _,a,z in edges)
                or any(_cover(a,z,edges) is None for _,a,z in segments)):
            raise ValueError('Owned source prefix is not covered by actual tracks')
        return dict(source_forward_extent_nm=(expected['width']+1)//2,source_backward_extent_nm=(expected['width']+1)//2,
            source_terminal_kind='owned-prefix-via',source_terminal_id=uid,source_terminal_width_nm=expected['width'],
            source_terminal_drill_nm=expected['drill'])
    except (KeyError,TypeError,AttributeError,StopIteration) as error:
        raise ValueError('Missing or stale owned source prefix context') from error


def _source_land_metadata(world,row):
    """Measure the actual retained source land along both cardinal directions.

    The source may lie off the pad center after prefix ownership selection.
    Bounds are therefore projected from window.source, never guessed from a
    nominal package length. Only non-pad callers receive zero land extents.
    """
    if 'mcu_ref' not in row and 'mcu_pad' not in row:
        return dict(source_forward_extent_nm=0,source_backward_extent_nm=0)
    if 'mcu_ref' not in row or 'mcu_pad' not in row:raise ValueError('Missing physical source pad identity')
    footprint=next((q for q in world.board.GetFootprints() if q.GetReference()==row['mcu_ref']),None)
    pad=next((q for q in footprint.Pads() if q.GetNumber()==row['mcu_pad']),None) if footprint is not None else None
    if pad is None or pad.GetNetname()!=row['net']:raise ValueError('Missing physical source pad identity')
    margins=dict(source_track_margin_nm=max(world.minimum_clearance,world.net_clearances.get(row['net'],0),getattr(world.generation,'foreign_copper_clearance_nm',0)),
        source_via_copper_margin_nm=max(world.minimum_clearance,world.net_clearances.get(row['net'],0),world.generation.via_to_smd_clearance_nm),
        source_drill_land_margin_nm=p.FromMM(.1))
    if 'source_prefix_witness' in row:return dict(_owned_prefix_terminal(world,row,pad),**margins)
    face=row['window']['source_layer'];origin=row['window']['source']
    if not pad.IsOnLayer(face) or not p.SHAPE.Collide(p.SHAPE_CIRCLE(p.VECTOR2I(*origin),0),pad.GetEffectiveShape(face),0):
        raise ValueError('Actual source terminal does not contact its source pad')
    h=tuple(row['heading'])
    if h not in ((1,0),(-1,0),(0,1),(0,-1)):raise ValueError('Cardinal source pad heading required')
    box=pad.GetEffectiveShape(row['window']['source_layer']).BBox();origin=row['window']['source']
    projections=[sum((point[k]-origin[k])*h[k] for k in (0,1))
        for point in ((box.GetLeft(),box.GetTop()),(box.GetRight(),box.GetBottom()))]
    return dict(source_forward_extent_nm=max(0,max(projections)),source_backward_extent_nm=max(0,-min(projections)),
        source_pad_id=pad.m_Uuid.AsString(),source_pad_bbox_nm=[box.GetLeft(),box.GetTop(),box.GetRight(),box.GetBottom()],
        **margins)


def plan_obstacle_group(board,group,*,bounds,preferred_layer,protected_ids,deadline,deferred_zone_ids=(),candidate_gate=None,bend_cost_nm=1500000,candidate_score=None,candidate_axes=None,attachment_search=None,source_strategy='sequential'):
    if source_strategy not in ('sequential','joint','staged-joint'):raise ValueError('Unknown source strategy')
    started=time.monotonic();before=snapshot(board);ids={u for row in group for u in row['ids']};names=[q['net'] for q in group]
    if candidate_score is not None and not callable(candidate_score):raise ValueError('Candidate scorer must be callable')
    choice=CompleteChoice(deadline=deadline) if candidate_score is not None else None
    report=dict(planned=False,axis=[],reason='No complete physical ribbon',attempts=[],removed_ids=sorted(ids),nets=names,rejected_ribbons=[],source_strategy=source_strategy,accepted_source_strategy=None,joint_source_diagnostics=[])
    if len(group)<2 or len(set(names))!=len(names) or len(ids)!=sum(len(q['ids']) for q in group):return dict(report,reason='Expected disjoint distinct-net chains')
    if not ids<=before['tracks'].keys() or ids&set(protected_ids):return dict(report,reason='Protected/stale group')
    if source_strategy in ('joint','staged-joint'):
        headings={tuple(q['heading']) for q in group}
        if len(headings)!=1 or next(iter(headings)) not in ((1,0),(-1,0),(0,1),(0,-1)):
            return dict(report,reason='Joint source requires one cardinal physical heading')
    widths={q['width'] for q in group}
    if len(widths)!=1:return dict(report,reason='Group requires uniform widths')
    if source_strategy=='staged-joint' and any(row['window']['prefix_legs'] or 'source_prefix_witness' in row or 'mcu_ref' not in row or 'mcu_pad' not in row for row in group):
        return dict(report,reason='Staged joint requires original direct pad source windows')
    width=next(iter(widths));world=VirtualCopper(board,compact=True,ignored_copper=ids,track_width_nm=width,deadline=deadline,deferred_zone_ids=deferred_zone_ids);world.connection_failures=Counter();world.attachment_search=attachment_search
    # Keep all source exits and field copper on their actual old layers.
    for row in group:
        world.track_width=row['width']
        for face,path in row['window']['fixed_legs']:world.add_path(path,face,row['net'])
        for pos in row['window']['fixed_vias']:
            old=next((t for u,t in before['tracks'].items() if u in row['ids'] and t['class']=='PCB_VIA' and tuple(t['start'])==tuple(pos)),None)
            if old is None:return dict(report,reason='Missing fixed via witness')
            old_width,old_drill=world.via_width,world.drill;world.via_width=old['width'];world.drill=old['drill'];world.add_via(pos,row['net']);world.via_width,world.drill=old_width,old_drill
    for op in world.operations:op['fixed']=True
    gap=max(world.minimum_clearance,*[world.net_clearances.get(n,0) for n in names]);world.net_clearances['__ribbon_axis__']=gap;world.net_clearances['__cohort_capacity_probe__']=gap;fixed=world.checkpoint()
    head_via_ops={}
    if source_strategy=='staged-joint':
        from .staged_sources import admit_source_heads,staged_group
        lanes=[dict(net=row['net'],source=tuple(row['window']['source']),source_layer=row['window']['source_layer'],
            heading=tuple(row['heading']),width=row['width'],**_source_land_metadata(world,row)) for row in group]
        def check_head(world,route,lane):
            evidence={}
            okay=_physical_connection(world,route,lane['source'],route['legs'][-1][1][-1],lane['net'],bounds,diagnostics=evidence)
            return okay,evidence
        receipt=admit_source_heads(world,lanes,body_face=preferred_layer,via_width_nm=world.via_width,via_drill_nm=world.drill,
            clearance_nm=gap,deadline=deadline,checker=check_head)
        report['staged_source_heads']=receipt
        report['source_attachment_origin']='admitted-head-outlets'
        if not receipt['accepted']:
            world.restore(fixed)
            if snapshot(board)!=before:raise ValueError('Source head planning mutated source board')
            return dict(report,reason='No complete physical source head group',source_snapshot=before,source_readonly=True,
                planning_s=time.monotonic()-started,connection_failures=dict(world.connection_failures))
        group,head_via_ops=staged_group(group,receipt)
        # New head copper is frozen only inside this candidate checkpoint. It
        # remains newly added output, never an original retained-via witness.
        fixed=world.checkpoint()
    h=tuple(group[0]['heading']);normal=(-h[1],h[0]);order=sorted(range(len(group)),key=lambda i:sum(group[i].get('pad_position',group[i]['window']['source'])[k]*normal[k] for k in (0,1)))
    # Straight copper needs its track clearance, not the width of a via.
    # Every actual staggered portal still proves both faces and its drills.
    pitch=math.ceil((width+gap+25000)/1000)*1000
    offsets={names[i]:round((rank-(len(group)-1)/2)*pitch) for rank,i in enumerate(order)}
    radius=math.ceil(max(abs(q) for q in offsets.values())+width/2)
    # The circular swept envelope conservatively covers the miter of a45° turn.
    sweep=math.ceil(radius/math.cos(math.pi/8));step=250000;minimum_run=2*radius*(math.sqrt(2)-1)+width
    mean=lambda key:tuple(round(sum(q['window'][key][i] for q in group)/len(group)) for i in (0,1))
    a,z=mean('source'),mean('target')
    spread=lambda key:max(abs(sum((q['window'][key][i]-mean(key)[i])*normal[i] for i in (0,1))-offsets[q['net']]) for q in group)
    outward=0
    for row in group:
        if source_strategy=='staged-joint' or 'mcu_ref' not in row:continue
        fp=next(f for f in board.GetFootprints() if f.GetReference()==row['mcu_ref'])
        pad=next(d for d in fp.Pads() if d.GetNumber()==row['mcu_pad']);bb=pad.GetEffectiveShape(row['window']['source_layer']).BBox()
        outward=max(outward,*[sum((point[i]-a[i])*h[i] for i in (0,1)) for point in ((bb.GetLeft(),bb.GetTop()),(bb.GetRight(),bb.GetBottom()))])
    # Compact lanes need space for the last source via before the pack begins.
    lead_a=math.ceil(max(1000000,spread('source')+radius+gap,outward+world.via_width+gap+radius)/step)*step
    faces=(preferred_layer,p.B_Cu if preferred_layer==p.F_Cu else p.F_Cu)
    def edge_clear(a,z,face):return world.clear(p.SHAPE_SEGMENT(vec(a),vec(z),2*sweep),face,'__ribbon_axis__')
    portals=_Portals(world,names,offsets,step,deadline)
    sampling=dict(departure_deferred=0)
    def departure_free(point):
        if time.monotonic()>=deadline:raise TimeoutError('Port sampling deadline')
        start_faces=[face for face in faces if world.clear(p.SHAPE_CIRCLE(vec(point),sweep),face,'__ribbon_axis__')]
        if not start_faces:return False
        possible=departure_possible(point,h,start_faces,step=step,minimum_run=math.ceil(minimum_run),
            bounds=bounds,edge_clear=edge_clear,portal=lambda point,face:portals.propose(point,h,face))
        if possible is None:sampling['departure_deferred']+=1
        return possible is not False
    if candidate_axes is None:
        ports=port_pairs(a,[q['window']['target'] for q in group],h,bounds,step_nm=step,lead_nm=lead_a,minimum_run_nm=max(2000000,math.ceil(minimum_run)),
            free=lambda q:any(world.clear(p.SHAPE_CIRCLE(vec(q),sweep),face,'__ribbon_axis__') for face in faces),limit=16,departure_free=departure_free,sampling=sampling)
    else:
        candidate_axes=list(candidate_axes)
        if any(not axis for axis in candidate_axes):return dict(report,reason='Empty supplied axis')
        ports=[(axis[0]['start'],axis[-1]['end']) for axis in candidate_axes]
    report['port_sampling']=sampling
    failures=Counter();chosen=[None];joint_remaining=[64]
    def admit(edges):
        if not edges:return False
        last=edges[-1]
        if sum((last['end'][i]-last['start'][i])*h[i] for i in (0,1))<=0:
            failures['ribbon-arrival-heading']+=1;return False
        try:body=_expanded(edges,names,offsets,width)
        except ValueError:failures['offset-expansion']+=1;return False
        sample=None
        if len(report['rejected_ribbons'])<3:
            sample=dict(axis=edges,bodies=body,phase='lane-expansion');report['rejected_ribbons'].append(sample)
        for net in names:
            route=_route_from_operations(body[net],default_via_width=world.via_width)
            if route is None or not safe_route(route,width,world.via_width):failures['self-crossing']+=1;return False
        for sequence in (order,list(reversed(order))):
            world.restore(fixed);world.path_policy=None;world.track_width=width
            okay=True
            for net in names:
                for op in body[net]:
                    if not _add_checked(world,op):okay=False;failures['expanded-body-clearance']+=1;break
                if not okay:break
            if not okay:continue
            attachments={}
            # Reserve every source escape before any field tail can occupy it.
            if source_strategy=='joint':
                from .joint_source_transition import admit_joint_sources
                lanes=[]
                for row in group:
                    first=next(op for op in body[row['net']] if op['kind']=='track')
                    direction=tuple((b>a)-(b<a) for a,b in zip(first['path'][0],first['path'][1]))
                    lanes.append(dict(net=row['net'],source=tuple(row['window']['source']),
                        source_layer=row['window']['source_layer'],target=tuple(first['path'][0]),
                        target_layer=first['layer'],heading=tuple(row['heading']),width=row['width'],**_source_land_metadata(world,row)))
                    if direction!=tuple(row['heading']):okay=False
                def check_source(world,route,lane):
                    evidence={}
                    accepted=_physical_connection(world,route,lane['source'],lane['target'],lane['net'],bounds,diagnostics=evidence)
                    return accepted,evidence
                if not okay:
                    receipt=dict(accepted=False,reason='Joint body entry heading differs from source',attempts=[])
                elif joint_remaining[0]<=0:
                    receipt=dict(accepted=False,reason='joint-source-proposal-limit',attempts=[])
                else:
                    receipt=admit_joint_sources(world,lanes,via_width_nm=world.via_width,via_drill_nm=world.drill,
                        clearance_nm=gap,deadline=deadline,checker=check_source,limit=joint_remaining[0])
                joint_remaining[0]-=len(receipt['attempts'])
                if len(report['joint_source_diagnostics'])<64:
                    report['joint_source_diagnostics'].append(receipt)
                okay=receipt['accepted']
                if okay:attachments={net:(receipt['operations'][net],[]) for net in names}
                else:
                    failures['joint-source-attachment']+=1
                    if sample is not None:sample.update(phase='joint-source-attachment',reason=receipt.get('reason'))
            else:
                for i in sequence:
                    row=group[i];net=row['net'];w=row['window'];start=next(op['path'][0] for op in body[net] if op['kind']=='track')
                    world.track_width=width
                    mark=len(world.operations)
                    if not _connect(world,w['source'],start,w['source_layer'],edges[0]['layer'],net,bounds,deadline,departure_heading=row['heading']):
                        if sample is not None:sample.update(phase='source-attachment',net=net,terminals=[w['source'],start],layers=[w['source_layer'],edges[0]['layer']])
                        okay=False;failures['source-attachment']+=1;break
                    attachments[net]=([dict(op) for op in world.operations[mark:]],[])
            if not okay:
                # The joint proposal family is independent of tail order. A
                # failed family on this unchanged body must not be repeated.
                if source_strategy=='joint':break
                continue
            for i in sequence:
                row=group[i];net=row['net'];w=row['window'];last=next(op for op in reversed(body[net]) if op['kind']=='track')
                world.track_width=width
                mark=len(world.operations)
                def tail_admissible(proposed,net=net,w=w):
                    def leg_ops(legs):return [dict(kind='track',net=net,layer=f,path=path,width=width) for f,path in legs]
                    tail=leg_ops(proposed['legs'])+[dict(kind='via',net=net,position=v,width=world.via_width,drill=world.drill) for v in proposed['vias']]
                    complete=leg_ops(w['prefix_legs'])+attachments[net][0]+body[net]+tail+leg_ops(w['suffix_legs'])
                    complete += head_via_ops.get(net,[])
                    complete += [dict(op) for op in world.operations if op['net']==net and op['kind']=='via' and op.get('fixed')]
                    assembled=_route_from_operations(complete,default_via_width=world.via_width)
                    diameter=max([world.via_width]+[op.get('width',world.via_width) for op in complete if op['kind']=='via'])
                    return assembled is not None and safe_route(assembled,width,diameter)
                if not _connect(world,last['path'][-1],w['target'],last['layer'],w['target_layer'],net,bounds,deadline,admit_continuation=tail_admissible):
                    if sample is not None:sample.update(phase='target-attachment',net=net,terminals=[last['path'][-1],w['target']],layers=[last['layer'],w['target_layer']])
                    okay=False;failures['target-attachment']+=1;break
                attachments[net]=(attachments[net][0],[dict(op) for op in world.operations[mark:]])
            if not okay:continue
            operations=[dict(op) for op in world.operations]
            # Check the complete logical route, rather than trusting five
            # individually legal fragments to form a simple path together.
            for row in group:
                net=row['net'];w=row['window'];entry,tail=attachments[net]
                def fixed_ops(legs):return [dict(kind='track',net=net,layer=f,path=path,width=width) for f,path in legs]
                all_ops=fixed_ops(w['prefix_legs'])+entry+body[net]+tail+fixed_ops(w['suffix_legs'])
                all_ops += head_via_ops.get(net,[])
                all_ops += [op for op in operations if op['net']==net and op['kind']=='via' and op.get('fixed')]
                route=_route_from_operations(all_ops,default_via_width=world.via_width)
                if route is None or not safe_route(route,width,max([world.via_width]+[op['width'] for op in operations if op['kind']=='via' and op['net']==net])):
                    okay=False;failures['whole-route-self-contact']+=1;break
                if not _own_contacts_clear(world,route,net,width,ignored_ids=ids|set(world.added)):
                    okay=False;failures['whole-route-retained-contact']+=1;break
            if not okay:continue
            projected=project_operations(before['tracks'],ids,operations)
            eligible=candidate_gate is None or bool(candidate_gate(before['tracks'],projected))
            if not eligible:failures['complete-quality']+=1
            if choice is None:
                if not eligible:continue
                if sample is not None:sample.update(phase='admitted',admitted=True)
                chosen[0]=operations;return True
            # Both attachment orders and subsequent goal/port proposals are
            # evaluated against this same original source. Never emit to BOARD.
            choice.consider(operations,edges,score=candidate_score(projected),eligible=eligible)
            if sample is not None:sample.update(phase='complete-scored',admitted=eligible)
        world.restore(fixed);return False
    last_search=None
    try:
        for index,(start,goal) in enumerate(ports):
            world.restore(fixed);world.track_width=width;world.net_clearances['__ribbon_axis__']=gap
            if time.monotonic()>=deadline:break
            # Capacity-probe endpoints are free shared space, not source-pad centers.
            start_faces=[face for face in (preferred_layer,p.B_Cu if preferred_layer==p.F_Cu else p.F_Cu) if world.clear(p.SHAPE_CIRCLE(vec(start),sweep),face,'__ribbon_axis__')]
            end_faces=[face for face in (p.F_Cu,p.B_Cu) if world.clear(p.SHAPE_CIRCLE(vec(goal),sweep),face,'__ribbon_axis__')]
            if candidate_axes is not None and (not start_faces or not end_faces):
                # A newly preserved source via can occupy a cached axis port.
                # Shorten only at existing vertices; re-prove the entire body
                # and all attachments below. No new axis search is introduced.
                from .axis_replay import trim_track_axis_endpoints
                original_axis=candidate_axes[index]
                trimmed=trim_track_axis_endpoints(original_axis,
                    free=lambda point,face:world.clear(p.SHAPE_CIRCLE(vec(point),sweep),face,'__ribbon_axis__'),
                    minimum_run_nm=max(2000000,math.ceil(minimum_run)))
                if trimmed is not None:
                    candidate_axes[index]=trimmed
                    start,goal=trimmed[0]['start'],trimmed[-1]['end']
                    ports[index]=(start,goal)
                    start_faces=[trimmed[0]['layer']];end_faces=[trimmed[-1]['end_layer']]
                    report.setdefault('axis_endpoint_trims',[]).append(dict(original_edges=len(original_axis),
                        retained_edges=len(trimmed),start=start,goal=goal))
            if not start_faces or not end_faces:failures['ribbon-port-occupied']+=1;continue
            stop=min(deadline,time.monotonic()+max(.5,(deadline-time.monotonic())/max(1,len(ports)-index)))
            if candidate_axes is None:
                last_search=search_corridor(start,goal,start_heading=h,layers=(p.F_Cu,p.B_Cu),preferred_layer=preferred_layer,bounds_nm=bounds,step_nm=step,minimum_run_nm=minimum_run,
                    edge_clear=edge_clear,portal=portals.propose,deadline=stop,admission_deadline=deadline,start_layers=start_faces,goal_layers=end_faces,goal_accept=admit,max_expansions=80000,bend_cost_nm=bend_cost_nm,secondary_factor=2)
            else:
                from .axis_replay import replay_track_axis
                last_search=replay_track_axis(candidate_axes[index],bounds=bounds,layers=(p.F_Cu,p.B_Cu),edge_clear=edge_clear,goal_accept=admit)
            report['attempts'].append({k:v for k,v in last_search.items() if k!='edges'})
            if last_search['accepted'] and chosen[0] is not None:
                report.update(planned=True,operations=chosen[0],axis=last_search['edges'],search=last_search,reason=None);break
    except TimeoutError:
        if choice is None:raise
        failures['collection-timeout']+=1
    if choice is not None:
        selected=choice.best
        report['selection']=choice.receipt()
        if selected is not None:
            report.update(planned=True,operations=selected['operations'],axis=selected['axis'],
                          selected_score=selected['score'],search=last_search,reason=None)
    if report['planned']:report['accepted_source_strategy']=source_strategy
    world.restore(fixed)
    if snapshot(board)!=before:raise ValueError('Virtual corridor planning mutated source board')
    report.update(deferred_zone_ids=list(deferred_zone_ids),failures=dict(failures),connection_failures=dict(world.connection_failures),pitch_nm=pitch,pitch_policy='track-clearance with physical portal proof',port_pairs=ports,reserved_width_nm=2*radius,planning_s=time.monotonic()-started,source_snapshot=before,source_readonly=True)
    if not report['planned']:report['search']=last_search
    return report


def project_operations(before,removed,operations):
    tracks={u:t for u,t in before.items() if u not in removed}
    for i,op in enumerate(operations):
        if op['kind']=='track':tracks['virtual-'+str(i)]=dict(**{'class':'PCB_TRACK'},net=op['net'],layer=op['layer'],width=op['width'],start=op['start'],end=op['end'],locked=False)
        else:tracks['virtual-'+str(i)]=dict(**{'class':'PCB_VIA'},net=op['net'],layer=p.F_Cu,width=op['width'],drill=op['drill'],start=op['position'],end=op['position'],top=p.F_Cu,bottom=p.B_Cu,via_type=p.VIATYPE_THROUGH,locked=False)
    return tracks


def commit_obstacle_group(board,plan,*,protected_ids,deadline,quality_gate=None):
    before=snapshot(board);ids=set(plan['removed_ids']);original=plan['source_snapshot']
    if before!=original or not ids<=before['tracks'].keys() or ids&set(protected_ids):return dict(accepted=False,reason='Source changed or workset protected')
    items={t.m_Uuid.AsString():t for t in board.GetTracks()};copies={u:_copy(items[u]) for u in ids};parts=_parts(board,deadline)
    def restore():
        for t in list(board.GetTracks()):
            if t.m_Uuid.AsString() not in before['tracks']:board.Delete(t)
        live={t.m_Uuid.AsString() for t in board.GetTracks()}
        for u,t in copies.items():
            if u not in live:board.Add(_copy(t))
        _refill(board)
        if snapshot(board)!=before:raise ValueError('Obstacle group rollback lost source')
    try:
        for u in ids:board.Delete(items[u])
        world=LocalRouter(board,compact=True,track_width_nm=min(op['width'] for op in plan['operations'] if op['kind']=='track'),deadline=deadline,deferred_zone_ids=plan.get('deferred_zone_ids',()))
        for op in plan['operations']:
            if time.monotonic()>=deadline:raise TimeoutError('Atomic corridor commit expired')
            net=op['net'];world.track_width=op.get('width',world.track_width)
            if op['kind']=='track':
                path=[op['start'],op['end']]
                if not op.get('fixed') and not world.clear(p.SHAPE_SEGMENT(vec(path[0]),vec(path[1]),world.track_width),op['layer'],net):raise ValueError('Committed track disagrees with virtual clearance')
                world.add_path(path,op['layer'],net)
            else:
                old_width,old_drill=world.via_width,world.drill;world.via_width=op['width'];world.drill=op['drill']
                if not op.get('fixed') and not world.via_clear(op['position'],net):raise ValueError('Committed via disagrees with virtual clearance')
                world.add_via(op['position'],net);world.via_width,world.drill=old_width,old_drill
        _refill(board);after=snapshot(board)
        if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=t for u,t in before['tracks'].items() if u not in ids):raise ValueError('Commit changed retained geometry')
        if _parts(board,deadline)!=parts:raise ValueError('Commit changed numbered-pad connectivity')
        if quality_gate is not None and not quality_gate(before['tracks'],after['tracks']):raise ValueError('Commit rejected by whole-area quality')
        return dict(accepted=True,removed_ids=sorted(ids),new_ids=sorted(set(after['tracks'])-set(before['tracks'])))
    except (ValueError,TimeoutError) as error:
        restore();return dict(accepted=False,reason=str(error))
    except BaseException:
        restore();raise
