"""Connect compatible source rails to actual matrix field components.

Cohorts split only along their physical lane order. Every accepted peel is a
real native MCU-to-field connection. The caller's complete matrix proof remains
the publication gate; this report never treats an outlet as a connected net.
"""
import time
from collections import deque
from copy import deepcopy
from smilepcb.native_worker import p,snapshot
from .collector_capacity import groups_for,partition_initial
from .collector_peel import compress,branch_goals,CollectorSearch,field_targets
from .native_graph import copper_graph
from .interface_launches import publish_launches


def route_collectors(board,cfg,ports,*,deadline,field_owned_ids=()):
    before=snapshot(board);live=deepcopy(ports);history=[];closed=set();failures=[]
    if time.monotonic()>=deadline:
        return {'accepted':False,'closed_nets':[],'history':[],'added_ids':[],'reason':'Collector deadline exhausted'}
    started=time.monotonic()
    planning_deadline=deadline-max(1,min(5,(deadline-started)*.15))
    try:targets=field_targets(board,cfg,live,field_owned_ids,planning_deadline)
    except TimeoutError as error:
        return {'accepted':False,'closed_nets':[],'ports':live,'history':history,'added_ids':[],'reason':'Field contact budget exhausted: '+str(error)}
    initial=groups_for(board,cfg,live)
    initial,partition=partition_initial(board,live,initial,deadline=planning_deadline)
    if partition is not None:history.append({'kind':'partition',**partition})
    pending=deque((nets[:],0,False) for nets in initial)
    while pending:
        turn_started=time.monotonic()
        if planning_deadline-turn_started<4:break
        nets,depth,prepared=pending.popleft();remaining=[n for n in nets if n not in closed]
        if not remaining:continue
        # A turn includes native oracle setup and an atomic contact proof.
        # Keep the existing attempt quantum; fractional shares can expire
        # before even one physical candidate can be admitted.
        budget=min(8 if len(remaining)>1 else 4,planning_deadline-turn_started)
        turn_end=min(planning_deadline,turn_started+budget)
        if len(remaining)>1 and not prepared:
            advanced,transition=compress(board,[q for q in live if q['net'] not in closed],remaining,deadline=min(turn_end,time.monotonic()+3),preserve_portal_pitch=True)
            updated={q['net']:q for q in advanced};live=[updated.get(q['net'],q) for q in live]
            publish_launches(live)
            history.append({'kind':'pitch','nets':remaining[:],'depth':depth,**transition})
        # Compression, goal inventory and search consume one finite turn.
        # Their continuation joins the tail, after every original cohort.
        if time.monotonic()>=turn_end:
            pending.append((remaining,depth,True));continue
        try:
            goals=branch_goals(board,cfg,[q for q in live if q['net'] not in closed],remaining,targets)
            point=goals[0].point;started=time.monotonic()
            if started>=turn_end:
                pending.append((remaining,depth,True));continue
            search=CollectorSearch(board,[q for q in live if q['net'] not in closed],remaining,{n:point for n in remaining},deadline=turn_end,goals=goals)
            result=search.solve();result.update(kind='peel',depth=depth,branch=search.branch,elapsed_s=time.monotonic()-started,cohort_budget_s=budget);history.append(result)
            if result['accepted']:
                live=[result['ports'].get(q['net'],q) for q in live]
                publish_launches(live,search.interface_graph)
                leaf=search.branch['net'];closed.add(leaf);remaining.remove(leaf)
                if remaining:pending.append((remaining,depth,True))
                continue
        except (ValueError,TimeoutError) as error:
            history.append({'kind':'peel','nets':remaining[:],'depth':depth,'accepted':False,'reason':str(error),'cohort_budget_s':budget})
        if len(remaining)>1:
            mid=len(remaining)//2
            pending.append((remaining[:mid],depth+1,False));pending.append((remaining[mid:],depth+1,False))
        else:failures.append({'nets':remaining,'reason':'No certified field branch within the shared budget'})
    for nets,_,_ in pending:
        remaining=[n for n in nets if n not in closed]
        if remaining:failures.append({'nets':remaining,'reason':'Shared collector deadline'})
    # Verify all numbered pads on each claimed net, not just the chosen pad.
    if time.monotonic()>=deadline:
        return {'accepted':False,'closed_nets':[],'history':history,'ports':live,'added_ids':sorted(set(snapshot(board)['tracks'])-set(before['tracks'])),'reason':'No collector proof budget remains'}
    try:
        graph=copper_graph(board,{q['net'] for q in ports},deadline)
    except TimeoutError as error:
        # This local sub-budget does not own the caller's whole matrix stage.
        # Preserve live source guards and leave all-net proof to that stage.
        return {'accepted':False,'closed_nets':[],'ports':live,'history':history,'added_ids':sorted(set(snapshot(board)['tracks'])-set(before['tracks'])),'reason':'Collector proof budget exhausted: '+str(error)}
    roots={q['net']:set() for q in ports}
    counts={n:0 for n in roots}
    for i,node in enumerate(graph['nodes']):
        item=node['item']
        if isinstance(item,p.PAD) and item.GetNumber():roots[node['net']].add(graph['find'](i));counts[node['net']]+=1
    verified={n for n,r in roots.items() if len(r)==1 and counts[n]>=2}
    if not closed<=verified:raise ValueError('Collector claimed a net with disconnected physical terminals')
    publish_launches(live,graph)
    after=snapshot(board)
    if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=t for u,t in before['tracks'].items()):raise ValueError('Collector changed source geometry')
    return {'accepted':verified==set(roots),'closed_nets':sorted(verified),'history':history,'initial_groups':initial,'failures':failures,'added_ids':sorted(set(after['tracks'])-set(before['tracks'])),'source_copper_preserved':True,'ports':live}
