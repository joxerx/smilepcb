"""Reference-inspired terminal banks and shared, offset corridor proposals."""
from collections import defaultdict
import math
import time

from .bundle_geometry import bundle_score, _parallel_intervals, paths_clear
from .follow_geometry import offset_path, follow_spans, simplify
from .polish_geometry import octile, valid_shape


def terminal_groups(chains, center, *, radius_nm=12000000, limit=32):
    banks=defaultdict(list)
    for row in chains:
        ends=[tuple(row['path'][0]),tuple(row['path'][-1])]
        if math.dist(ends[0],center)>math.dist(ends[1],center):ends.reverse()
        a,z=ends
        if math.dist(a,center)>radius_nm or math.dist(a,z)<2000000:continue
        dx,dy=a[0]-center[0],a[1]-center[1]
        bank=(0,1 if dx>=0 else -1) if abs(dx)>=abs(dy) else (1,1 if dy>=0 else -1)
        q=dict(row,source=a,target=z)
        banks[bank].append(q)
    out=[];seen=set()
    for bank,rows in sorted(banks.items()):
        # Long chains represent the matrix feeder; tiny old escape fragments
        # must not replace that feeder merely because their endpoint is closer.
        unique={}
        for q in rows:
            score=math.dist(q['source'],q['target'])
            if q['net'] not in unique or score>unique[q['net']][0]:unique[q['net']]=(score,q)
        rows=[q for _,q in unique.values()]
        for seed in sorted(rows,key=lambda q:(q['source'],q['net'])):
            a,z=seed['source'],seed['target'];v=(z[0]-a[0],z[1]-a[1]);norm=math.hypot(*v)
            candidates=[]
            for q in rows:
                u=(q['target'][0]-q['source'][0],q['target'][1]-q['source'][1])
                if math.dist(a,q['source'])<=radius_nm/2 and sum(v[i]*u[i] for i in (0,1))>=.4*norm*math.hypot(*u):candidates.append(q)
            candidates.sort(key=lambda q:(math.dist(q['source'],a),q['source'],q['net']))
            candidates=candidates[:6]
            if len(candidates)<2:continue
            key=tuple(sorted(q['net'] for q in candidates))
            if key not in seen:seen.add(key);out.append(candidates)
            if len(out)>=limit:return out
    return out


def blocker_closure(blocked_ids, chains, *, protected_ids=()):
    protected=set(protected_ids);result=set()
    for row in chains:
        ids=set(row['ids'])
        if ids.intersection(blocked_ids) and not ids.intersection(protected):result.update(ids)
    return result


def restoration_paths(row, *, deadline, preserve_v45_nets=()):
    """Bounded, deadline-aware simple detours for a displaced whole chain."""
    from .bundle_geometry import preserves_clean_columns, stemmed_chain
    original=list(map(tuple,row['path']));seen=set()
    def allowed(path):
        key=tuple(path)
        if key in seen or time.monotonic()>=deadline:return False
        seen.add(key)
        return valid_shape(path) and preserves_clean_columns(row,path,set(preserve_v45_nets)) and paths_clear([path],[row['width']],0)
    if time.monotonic()>=deadline:return
    if len(original)<=512 and allowed(original):yield original
    bases=octile(original[0],original[-1])
    for base in bases:
        if time.monotonic()>=deadline:return
        if allowed(base):yield base
        for radius in (500000,1000000,2000000,4000000):
            for sign in (-1,1):
                if time.monotonic()>=deadline:return
                lane=offset_path(base,sign*radius)
                if lane is None:continue
                for trim in (radius//2,radius):
                    middle=stemmed_chain(lane,trim,trim)[1]
                    for left in octile(original[0],middle[0]):
                        for right in octile(middle[-1],original[-1]):
                            if time.monotonic()>=deadline:return
                            path=simplify([*left,*middle[1:],*right[1:]])
                            if allowed(path):yield path



def restoration_cost_delta(saved,before_tracks,*,preferred_layer):
    from .polish_geometry import shape_score
    row=saved['row'];route=saved['route']
    length=lambda path:sum(math.dist(a,z) for a,z in zip(path,path[1:]))
    old_off=sum(length(path) for face,path in row['old_legs'] if face!=preferred_layer)
    new_off=sum(length(path) for face,path in route['legs'] if face!=preferred_layer)
    return (shape_score(saved['path'],3000000)-shape_score(row['path'],3000000)
            +4*(new_off-old_off)
            +400000*(len(route['vias'])-sum(before_tracks[u]['class']=='PCB_VIA' for u in row['ids'])))


def corridor_candidates(group, *, clearance_nm, limit=48, deadline=None):
    """Finite complete-group alternatives from geometry, never net indices.

    Lanes have one physical pitch through shared mitered elbows. Independent
    source/destination attachments may cross in XY; actual two-layer admission
    must prove every crossing and via before accepting a proposal.
    """
    if not 2<=len(group)<=6 or len({q['net'] for q in group})!=len(group):raise ValueError('Two to six distinct nets required')
    expired=lambda:deadline is not None and time.monotonic()>=deadline
    original=[list(map(tuple,q['path'])) for q in group]
    # Orient all paths along the same mean direction, independent of the
    # endpoint enumeration chosen by the old copper extractor.
    reference=max(original,key=lambda q:math.dist(q[0],q[-1]));direction=(reference[-1][0]-reference[0][0],reference[-1][1]-reference[0][1])
    reverse=[];oriented=[]
    for q in original:
        rev=sum((q[-1][i]-q[0][i])*direction[i] for i in (0,1))<0
        reverse.append(rev);oriented.append(list(reversed(q)) if rev else q)
    start=tuple(round(sum(q[0][i] for q in oriented)/len(group)) for i in (0,1))
    end=tuple(round(sum(q[-1][i] for q in oriented)/len(group)) for i in (0,1))
    dx,dy=end[0]-start[0],end[1]-start[1];norm=math.hypot(dx,dy)
    if not norm:return []
    normal=lambda a:(-dy*a[0]+dx*a[1])/norm
    # Follow physical source order. Destination permutations are handled in
    # the short attachments by native layer bridges, never by joining nets.
    order=sorted(range(len(group)),key=lambda i:(normal(oriented[i][0]),normal(oriented[i][-1]),i))
    widths=[q['width'] for q in group];offsets={order[0]:0}
    for i,j in zip(order,order[1:]):offsets[j]=offsets[i]+math.ceil((widths[i]+widths[j])/2+clearance_nm+25000)
    middle=sum(offsets.values())/len(offsets);offsets={i:round(v-middle) for i,v in offsets.items()}
    step=max(widths)+clearance_nm+25000
    spines=[]
    for path in [*octile(start,end),*oriented]:
        if valid_shape(path):spines.append(path)
    results={}
    # Translate the common corridor together instead of merely moving a via
    # on an unchanged obstructed carrier. Radius scales with actual bus width.
    shifts=[0,step,-step,2*step,-2*step,4*step,-4*step,8*step,-8*step]
    for spine in spines:
        for shift in shifts:
            if expired():break
            paths=[];okay=True
            for i,row in enumerate(group):
                delta=offsets[i]+shift
                lane=spine if not delta else offset_path(spine,delta)
                if lane is None:okay=False;break
                a,z=oriented[i][0],oriented[i][-1];choices=[]
                for span in follow_spans(lane,a,z):
                    for left in octile(a,span[0]):
                        for right in octile(span[-1],z):
                            q=simplify([*left,*span[1:],*right[1:]])
                            if valid_shape(q) and paths_clear([q],[widths[i]],0):choices.append(q)
                if not choices:okay=False;break
                best=min(choices,key=lambda q:(len(q),sum(math.dist(a,b) for a,b in zip(q,q[1:])),q))
                paths.append(list(reversed(best)) if reverse[i] else best)
            if not okay:continue
            key=tuple(tuple(q) for q in paths)
            shared=sum(run for i,a in enumerate(paths) for b in paths[:i] for run,gap in _parallel_intervals(a,b) if gap<=1000000+(max(widths)))
            results[key]=dict(paths=paths,score=bundle_score(paths,widths,clearance_nm=clearance_nm,bend_price_nm=3000000,group_strength=3,nets=[q['net'] for q in group]),parallel_mm=shared/1e6,shift_nm=shift)
    return sorted(results.values(),key=lambda q:(q['score'],-q['parallel_mm'],q['paths']))[:limit]
