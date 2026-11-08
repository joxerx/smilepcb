"""Geometry and outward terminal-heading admission for grid access edges."""
from smilepcb.keyboard_routing.polish_geometry import valid_shape,octile


class TerminalStyle:
    def __init__(self,bounds,endpoint_rays):
        self.bounds=tuple(bounds)
        self.endpoint_rays={tuple(point):frozenset(rays) for point,rays in endpoint_rays.items()}

    def accepts_path(self,path):
        if not valid_shape(path):return False
        if not all(self.bounds[0]<=q[0]<=self.bounds[2] and self.bounds[1]<=q[1]<=self.bounds[3] for q in path):return False
        for point,adjacent in ((path[0],path[1]),(path[-1],path[-2])):
            rays=self.endpoint_rays.get(tuple(point),())
            if len(rays)!=1:continue
            old=next(iter(rays));new=(adjacent[0]-point[0],adjacent[1]-point[1])
            dot=sum(a*b for a,b in zip(old,new))
            norm=sum(v*v for v in old)*sum(v*v for v in new)
            if dot>=0 or 2*dot*dot<norm:return False
        return True


def ranked_departures(a,z,heading=None):
    """Try late compaction first; keep every original octile alternative."""
    paths=list(octile(a,z))
    if heading is None:return paths
    def projection(path):
        return sum((path[1][i]-path[0][i])*heading[i] for i in (0,1)) if len(path)>1 else 0
    return sorted(paths,key=projection,reverse=True)
