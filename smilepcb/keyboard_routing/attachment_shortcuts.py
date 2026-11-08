"""One optional bounded shortcut pass over new attachment tracks only.

This generates a proposal in a virtual copper union. It never searches routes,
changes vias, mutates BOARD, or establishes saved ERC/DRC qualification.
"""
from collections import defaultdict
from copy import deepcopy
import math
import time

from .corridor_geometry import offset_path
from .follow_geometry import simplify
from .polish_geometry import chains,shortcut_candidates,valid_shape
from .bundle_geometry import paths_clear
from .layered_simple_route import safe_layered_route


def _length(path):return sum(math.dist(a,z) for a,z in zip(path,path[1:]))


def prepare(plan):
    """Derive editable spans only after uniquely matching the exact shared body."""
    ops=plan['operations'];names=plan['nets'];axis=plan['axis']
    if not axis or len(names)<2 or len(names)!=len(set(names)):raise ValueError('Expected complete shared-axis plan')
    face=axis[0]['layer'];path=simplify([axis[0]['start']]+[edge['end'] for edge in axis])
    if (any(edge['kind']!='track' or edge['layer']!=face or edge['end_layer']!=face for edge in axis)
            or any(tuple(a['end'])!=tuple(b['start']) for a,b in zip(axis,axis[1:])) or not valid_shape(path)):
        raise ValueError('Shortcut scope requires a contiguous track-only axis')
    widths={op['width'] for op in ops if op['kind']=='track'}
    if len(widths)!=1 or any(op['net'] not in names for op in ops):raise ValueError('Expected one complete uniform-width group')
    body=set();ranks=set()
    for net in names:
        matches=[]
        for rank in range(len(names)):
            delta=round((rank-(len(names)-1)/2)*plan['pitch_nm'])
            lane=path if not delta else offset_path(path,delta);indices=[]
            for a,z in zip(lane,lane[1:]):
                found=[i for i,op in enumerate(ops) if not op.get('fixed') and op['kind']=='track'
                       and op['net']==net and op['layer']==face and tuple(op['start'])==tuple(a) and tuple(op['end'])==tuple(z)]
                if len(found)!=1:break
                indices.extend(found)
            else:matches.append((rank,indices))
        if len(matches)!=1 or matches[0][0] in ranks:raise ValueError('Missing or ambiguous exact shared-body geometry')
        rank,indices=matches[0];ranks.add(rank);body.update(indices)
    frozen={i for i,op in enumerate(ops) if op.get('fixed') or op['kind']=='via'}|body
    anchors={(op['net'],op['layer'],tuple(q)) for i,op in enumerate(ops) if i in frozen and op['kind']=='track'
             for q in (op['start'],op['end'])}
    anchors.update((op['net'],face,tuple(op['position'])) for op in ops if op['kind']=='via' for face in (0,2))
    rows={str(i):dict(net=op['net'],layer=op['layer'],width=op['width'],start=op['start'],end=op['end'],locked=False,
                     **{'class':'PCB_TRACK'}) for i,op in enumerate(ops) if i not in frozen}
    spans=[]
    for row in chains(rows,anchors):
        row=dict(row,ids=sorted(map(int,row['ids'])))
        row['candidates']=[q for q in shortcut_candidates(row['path']) if len(q)<len(row['path'])
            and _length(q)<=_length(row['path'])+1e-6 and paths_clear([q],[row['width']],0)] if len(row['path'])<=32 else []
        row['candidates'].sort(key=lambda q:(len(q),_length(q),q))
        spans.append(row)
    return dict(frozen_indices=sorted(frozen),body_indices=sorted(body),spans=spans)


def replace(operations,replacements,omit=()):
    """Replace only disjoint original span indices, retaining all other records."""
    skip=set(omit);starts={}
    for ids,(span,path) in replacements.items():
        if not ids or skip&set(ids) or tuple(path[0])!=tuple(span['path'][0]) or tuple(path[-1])!=tuple(span['path'][-1]):
            raise ValueError('Shortcut replacement changed scope or endpoints')
        skip.update(ids)
        starts[min(ids)]=[dict(kind='track',net=span['net'],layer=span['layer'],width=span['width'],start=tuple(a),end=tuple(z))
                          for a,z in zip(path,path[1:])]
    return [op for i,old in enumerate(operations) for op in (starts.get(i,[]) if i in skip else [old])]


def ordered_route(operations,net):
    """Recover an unbranched 3D route from an unordered complete emission log."""
    edges=[];adjacency=defaultdict(list)
    for op in operations:
        if op['net']!=net:continue
        if op['kind']=='track':a,b=(op['layer'],tuple(op['start'])),(op['layer'],tuple(op['end']))
        else:a,b=(0,tuple(op['position'])),(2,tuple(op['position']))
        if a==b:return None
        index=len(edges);edges.append((a,b,op));adjacency[a].append(index);adjacency[b].append(index)
    ends=sorted(node for node,indices in adjacency.items() if len(indices)==1)
    if len(ends)!=2 or any(len(indices)>2 for indices in adjacency.values()):return None
    node=ends[0];used=set();legs=[];vias=[];widths=[]
    while True:
        options=[index for index in adjacency[node] if index not in used]
        if not options:break
        if len(options)!=1:return None
        index=options[0];used.add(index);a,b,op=edges[index];other=b if node==a else a
        if op['kind']=='via':vias.append(node[1]);widths.append(op['width'])
        elif legs and legs[-1][0]==node[0]:legs[-1][1].append(other[1])
        else:legs.append((node[0],[node[1],other[1]]))
        node=other
    if len(used)!=len(edges) or len(vias)!=len(legs)-1:return None
    return dict(legs=[(face,simplify(path)) for face,path in legs],vias=vias,via_widths=widths)


def propose(board,plan,*,protected_ids,deadline,max_candidates=64,max_per_span=8):
    """Try one monotone pass, at most 64 Native candidates and five seconds.

    Returned plans still require the caller's one common finalized commit and
    fresh saved checks. An expired final proof returns the incumbent unchanged.
    """
    from smilepcb.native_worker import p,snapshot
    from .virtual_copper import VirtualCopper
    from .local_router import vec
    from .obstacle_bundles import _join_clear,_own_contacts_clear
    from .bundle_polish import retained_contacts_clear
    from .via_junction_polish import _pad_contacts_clear
    started=time.monotonic();stop=min(deadline,started+5)
    if not 1<=max_candidates<=64 or not 1<=max_per_span<=8:raise ValueError('Finite shortcut limits required')
    before=snapshot(board);removed=set(plan['removed_ids']);protected=set(protected_ids)
    report=dict(accepted=False,checked=0,rewrites=[],plan=plan,source_exact=True)
    def check():
        if time.monotonic()>=stop:raise TimeoutError('Attachment shortcut proposal deadline')
    def world_for(ops):
        check();world=VirtualCopper(board,compact=True,ignored_copper=removed,track_width_nm=width,
                                    deadline=stop,deferred_zone_ids=plan.get('deferred_zone_ids',()))
        for op in ops:
            check();world.track_width=op['width'] if op['kind']=='track' else width
            if op['kind']=='track':
                if not op.get('fixed') and not world.clear(p.SHAPE_SEGMENT(vec(op['start']),vec(op['end']),op['width']),op['layer'],op['net']):
                    raise ValueError('Shortcut union track clearance failed')
                world.add_path([op['start'],op['end']],op['layer'],op['net'])
            else:
                old_width,old_drill=world.via_width,world.drill;world.via_width,world.drill=op['width'],op['drill']
                try:
                    if not op.get('fixed') and not world.via_clear(op['position'],op['net']):raise ValueError('Shortcut union via clearance failed')
                    world.add_via(op['position'],op['net'])
                finally:world.via_width,world.drill=old_width,old_drill
        return world
    try:
        check()
        if before!=plan.get('source_snapshot') or not removed<=before['tracks'].keys() or removed&protected:
            raise ValueError('Shortcut source or workset changed')
        scope=prepare(plan);width=plan['operations'][next(i for i,op in enumerate(plan['operations']) if op['kind']=='track')]['width']
        useful=[row for row in scope['spans'] if row['candidates']]
        useful.sort(key=lambda row:(len(row['candidates'][0])-len(row['path']),row['net'],row['path']))
        replacements={}
        for span in useful[:12]:
            if report['checked']>=max_candidates or time.monotonic()>=stop-.8:break
            ids=tuple(span['ids']);base=replace(plan['operations'],replacements,omit=ids);world=world_for(base)
            for path in span['candidates'][:max_per_span]:
                if report['checked']>=max_candidates or time.monotonic()>=stop-.8:break
                check();report['checked']+=1
                proposed=dict(replacements);proposed[ids]=(span,path)
                ops=replace(plan['operations'],proposed)
                routes={net:ordered_route(ops,net) for net in plan['nets']}
                if any(route is None or not safe_layered_route(route,width,world.via_width) for route in routes.values()):continue
                face,net=span['layer'],span['net']
                if not all(world.clear(p.SHAPE_SEGMENT(vec(a),vec(z),width),face,net) for a,z in zip(path,path[1:])):continue
                if not _join_clear(world,path,face,net) or not _pad_contacts_clear(board,path,face,net,width,world):continue
                if not retained_contacts_clear(board,path,face,net,width,ignored_ids=removed,oracle=world,track_items=world.track_items()):continue
                replacements=proposed
                report['rewrites'].append(dict(net=net,layer=face,old=span['path'],new=path,
                                               removed_operation_indices=list(ids)))
                break
        if not replacements:
            report['reason']='No admitted attachment shortcut';return report
        ops=replace(plan['operations'],replacements);world=world_for(ops)
        for net in plan['nets']:
            check();route=ordered_route(ops,net)
            if route is None or not safe_layered_route(route,width,world.via_width):raise ValueError('Complete shortcut route is not simple')
            ignored=removed|set(world.added)
            for face,path in route['legs']:
                check()
                if (not _join_clear(world,path,face,net,ignored_ids=ignored)
                        or not _pad_contacts_clear(board,path,face,net,width,world)
                        or not retained_contacts_clear(board,path,face,net,width,ignored_ids=ignored,
                                                       oracle=world,track_items=world.track_items())):
                    raise ValueError('Complete shortcut retained-contact proof failed')
            if not _own_contacts_clear(world,route,net,width,ignored_ids=ignored):raise ValueError('Complete shortcut source contacts changed')
        check()
        if any(plan['operations'][i] not in ops for i in scope['frozen_indices']):raise ValueError('Shortcut changed frozen geometry')
        result=deepcopy(dict(plan));result['operations']=ops
        report.update(accepted=True,plan=result,operation_reduction=len(plan['operations'])-len(ops))
    except (ValueError,TimeoutError) as error:
        report.update(accepted=False,plan=plan,reason=str(error))
    finally:
        if snapshot(board)!=before:raise ValueError('Attachment shortcut proposal mutated original board')
        report['elapsed_s']=time.monotonic()-started
    return report
