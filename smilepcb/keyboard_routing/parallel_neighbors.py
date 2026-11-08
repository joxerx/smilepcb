"""Per-length affinity to foreign parallel matrix nets, never to the query net.

Geometry is a cost input only. Native DRC remains the admission authority.
Overlapping neighbours contribute union coverage, so every query millimetre
counts at most once. Surcharging uncovered length keeps all graph costs
nonnegative and preserves Euclidean lower bounds without negative cycles.
"""
import math
from collections import defaultdict

def family(net):
    if net.startswith('COL'):return 'COL'
    if net.startswith('ROW'):return 'ROW'
    return None

def frame(a,z):
    dx,dy=z[0]-a[0],z[1]-a[1]
    if dx==dy==0:return None
    if dy==0:d=(1,0)
    elif dx==0:d=(0,1)
    elif abs(dx)==abs(dy):d=(1,1 if dx*dy>0 else -1)
    else:return None
    scale=math.hypot(*d)
    u=lambda q:(q[0]*d[0]+q[1]*d[1])/scale
    c=(a[1]*d[0]-a[0]*d[1])/scale
    return d,c,min(u(a),u(z)),max(u(a),u(z))

class ParallelNeighbors:
    def __init__(self,segments,*,min_edge_gap=173000,max_edge_gap=1000000,min_run_nm=0,mixed_matrix=False,eligible_nets=None):
        if not 0<=min_edge_gap<max_edge_gap:raise ValueError('Invalid neighbour spacing band')
        if type(min_run_nm) is not int or not 0<=min_run_nm<=10000000:raise ValueError('Invalid continuous run threshold')
        self.min_run=min_run_nm
        eligible=None if eligible_nets is None else set(eligible_nets)
        self.family=(lambda net:'matrix' if net in eligible else None) if eligible is not None else (lambda net:('matrix' if mixed_matrix else family(net)) if family(net) is not None else None)
        self.minimum=min_edge_gap;self.maximum=max_edge_gap;self.cell=max_edge_gap;self.rows=defaultdict(list);self.max_width=0
        for s in segments:
            f=self.family(s['net']);geometry=frame(s['start'],s['end'])
            if f is None or geometry is None:continue
            d,c,lo,hi=geometry;width=s['width'];self.max_width=max(self.max_width,width)
            self.rows[(s['layer'],f,d,math.floor(c/self.cell))].append((c,lo,hi,s['net'],width))

    def neighbor_intervals(self,a,z,layer,net,width):
        geometry=frame(a,z);f=self.family(net)
        if geometry is None or f is None:return {}
        d,c,lo,hi=geometry;radius=self.maximum+(width+self.max_width)/2;by_neighbor=defaultdict(list)
        for cell in range(math.floor((c-radius)/self.cell),math.floor((c+radius)/self.cell)+1):
            for nc,nlo,nhi,other,nwidth in self.rows.get((layer,f,d,cell),()):
                if other==net:continue
                gap=abs(c-nc)-(width+nwidth)/2
                if not self.minimum-1e-6<=gap<=self.maximum+1e-6:continue
                start,end=max(lo,nlo),min(hi,nhi)
                if end>start:by_neighbor[(other,round(nc,3),nwidth)].append((start,end))
        return by_neighbor

    def coverage(self,a,z,layer,net,width):
        geometry=frame(a,z)
        if geometry is None:return 0.0
        _,_,lo,hi=geometry;by_neighbor=self.neighbor_intervals(a,z,layer,net,width)
        def merged(intervals):
            if not intervals:return []
            output=[];start,end=sorted(intervals)[0]
            for a,b in sorted(intervals)[1:]:
                if a>end+1e-6:output.append((start,end));start,end=a,b
                else:end=max(end,b)
            return output+[(start,end)]
        eligible=[(a,b) for intervals in by_neighbor.values() for a,b in merged(intervals) if b-a+1e-6>=self.min_run]
        return min(hi-lo,max(0.0,sum(b-a for a,b in merged(eligible))))

    def surcharge(self,a,z,layer,net,width,strength):
        if not math.isfinite(strength) or not 0<=strength<=4:raise ValueError('Neighbour strength must be between0and4')
        if not strength:return 0.0
        return strength*max(0.0,math.dist(a,z)-self.coverage(a,z,layer,net,width))
