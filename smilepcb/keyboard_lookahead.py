"""Bounded disposable recovery; provisional results never imply acceptance."""

def gap_signature(missing):
    if not isinstance(missing,dict) or any(type(net) is not str or not net or type(count) is not int or count<=0 for net,count in missing.items()):
        raise ValueError('Invalid native pad-gap inventory')
    return tuple(sorted(missing.items()))


def lookahead_limits(elastic):
    if type(elastic) is not bool:raise ValueError('Lookahead mode must be boolean')
    return (8,8) if elastic else (3,3)


def provisional_step(before,after,*,seen,depth,max_depth=3,gap_ceiling=None):
    gap_signature(before);signature=gap_signature(after)
    if type(max_depth) is not int or not 1<=max_depth<=8:raise ValueError('Lookahead depth must be bounded by eight')
    if gap_ceiling is not None and (type(gap_ceiling) is not int or not 1<=gap_ceiling<=8):
        raise ValueError('Invalid provisional gap ceiling')
    allowed=(type(depth) is int and 0<=depth<max_depth and sum(after.values())<=(sum(before.values()) if gap_ceiling is None else gap_ceiling) and after!=before and signature not in seen)
    return {'allowed':allowed,'complete':allowed and not after,'protect_healed':set(before)-set(after) if allowed else set()}


def run_lookahead(project,output,previous,protected,deadline,announce,*,native_call=None,checks_call=None,routing_quality='legacy',elastic=False):
    """Promote only checked zero; optionally explore bounded temporary gaps.

    Conservative mode retains three non-worsening hops. Elastic mode permits
    up to eight hops, never exceeding initial+2 gaps (capped at eight), and
    selects return recovery when the current provisional state needs it.
    The last 180 seconds of the caller's existing budget are reserved for
    final cleanup/acceptance. Every saved intermediate stays under one newly
    allocated disposable root; routing_transaction promotes only the board.
    """
    import json
    import secrets
    import shutil
    import tempfile
    import time
    from pathlib import Path
    from .keyboard_build import routing_transaction,NAME,BRIDGE
    from .lab_tools import worker,rule_checks
    from .service import encode
    from .routing_quality import quality_profile
    routing_quality=quality_profile(routing_quality).name
    max_depth,max_initial=lookahead_limits(elastic)
    project=Path(project).resolve();output=Path(output).resolve()
    missing=previous['pad_groups_missing'];gap_signature(missing)
    planes=set(json.loads((project/'.smilepcb-keyboard.json').read_text())['plane_nets'])
    reserve=180;trial_deadline=deadline-reserve
    if not missing or sum(missing.values())>max_initial or (not elastic and set(missing)&planes) or trial_deadline-time.perf_counter()<90:return previous
    ceiling=min(8,sum(missing.values())+2) if elastic else None
    root=Path(tempfile.mkdtemp(prefix='lookahead-',dir=output));token=secrets.token_hex(24)
    (root/'.smilepcb-lookahead-owner.json').write_text(encode({'token':token,'source_board':str(project/(NAME+'.kicad_pcb')),'elastic':elastic,'gap_ceiling':ceiling}))
    def remaining():
        value=trial_deadline-time.perf_counter()
        if value<=1:raise TimeoutError('Lookahead exhausted the existing build budget')
        return min(90,value)
    def invoke(operation,tag=None,**request):
        request.update(routing_quality=routing_quality,elastic=elastic,gap_ceiling=ceiling)
        budget=remaining()
        if native_call is not None:return native_call(operation,tag=tag,**request)
        bridge=Path(__file__).with_name('keyboard_lookahead_native.py') if operation in ('provisional','final_frame') else BRIDGE
        data=worker(bridge,dict(operation=operation,**{key:str(value) if isinstance(value,Path) else value for key,value in request.items()},budget_s=min(82,budget-1)),root/(tag+'.log'),timeout_s=budget)
        (root/(tag+'.json')).write_text(encode(data));return data
    current=project;seen={gap_signature(missing)};healed=set();history=[];final=None
    announce('bounded disposable routing lookahead')
    for depth in range(max_depth):
        if trial_deadline-time.perf_counter()<90:break
        candidate=root/('hop-'+str(depth))/'project';shutil.copytree(current,candidate)
        data=invoke('provisional',tag='hop-'+str(depth),board=candidate/(NAME+'.kicad_pcb'),
                    trial_root=root,owner_token=token,depth=depth,seen=[list(row) for row in sorted(seen)],
                    protected_ids=sorted(set(protected)),protect_nets=sorted(healed))
        if data.get('provisional_only') is not True or data.get('accepted_final') is not False:
            raise ValueError('Provisional worker returned a final-acceptance claim')
        if data.get('before')!=missing:raise ValueError('Stale provisional connectivity inventory')
        decision=provisional_step(missing,data['after'],seen=seen,depth=depth,max_depth=max_depth,gap_ceiling=ceiling)
        history.append({**data,'decision':{**decision,'protect_healed':sorted(decision['protect_healed'])}})
        (root/'history.json').write_text(encode({'provisional_only':True,'accepted_final':False,'hops':history}))
        if data.get('saved_trial') is not True or not decision['allowed']:break
        current=candidate;missing=data['after'];seen.add(gap_signature(missing))
        # A return repair changes the available signal corridors. Old generated
        # reservations must not become permanent obstacles in the new topology;
        # final frame/partition admission still covers the entire original board.
        if elastic and data.get('strategy')=='plane_negotiate':healed.clear()
        else:healed.update(decision['protect_healed'])
        if not missing:final=candidate;break
    if final is None:return previous
    frame=invoke('final_frame',tag='final-frame',board=final/(NAME+'.kicad_pcb'),trial_root=root,owner_token=token,
                 protected_ids=sorted(set(protected)))
    if frame.get('original_frame_preserved') is not True or frame.get('original_pad_partitions_preserved') is not True:
        raise ValueError('Lookahead lost the original native frame')
    independent=invoke('verify',tag='final-independent',board=final/(NAME+'.kicad_pcb'))
    if independent['pad_group_gaps'] or independent['pad_groups_missing']:return previous
    checks=(checks_call or rule_checks)(final,root/'final-checks',NAME,timeout_s=remaining(),deadline=trial_deadline)
    erc=checks['erc'];drc=checks['drc']
    if (erc['exit_code'] or erc['severity'] or erc['schematic_parity'] or erc['unconnected_items'] or
            drc['exit_code'] not in (0,5) or drc['severity'].get('error') or drc['schematic_parity'] or
            (drc['severity'] and (set(drc['severity'])!={'warning'} or not drc.get('types') or
             set(drc['types'])-{'track_dangling','via_dangling'}))):return previous
    remaining()
    def checked(candidate):
        shutil.copyfile(final/(NAME+'.kicad_pcb'),candidate/(NAME+'.kicad_pcb'))
        return independent
    transaction=routing_transaction(project,root/'promotion',checked,previous,deadline=trial_deadline)
    (root/'promotion.json').write_text(encode(transaction))
    return transaction['independent']


def generated_copper_inventory(board):
    """Identify actual saved generated objects; descriptions are never authority."""
    from . import schematic as s
    inventory={}
    for item in s.read(board):
        kind=s.tag(item)
        if kind not in ('segment','via','arc'):continue
        uid=str(s.one(item,'uuid')[1])
        if uid in inventory:raise ValueError('Duplicate generated copper identity')
        locked=any(str(value)=='locked' for value in item[1:] if not isinstance(value,list))
        state=s.one(item,'locked')
        if state is not None:locked=len(state)==1 or str(state[1]) in ('yes','true')
        inventory[uid]={'class':{'segment':'PCB_TRACK','via':'PCB_VIA','arc':'PCB_ARC'}[kind],'locked':locked}
    return inventory


def tail_cleanup_eligible(independent,checks,report,inventory,protected):
    """Permit only native-reported generated tails, including proven orphan pairs.

    The native remover still guards the entire pad partition. This is only
    permission to try removal; the final require_clean_rules gate is unchanged.
    """
    erc=checks['erc'];drc=checks['drc'];protected=set(protected)
    if (independent['pad_group_gaps'] or independent['pad_groups_missing'] or
            erc['exit_code'] or erc['severity'] or erc['schematic_parity'] or erc['unconnected_items'] or
            drc['exit_code'] not in (0,5) or drc['severity'].get('error') or drc['schematic_parity'] or report.get('schematic_parity')):return False
    findings=report.get('violations');pairs=report.get('unconnected_items')
    if not isinstance(findings,list) or not findings or not isinstance(pairs,list) or len(pairs)!=drc['unconnected_items']:return False
    def editable(uid):
        item=inventory.get(uid)
        return item is not None and item['class']=='PCB_TRACK' and item['locked'] is False and uid not in protected
    selected=set()
    for finding in findings:
        items=finding.get('items',())
        if finding.get('type')!='track_dangling' or finding.get('severity')!='warning' or len(items)!=1:return False
        uid=items[0].get('uuid')
        if not editable(uid):return False
        selected.add(uid)
    if drc['severity']!={'warning':len(findings)}:return False
    for pair in pairs:
        items=pair.get('items',())
        if pair.get('type')!='unconnected_items' or pair.get('severity')!='error' or len(items)!=2:return False
        ids=[item.get('uuid') for item in items]
        if len(set(ids))!=2 or not all(editable(uid) for uid in ids) or not selected.intersection(ids):return False
    return True
