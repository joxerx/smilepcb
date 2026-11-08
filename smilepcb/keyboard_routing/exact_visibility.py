"""Bounded hard-copper visibility attachment with complete-world admission."""
import math
import time

from .follow_geometry import simplify


def smooth(incoming,outgoing,layer):
    """Admit at most a 45-degree same-layer join, including straight travel."""
    dot=sum(a*b for a,b in zip(incoming,outgoing))
    return dot>0 and 2*dot*dot>=sum(a*a for a in incoming)*sum(b*b for b in outgoing)


def normalize(plan):
    """Merge visibility edges into ordered physical legs without losing vias."""
    legs=[];vias=list(map(tuple,plan['vias']))
    if len(vias)!=len(set(vias)):return None
    widths=plan.get('via_widths')
    if 'via_widths' in plan and (not isinstance(widths,(list,tuple)) or len(widths)!=len(vias)
            or any(type(d) not in (int,float) or not math.isfinite(d) or d<=0 for d in widths)):return None
    for face,path in plan['legs']:
        path=list(map(tuple,path))
        if len(path)<2 or legs and legs[-1][1][-1]!=path[0]:return None
        if legs and legs[-1][0]==face:legs[-1][1].extend(path[1:])
        else:legs.append((face,path))
    if not legs:return None
    legs=[(face,simplify(path)) for face,path in legs]
    ordered=[left[1][-1] for left,right in zip(legs,legs[1:])]
    if len(ordered)!=len(vias) or set(ordered)!=set(vias):return None
    route=dict(legs=legs,vias=ordered)
    if widths is not None:
        by_point=dict(zip(vias,widths));route['via_widths']=[by_point[point] for point in ordered]
    return route


def search(world,a,z,la,lz,net):
    """Emit one admitted attachment within the existing 512-point/5s bounds.

    Native dependencies are imported on use so obstacle planning can import
    this helper without creating an import cycle. The caller owns rollback,
    matching grid search and the other attachment strategies.
    """
    from .joint_repair_native import _Region,_plan_geometry,_emit
    from .grid_search import valid_plan
    region=_Region([a,z],padding=2000000)
    stop=min(world.deadline,time.monotonic()+5)
    policy=getattr(world,'path_policy',None)
    def reconstructed(plan):
        return normalize(dict(plan,via_widths=[world.via_width]*len(plan['vias'])))
    def complete(plan):
        route=reconstructed(plan)
        return route is not None and valid_plan(world,route['legs'],route['vias'],net)
    plans,diagnostic=_plan_geometry(world,world,[(a,face) for face in la],[(z,face) for face in lz],
                                    net,region,set(),stop,max_results=1,max_points=512,
                                    path_admission=None if policy is None else policy.accepts_path,
                                    turn_admission=smooth,plan_admission=complete)
    world.last_visibility=diagnostic
    for plan in plans:
        route=reconstructed(plan)
        if route is None or not valid_plan(world,route['legs'],route['vias'],net):continue
        _emit(world,dict(plan,**route,net=net))
        diagnostic.update(accepted=True,raw_edge_count=len(plan['legs']))
        return True
    return False
