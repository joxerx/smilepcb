"""Maximal physical chains: degree-two vias are geometry, not fixed terminals."""
from collections import defaultdict
from .follow_geometry import simplify


def assemble_chains(rows,vias):
    """Join vetted via contacts; never absorb a branch or a width transition.

    The native caller supplies only vias whose entire physical contact star is
    represented by the two arms. A closed copper ring has no replaceable
    endpoints, so its original chains are returned unchanged.
    """
    def key(q):return (q['net'],tuple(min(q['path'],list(reversed(q['path'])))),q['layer'],q['width'],tuple(sorted(q['ids'])))
    rows=sorted(rows,key=key);ends=defaultdict(list)
    for i,row in enumerate(rows):
        for side in (0,1):ends[row['net'],tuple(row['path'][0 if side==0 else -1])].append((i,side))
    links={}
    for via in sorted(vias,key=lambda v:(v['net'],tuple(v['position']),v['id'])):
        arms=ends.get((via['net'],tuple(via['position'])),[])
        if len(arms)!=2:continue
        (i,a),(j,b)=arms
        if i==j or rows[i]['width']!=rows[j]['width'] or rows[i]['layer']==rows[j]['layer']:continue
        if not {rows[i]['layer'],rows[j]['layer']}<=set(via['layers']):continue
        if (i,a) in links or (j,b) in links:continue
        links[i,a]=((j,b),via['id']);links[j,b]=((i,a),via['id'])
    used=set();out=[]
    starts=sorted(((i,s) for i in range(len(rows)) for s in (0,1) if (i,s) not in links),key=lambda a:(rows[a[0]]['net'],tuple(rows[a[0]]['path'][0 if a[1]==0 else -1]),key(rows[a[0]])))
    for i,side in starts:
        if i in used:continue
        row=rows[i];path=[];ids=[];old_legs=[];start_layer=row['layer'];net=row['net'];width=row['width']
        while True:
            if i in used:raise ValueError('Cross-layer chain revisited an arm')
            used.add(i);row=rows[i];leg=list(map(tuple,row['path'] if side==0 else reversed(row['path'])))
            path.extend(leg if not path else leg[1:]);old_legs.append((row['layer'],leg));ids.extend(row['ids']);end_layer=row['layer']
            joint=links.get((i,1-side))
            if joint is None:break
            (i,side),via_id=joint;ids.append(via_id)
        out.append(dict(net=net,width=width,path=simplify(path),ids=sorted(ids),old_legs=old_legs,start_layer=start_layer,end_layer=end_layer))
    # Rings are retained as their original independent geometry, not opened.
    for i,row in enumerate(rows):
        if i not in used:
            path=list(map(tuple,row['path']));out.append(dict(net=row['net'],width=row['width'],path=path,ids=sorted(row['ids']),old_legs=[(row['layer'],path)],start_layer=row['layer'],end_layer=row['layer']))
    return sorted(out,key=lambda q:(q['net'],q['path'],q['ids']))
