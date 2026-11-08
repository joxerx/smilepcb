"""Actual contiguous-pad cohorts and whole-profile fresh source domains.

No board mutation, field targets, independent selector or fixture geometry.
Native candidate construction and compatibility remain caller-supplied guards.
"""
from collections import Counter,defaultdict
import hashlib,json,math,time
from .bank_geometry import port_outside_bounds
from .joint_source_transition import CARDINAL
from .staged_sources import source_head_proposals


def _check(deadline):
    if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('Coherent via bank deadline exhausted')


def discover_cohorts(pads,eligible_nets,*,deadline=None):
    """Split physical side inventories at every nonselected/unsupported pad.

    Records contain ref,pad_id,net,point,faces,heading,bounds_nm. A net must own
    exactly one physical MCU pad. Singletons/unsupported records remain legacy.
    """
    eligible=set(eligible_nets);counts=Counter(q['net'] for q in pads);sides=defaultdict(list);cohorts=[];fallback={}
    for pad in pads:
        _check(deadline);sides[pad['ref'],tuple(pad['heading'])].append(pad)
    for (ref,h),rows in sorted(sides.items(),key=lambda q:repr(q[0])):
        t=(-h[1],h[0]);position=lambda q:sum(q['point'][i]*t[i] for i in (0,1))
        duplicates=Counter(position(q) for q in rows);run=[];face=None
        def flush():
            if len(run)>=2:
                members=tuple(q['net'] for q in run);ids=tuple(q['pad_id'] for q in run)
                cohorts.append(dict(id=(ref,face,h,ids),ref=ref,face=face,heading=h,members=members,pad_ids=ids,pads=list(run)))
            elif run:fallback[run[0]['net']]='singleton physical cohort'
            run.clear()
        for pad in sorted(rows,key=lambda q:(position(q),q['pad_id'])):
            _check(deadline)
            supported=(pad['net'] in eligible and counts[pad['net']]==1 and len(pad['faces'])==1
                and h in CARDINAL and pad.get('supported',True) and duplicates[position(pad)]==1)
            if not supported:
                flush()
                if pad['net'] in eligible:fallback[pad['net']]='unsupported or ambiguous physical pad interface'
                continue
            actual_face=pad['faces'][0]
            if run and actual_face!=face:flush()
            face=actual_face;run.append(pad)
        flush()
    assigned={n for q in cohorts for n in q['members']}
    for net in eligible-assigned:fallback.setdefault(net,'missing or unsupported physical MCU pad')
    return cohorts,{n:reason for n,reason in fallback.items() if n not in assigned}


def cohort_geometry(cohort,*,body_face,width_nm,via_width_nm,via_drill_nm,clearance_nm,
                    track_margin_nm,via_land_margin_nm,drill_land_margin_nm,runway_nm,
                    exit_bounds_nm,explicit_exit_bounds,maximum_distance_nm,continuation_nm,deadline):
    """Adapt source heads to the real production straight-runway/exit contract.

    Via coordinates remain staggered. The actual via-to-exit segment is extended
    past the supplied frame and by at least runway_nm; continuation is physical
    candidate geometry, never a synthetic field target.
    """
    _check(deadline);h=tuple(cohort['heading']);axis=0 if h[0] else 1;sign=h[axis];lanes=[]
    for pad in cohort['pads']:
        origin=pad['point'];box=pad['bounds_nm']
        projections=[sum((q[k]-origin[k])*h[k] for k in (0,1)) for q in ((box[0],box[1]),(box[2],box[3]))]
        lanes.append(dict(net=pad['net'],source=tuple(origin),source_layer=cohort['face'],heading=h,width=width_nm,
            source_pad_id=pad['pad_id'],source_forward_extent_nm=max(0,max(projections)),source_backward_extent_nm=max(0,-min(projections)),
            source_track_margin_nm=track_margin_nm,source_via_copper_margin_nm=via_land_margin_nm,source_drill_land_margin_nm=drill_land_margin_nm))
    proposals=source_head_proposals(lanes,body_face=body_face,via_width_nm=via_width_nm,via_drill_nm=via_drill_nm,
        clearance_nm=clearance_nm,deadline=deadline,outlet_nm=min(500000,runway_nm),limit=64)
    result=[];seen=set();edge=exit_bounds_nm[axis+(2 if sign>0 else 0)]
    margin=width_nm//2+1 if explicit_exit_bounds else runway_nm
    for proposal in proposals:
        _check(deadline);rows=[]
        for pad,route in zip(cohort['pads'],proposal['routes']):
            if len(route['vias'])!=1 or len(route['legs'])!=2:raise ValueError('Fresh coherent via receipt requires one actual layer transition')
            via=tuple(route['vias'][0])
            if math.dist(pad['point'],via)>maximum_distance_nm:break
            end=list(via);end[axis]=sign*max(sign*via[axis]+runway_nm,sign*edge+margin)
            end=tuple(end)
            if not port_outside_bounds(end,h,exit_bounds_nm,width_nm):break
            future=tuple(end[k]+h[k]*continuation_nm for k in (0,1))
            rows.append(dict(net=pad['net'],pad_id=pad['pad_id'],path=route['legs'][0][1],layer=cohort['face'],
                via_nm=via,exit_nm=end,exit_layer=body_face,heading_origin_nm=via,future_end_nm=future,axis=axis,sign=sign,
                kind='via',bank=(cohort['ref'],axis,sign),cohort=cohort['id'],cohort_pad_ids=cohort['pad_ids'],
                cohort_members=cohort['members'],escape_side=proposal['escape_side'],rail=proposal['rail']))
        if len(rows)!=len(cohort['pads']):continue
        geometry=[{k:r[k] for k in ('pad_id','path','layer','via_nm','exit_nm','exit_layer','future_end_nm')} for r in rows]
        signature=hashlib.sha256(json.dumps([width_nm,via_width_nm,via_drill_nm,geometry],sort_keys=True).encode()).hexdigest()
        if signature in seen:continue
        seen.add(signature);profile=('coherent-via-v1',signature)
        for row in rows:row.update(profile=profile,cohort_profile=profile)
        result.append(rows)
    return result


def cap_cohort_domains(domains,members,limits,*,deadline,compatible=None):
    """Intersect whole profiles before any per-net cap; keep one common menu.

    Optional Native peer admission filters an entire profile, using the same
    compatibility predicate as the production selector. It does not select nets.
    """
    _check(deadline)
    by_net={n:{r['cohort_profile']:r for r in domains.get(n,[])} for n in members}
    shared=set.intersection(*(set(rows) for rows in by_net.values())) if members else set()
    available=len(shared);ranked=[];rejected=0
    for profile in shared:
        _check(deadline);rows=[by_net[n][profile] for n in members];okay=True
        if compatible is not None:
            for i,a in enumerate(rows):
                for b in rows[i+1:]:
                    _check(deadline)
                    if not compatible(a,b):okay=False;break
                if not okay:break
        if not okay:rejected+=1;continue
        ranked.append((sum(r.get('cost',0) for r in rows),repr(profile),profile))
    cap=min((limits[n] for n in members),default=0)
    selected=[q[2] for q in sorted(ranked)[:max(0,cap)]]
    return {n:[by_net[n][p] for p in selected] for n in members},dict(available_profiles=available,
        peer_rejected_profiles=rejected,retained_profiles=len(selected),whole_profile_cap=cap)


def _selection_summary(rows,cohort):
    """Actual emitted source length excludes cost penalties and future reserve."""
    details=[]
    for row in rows:
        path=row['path'];origin=row.get('heading_origin_nm')
        if origin is None:origin=row.get('via_nm') if row.get('kind')=='via' else path[-2]
        tip=row['exit_nm'];dx,dy=tip[0]-origin[0],tip[1]-origin[1];g=math.gcd(dx,dy)
        heading=(dx//g,dy//g) if g else None
        source_length=sum(math.dist(a,b) for a,b in zip(path,path[1:]))
        runway=math.dist(row['via_nm'],tip) if row.get('kind')=='via' else 0
        details.append(dict(net=row['net'],kind=row.get('kind','via'),exit_nm=tip,exit_layer=row['exit_layer'],
            exit_heading=heading,source_length_nm=source_length,runway_length_nm=runway,
            actual_length_nm=source_length+runway,via_count=int(row.get('kind','via')=='via')))
    same_layer=bool(rows) and all(row.get('kind')=='same_layer' and row.get('bank')==rows[0].get('bank')
        and row.get('profile')==rows[0].get('profile') for row in rows)
    same_layer=same_layer and rows[0].get('bank') is not None and rows[0].get('profile') is not None
    same_layer=same_layer and all(d['exit_heading']==details[0]['exit_heading'] and d['exit_layer']==details[0]['exit_layer'] for d in details)
    coherent_via=bool(rows) and all(row.get('kind','via')=='via' and row.get('cohort')==cohort['id']
        and row.get('cohort_profile') is not None and row.get('cohort_profile')==rows[0].get('cohort_profile') for row in rows)
    return dict(coherence='same_layer' if same_layer else 'coherent_via' if coherent_via else 'mixed_or_unprofiled',
        rows=details,actual_length_nm=sum(d['actual_length_nm'] for d in details),via_count=sum(d['via_count'] for d in details),
        selector_cost=sum(row.get('cost',0) for row in rows))


def refine_cohort_selection(selected,cohorts,menu_builder,*,compatible,deadline,max_candidates,source_budget=None,accept_complete=None):
    """Atomically prefer whole profiles over an already complete feasible set.

    No selector or board mutation is performed. menu_builder(cohort,limit,charge)
    must charge each additional per-net Native candidate consideration, including
    rejected candidates. Every swap checks all replacement/retained peer pairs
    and any existing complete-head constraint before replacing any member.
    """
    chosen=list(selected);receipts=[]
    for cohort in cohorts:
        physical=set(cohort['pad_ids']);indices=[i for i,row in enumerate(chosen) if physical & set(row['pad_ids'])]
        current=[chosen[i] for i in indices];by_net={row['net']:row for row in current}
        entry=dict(id=cohort['id'],members=cohort['members'],pad_ids=cohort['pad_ids'],profile_checks=0,
            additional_candidate_checks={n:0 for n in cohort['members']},outcome='unchanged_no_compatible_profile')
        receipts.append(entry)
        entry['before']=_selection_summary(current,cohort);entry['after']=entry['before']
        if (set(by_net)!=set(cohort['members']) or len(current)!=len(cohort['members'])
                or physical-set().union(*(set(row['pad_ids']) for row in current))):
            entry.update(outcome='unchanged_unsupported_binding');continue
        if entry['before']['coherence']!='mixed_or_unprofiled':
            entry['outcome']='kept_existing_coherent';continue
        if time.monotonic()>=deadline:entry['outcome']='skipped_deadline';continue
        limits=[max_candidates if source_budget is None else source_budget.candidate_limit(row['pad_ids'],max_candidates) for row in current]
        limit=min(limits)
        if limit<=0:entry['outcome']='skipped_budget';continue
        def charge(net):
            _check(deadline)
            if net not in by_net or entry['additional_candidate_checks'][net]>=limit:raise ValueError('Coherent refinement candidate cap exceeded')
            if source_budget is not None:source_budget.charge_candidates(by_net[net]['pad_ids'],1)
            entry['additional_candidate_checks'][net]+=1
        try:
            profiles,diagnostic=menu_builder(cohort,limit,charge);entry.update(diagnostic)
            if len(profiles)>limit:raise ValueError('Coherent refinement profile cap exceeded')
            profiles=sorted(profiles,key=lambda rows:(sum(row.get('cost',0) for row in rows),repr([r.get('cohort_profile') for r in rows])))
            for rows in profiles:
                _check(deadline);entry['profile_checks']+=1
                replacement={row['net']:row for row in rows}
                if len(rows)!=len(current) or set(replacement)!=set(by_net):continue
                profile=rows[0].get('cohort_profile')
                if profile is None or any(row.get('cohort')!=cohort['id'] or row.get('cohort_profile')!=profile
                        or set(row['pad_ids'])!=set(by_net[row['net']]['pad_ids']) for row in rows):continue
                outside=[row for i,row in enumerate(chosen) if i not in indices];okay=True
                for j,a in enumerate(rows):
                    for b in rows[j+1:]+outside:
                        _check(deadline)
                        if not compatible(a,b):okay=False;break
                    if not okay:break
                if not okay:continue
                proposed=list(chosen)
                for i in indices:proposed[i]=replacement[chosen[i]['net']]
                _check(deadline)
                if accept_complete is not None and not accept_complete(proposed):continue
                _check(deadline)
                chosen=proposed;entry.update(outcome='applied_profile',applied_profile=profile,
                    after=_selection_summary([chosen[i] for i in indices],cohort));break
        except TimeoutError as error:
            entry.update(outcome='skipped_deadline',reason=str(error))
    return chosen,receipts
