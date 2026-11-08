"""Reject electrical self contacts on copper, not crossings of projections."""
import math
from .bundle_geometry import paths_clear,_point_distance


def safe_layered_route(route,width,via_diameter):
    legs=route['legs'];vias=list(map(tuple,route['vias']))
    if not legs or len(vias)!=len(legs)-1:return False
    diameters=route.get('via_widths',[via_diameter]*len(vias))
    if (not isinstance(diameters,(list,tuple)) or len(diameters)!=len(vias)
            or any(type(d) not in (int,float) or not math.isfinite(d) or d<=0 for d in diameters)):return False
    for i,((face,path),(other,next_path)) in enumerate(zip(legs,legs[1:])):
        if face==other or tuple(path[-1])!=tuple(next_path[0]) or tuple(path[-1])!=vias[i]:return False
    for face in {face for face,_ in legs}:
        paths=[path for f,path in legs if f==face]
        if not paths_clear(paths,[width]*len(paths),0):return False
    for i,via in enumerate(vias):
        for j,(_,path) in enumerate(legs):
            # The incident copper may bend while still inside the via land.
            # A convex disk meets each segment in one interval: permit only
            # the connected endpoint prefix, then forbid every later contact.
            incident=j==i or j==i+1
            outward=list(reversed(path)) if j==i else path
            exited=not incident;radius=(diameters[i]+width)/2
            for a,z in zip(outward,outward[1:]):
                if exited:
                    if _point_distance(via,a,z)+1e-6<radius:return False
                elif math.dist(via,z)+1e-6>=radius:exited=True
    return True
