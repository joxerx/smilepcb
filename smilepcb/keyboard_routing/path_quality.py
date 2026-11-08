"""One positive path cost for foreign bundles, independent of signal roles."""
import math
import time
from smilepcb.routing_quality import current_quality,path_cost
from .role_quality import _runs
from .visual_geometry import UniformNeighbors


class BundlePathRanker:
    """A captured physical copper revision; recreate after copper changes."""
    def __init__(self,tracks,*,clearance_nm,eligible_nets=()):
        self.names={q['net'] for q in tracks.values()}|set(eligible_nets)
        self.rows=_runs(tracks,self.names);self.gap=clearance_nm;self._neighbors=None

    def rank(self,paths,*,layer,net,width,profile=None,deadline=None):
        profile=profile or current_quality()
        if not profile.bend_penalty_nm:return iter(paths)
        if deadline is not None and time.monotonic()>=deadline:return iter(paths)
        if net not in self.names:self.names.add(net);self._neighbors=None
        if self._neighbors is None:
            self._neighbors=UniformNeighbors(self.rows,eligible_nets=self.names,min_edge_gap=self.gap,
                max_edge_gap=max(1000000,self.gap+1),preferred_edge_gap_nm=self.gap,min_run_nm=2000000)
        def score(path):
            base=path_cost(path,profile=profile)
            if deadline is not None and time.monotonic()>=deadline:return base
            length=sum(math.dist(a,z) for a,z in zip(path,path[1:]))
            covered=sum(self._neighbors.coverage(a,z,layer,net,width) for a,z in zip(path,path[1:]))
            # No negative edges or reward from same-net or duplicate copper.
            return base+max(0,length-covered)
        return iter(sorted(paths,key=score))


def ranked_bundle_paths(paths,tracks,*,layer,net,width,clearance_nm,profile=None):
    profile=profile or current_quality()
    if not profile.bend_penalty_nm:return iter(paths)
    return BundlePathRanker(tracks,clearance_nm=clearance_nm).rank(paths,layer=layer,net=net,width=width,profile=profile)
