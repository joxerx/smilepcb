"""Reject nonincident self-contact in finite-width, two-face route proposals."""
import math
from .bundle_geometry import _distance,_point_distance
from .polish_geometry import valid_shape


def simple_layer_plan(legs,vias,*,width,via_width):
    """A face change may turn sharply; copper on one face may not loop back."""
    if width<=0 or via_width<width:raise ValueError('Physical track and via widths required')
    if not legs:return False
    via_points=[tuple(q) for q in vias];segments=[];previous=None
    if len(set(via_points))!=len(via_points):return False
    for face,path in legs:
        path=[tuple(q) for q in path]
        if len(path)<2 or not valid_shape(path):return False
        if previous is not None:
            prior_face,prior=previous
            if prior[-1]!=path[0]:return False
            if prior_face!=face and path[0] not in via_points:return False
            if prior_face==face and not valid_shape([prior[-2],path[0],path[1]]):return False
        segments.extend((face,a,b) for a,b in zip(path,path[1:]))
        previous=face,path
    for i,(face,a,b) in enumerate(segments):
        for j in range(i+1,len(segments)):
            other,c,d=segments[j]
            if face!=other or j==i+1 and b==c:continue
            if _distance(a,b,c,d)<=width:return False
    for point in via_points:
        incident=[(face,a,b) for face,a,b in segments if point in (a,b)]
        if not incident:return False
        radius=(via_width+width)/2
        if any(point not in (a,b) and _point_distance(point,a,b)<=radius for _,a,b in segments):return False
    if any(math.dist(a,b)<=via_width for i,a in enumerate(via_points) for b in via_points[i+1:]):return False
    return True
