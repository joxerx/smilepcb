"""Whole-area quality guard: simplify without silently dismantling a bus."""
from collections import defaultdict
import math
from .visual_geometry import UniformNeighbors


def _runs(tracks,nets):
    groups=defaultdict(list)
    for t in tracks.values():
        if t['class']!='PCB_TRACK' or t['net'] not in nets:continue
        a,z=map(tuple,(t['start'],t['end']));dx,dy=z[0]-a[0],z[1]-a[1];g=math.gcd(dx,dy)
        if not g:continue
        d=(dx//g,dy//g)
        if max(map(abs,d))!=1:continue
        if d[0]<0 or not d[0] and d[1]<0:d=(-d[0],-d[1])
        c=-d[1]*a[0]+d[0]*a[1];lo,hi=sorted(d[0]*q[0]+d[1]*q[1] for q in (a,z));groups[t['net'],t['layer'],t['width'],d,c].append((lo,hi))
    out=[]
    for (net,layer,width,d,c),spans in groups.items():
        merged=[]
        for a,z in sorted(spans):
            if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(z,merged[-1][1]))
            else:merged.append((a,z))
        norm=d[0]**2+d[1]**2;point=lambda u:((d[0]*u-d[1]*c)//norm,(d[1]*u+d[0]*c)//norm)
        out.extend(dict(net=net,layer=layer,width=width,start=point(a),end=point(z)) for a,z in merged)
    return out


def _clip(a,z,box):
    lo,hi=0.,1.;dx,dy=z[0]-a[0],z[1]-a[1]
    for v,q in [(-dx,a[0]-box[0]),(dx,box[2]-a[0]),(-dy,a[1]-box[1]),(dy,box[3]-a[1])]:
        if not v:
            if q<0:return None
        elif v<0:lo=max(lo,q/v)
        else:hi=min(hi,q/v)
    if hi<=lo:return None
    return tuple(round(a[i]+lo*(z[i]-a[i])) for i in (0,1)),tuple(round(a[i]+hi*(z[i]-a[i])) for i in (0,1))


def role_coverage(tracks,role_nets,bounds):
    """V2 diagnostic: >=2mm foreign overlap, nonnegative gaps, preferred0.198mm."""
    out={}
    for label,nets in role_nets.items():
        nets=set(nets);rows=_runs(tracks,nets);index=UniformNeighbors(rows,eligible_nets=nets,min_edge_gap=0,max_edge_gap=1000000,min_run_nm=2000000,preferred_edge_gap_nm=198000);total=0.
        for t in rows:
            pair=_clip(t['start'],t['end'],bounds)
            if pair:total+=index.coverage(*pair,t['layer'],t['net'],t['width'])
        out[label]=total
    return out


def projected_tracks(before,removed,legs):
    """A geometry-only candidate for the guard, before native emission."""
    result={u:t for u,t in before.items() if u not in removed};serial=0
    for layer,net,width,path in legs:
        for a,z in zip(path,path[1:]):
            result['candidate-segment-'+str(serial)]=dict(**{'class':'PCB_TRACK'},net=net,layer=layer,width=width,start=a,end=z,locked=False);serial+=1
    return result


def quality_gate(role_nets,bounds):
    """Reject per-role coverage losses >1 nanometre in the fixed MCU crop."""
    cached=[None,None];stats={'evaluated':0,'vetoed':0}
    def accept(before,after):
        if cached[0] is not before:cached[:]=[before,role_coverage(before,role_nets,bounds)]
        candidate=role_coverage(after,role_nets,bounds);stats['evaluated']+=1
        okay=all(candidate[k]+1>=value for k,value in cached[1].items())
        if not okay:stats['vetoed']+=1
        return okay
    accept.stats=stats
    return accept


def whole_board_coverage(tracks,role_nets,bounds,*,preferred_edge_gap_nm=200000,min_run_nm=2000000):
    """Report every family against all real foreign copper on the same face.

    Widths contribute through edge clearance, rather than a fixed centre pitch.
    Collinear splits/duplicates are canonicalized; a net cannot reward itself.
    This diagnostic is independent of Native electrical/physical admission.
    """
    names={t['net'] for t in tracks.values()}
    rows=_runs(tracks,names)
    index=UniformNeighbors(rows,eligible_nets=names,min_edge_gap=0,max_edge_gap=1000000,
        min_run_nm=min_run_nm,preferred_edge_gap_nm=preferred_edge_gap_nm)
    out={}
    for label,nets in role_nets.items():
        selected=set(nets);total=0.
        for row in rows:
            if row['net'] not in selected:continue
            pair=_clip(row['start'],row['end'],bounds)
            if pair:total+=index.coverage(*pair,row['layer'],row['net'],row['width'])
        out[label]=total
    return out


def cohesion_summary(tracks,role_nets,bounds,*,preferred_edge_gap_nm=200000,min_run_nm=2000000):
    """Whole-board diagnostic, normalized so extra copper is not its own reward."""
    names={t['net'] for t in tracks.values()}
    reserved='__all_copper__'
    if reserved in role_nets:raise ValueError('Reserved cohesion role name')
    coverage=whole_board_coverage(tracks,{**role_nets,reserved:names},bounds,
        preferred_edge_gap_nm=preferred_edge_gap_nm,min_run_nm=min_run_nm)
    length=0.
    for row in _runs(tracks,names):
        pair=_clip(row['start'],row['end'],bounds)
        if pair:length+=math.dist(*pair)
    covered=coverage.pop(reserved)
    return dict(scope='whole-board foreign-net adjacency',per_role_nm=coverage,
        canonical_length_nm=length,weighted_coverage_nm=covered,
        weighted_fraction=covered/length if length else 0.,
        preferred_edge_gap_nm=preferred_edge_gap_nm,min_continuous_run_nm=min_run_nm)
