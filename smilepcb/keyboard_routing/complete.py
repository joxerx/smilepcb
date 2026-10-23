"""Bounded geometric connections between actual native copper components."""
from collections import defaultdict
import math
import time
from .local_router import LocalRouter,xy,distance
from .native_graph import copper_graph,stitch_zones


def connect_components(board, *, budget_s=45, max_length_mm=30, plane_nets=()):
    stop=time.monotonic()+budget_s
    nets={d.GetNetname() for f in board.GetFootprints() for d in f.Pads()
          if d.GetNetname() and not d.GetNetname().startswith('unconnected-')}
    nets-=set(plane_nets)
    graph=copper_graph(board,nets,stop)
    nodes=graph['nodes'];find=graph['find'];join=graph['join']
    from smilepcb.native_worker import p
    terminals={find(i) for i,node in enumerate(nodes)
               if isinstance(node['item'],p.PAD) and node['item'].GetNumber()}
    groups=defaultdict(list)
    for i,node in enumerate(nodes):
        if find(i) in terminals:groups[node['net']].append(i)
    candidates={}
    for net,ids in groups.items():
        if net in ('USB_DM','USB_DP'):continue
        for pos,i in enumerate(ids):
            if time.monotonic()>stop:break
            a=nodes[i]['item']
            for j in ids[:pos]:
                if find(i)==find(j):continue
                b=nodes[j]['item'];length=distance(xy(a.GetPosition()),xy(b.GetPosition()))
                if length>max_length_mm*1e6:continue
                pair=tuple(sorted((find(i),find(j))))
                previous=candidates.get(pair)
                if previous is None or length<previous[0]:candidates[pair]=(length,i,j)
    router=LocalRouter(board,deadline=stop,compact=True)
    attempts=[];connected=0
    for length,i,j in sorted(candidates.values()):
        if time.monotonic()>stop:break
        if find(i)==find(j):continue
        router.deadline=min(stop,time.monotonic()+.25)
        a,b=nodes[i]['item'],nodes[j]['item']
        ok=router.connect(a,b)
        if ok:join(i,j);connected+=1
        attempts.append({'net':nodes[i]['net'],'length_mm':length/1e6,'connected':ok})
    if any(not z.GetIsRuleArea() for z in board.Zones()):
        from smilepcb.native_worker import p
        p.ZONE_FILLER(board).Fill(board.Zones())
    return {'connections':connected,'added_ids':router.added,'attempts':attempts,
            'budget_exhausted':time.monotonic()>=stop}
