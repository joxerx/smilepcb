"""Cold-build recovery orchestration using only the current generated project."""
import json
import itertools
from pathlib import Path
import time


_ABSENT_PROOF=object()


def _read_interface_proof(path):
    """Open once without following links; bound bytes on the opened inode."""
    import os
    import stat
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return _ABSENT_PROOF
    except OSError as error:raise ValueError('Invalid interface proof file') from error
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>1048576:
            raise ValueError('Invalid interface proof file')
        chunks=[];size=0
        while True:
            chunk=os.read(fd,min(65536,1048577-size))
            if not chunk:break
            chunks.append(chunk);size+=len(chunk)
            if size>1048576:raise ValueError('Invalid interface proof file')
        final=os.fstat(fd)
        if (info.st_size,info.st_mtime_ns,info.st_ctime_ns)!=(final.st_size,final.st_mtime_ns,final.st_ctime_ns) or size!=info.st_size:
            raise ValueError('Interface proof changed during read')
        return json.loads(b''.join(chunks))
    finally:os.close(fd)


def key_via_ids(proof):
    """Protect persistent key access vias, never historical editable lead IDs."""
    import uuid
    if not isinstance(proof,dict) or proof.get('accepted') is not True:
        raise ValueError('Key-data interface proof was not accepted')
    count=proof.get('required');rows=proof.get('ports')
    if type(count) is not int or not 0<=count<=10000 or not isinstance(rows,list) or len(rows)!=count:
        raise ValueError('Key-data interface proof is incomplete')
    ids=[]
    for row in rows:
        if not isinstance(row,dict) or row.get('connected') is not True:
            raise ValueError('Key-data pad has no accepted layer access')
        uid=row.get('via_id')
        try:valid=isinstance(uid,str) and str(uuid.UUID(uid))==uid
        except (ValueError,TypeError,AttributeError):valid=False
        if not valid:raise ValueError('Invalid key-data via identity')
        ids.append(uid)
    return sorted(set(ids))


def controller_interface_ids(source_project):
    """Load accepted controller interfaces and stable key-data access vias.

    Legacy absence is allowed. A malformed present inventory fails closed;
    native cleanup checks that every actual protected object still exists.
    """
    import re
    folder=Path(source_project).parent;ids=[]
    data=_read_interface_proof(folder/'controller-ports.json')
    if data is not _ABSENT_PROOF:
        if not isinstance(data,dict) or data.get('accepted') is not True:
            raise ValueError('Controller interface proof was not accepted')
        for key in ('ports','returns'):
            section=data.get(key)
            if not isinstance(section,dict) or section.get('accepted') is not True:
                raise ValueError('Controller interface section was not accepted')
            values=section.get('added_ids')
            if not isinstance(values,list) or len(values)>10000 or any(type(value) is not str or
                    not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',value) for value in values):
                raise ValueError('Invalid controller interface identifiers')
            ids.extend(values)
    keys=_read_interface_proof(folder/'key-data-access.json')
    if keys is not _ABSENT_PROOF:ids.extend(key_via_ids(keys))
    return sorted(set(ids))


def pending_interface_ids(board_file,owned_ids,pending_nets):
    """Keep live reserved copper only on nets whose pads are still separated.

    This releases protection, never deletes geometry. Native cleanup still
    preserves every pad partition, locked object and explicitly protected ID.
    """
    from . import schematic as s
    owned=set(owned_ids);pending=set(pending_nets)
    if not owned:return []
    tree=s.read(board_file)
    names={node[1]:node[2] for node in tree if s.tag(node)=='net' and len(node)>2}
    live={}
    for node in tree:
        if s.tag(node) not in ('segment','via','arc'):continue
        uid=str(s.one(node,'uuid')[1])
        if uid in live:raise ValueError('Duplicate actual copper identity')
        net=s.one(node,'net')[1]
        live[uid]=names.get(net,net)
    return sorted(uid for uid in owned if uid in live and live[uid] in pending)


def balanced_candidates(rows,limit):
    """Round-robin nearest alternatives so one crowded net cannot starve others."""
    by_net={}
    for row in rows:by_net.setdefault(row['target_net'],[]).append(row)
    return [row for batch in itertools.zip_longest(*by_net.values()) for row in batch if row is not None][:limit]


def plane_pairs(independent,planes):
    """Select native pad representatives, one isolated plane island at a time."""
    result=[]
    for net in sorted(set(independent['pad_groups_missing'])&set(planes)):
        groups=sorted(independent.get('disconnected_pad_groups',{}).get(net,[]),
                      key=lambda row:(-row['pad_count'],row['sample_pads']))
        if len(groups)<2:continue
        main=groups[0]['sample_pads'][0]
        for group in sorted(groups[1:],key=lambda row:(row['pad_count'],row['sample_pads'])):
            result.append([group['sample_pads'][0][:],main[:]])
    return result


def recover_generated(project,output,independent,protected,call,announce,deadline,*,port_ids=(),routing_quality='legacy'):
    """Keep improving checked transactions; retain all rejected diagnostics."""
    from .service import encode
    from .keyboard_build import routing_transaction,NAME
    output=Path(output);index=0;current=independent
    if not current['pad_group_gaps']:return current
    def attempt(strategy,**kwargs):
        nonlocal index,current
        if not current['pad_group_gaps']:return False
        if deadline-time.perf_counter()<95:raise TimeoutError('Insufficient build budget for another bounded recovery')
        tag='completion-'+str(index);index+=1;announce('geometric completion '+str(index)+' / '+strategy)
        folder=output/tag
        def repair(candidate):
            result=call('complete',tag=tag,board_override=candidate/(NAME+'.kicad_pcb'),strategy=strategy,
                        protected_ids=protected,**kwargs)
            repair.result=result
            if result['accepted']:return {**current,'pad_group_gaps':result['pad_group_gaps'],'pad_groups_missing':result['pad_groups_missing']}
            return current
        transaction=routing_transaction(project,folder,repair,current,deadline=deadline)
        (output/(tag+'-transaction.json')).write_text(encode(transaction))
        if transaction['accepted']:
            current=call('verify',tag=tag+'-saved')
        return transaction['accepted'],repair.result
    announce('prune obsolete generated routing before completion')
    # An intentionally open escape is an interface, not an obsolete tail.
    # Keep it during initial pruning; checked replacements can still edit
    # it, and final cleanup runs only after every pad group is connected.
    call('cleanup',tag='completion-cleanup',protected_ids=sorted(set(protected)|set(port_ids)))
    current=call('verify',tag='completion-initial')
    # Search preferences are relaxed after grouped routing, never physical rules.
    for i,step in enumerate((1000000,500000,250000,125000)):
        if not current['pad_group_gaps']:return current
        call('components',tag='unbiased-'+str(i),grid_step_nm=step,connection_budget_s=5,
             relax_preferences=True,completion_search=True,track_width_nm=150000)
        call('stitch',tag='unbiased-stitch-'+str(i));current=call('verify',tag='unbiased-verify-'+str(i))
    # Completed nets no longer need every provisional outward runway.
    # Keep unfinished interfaces, but let the native partition-preserving
    # cleaner retire redundant completed-net tails before local negotiation.
    if current['pad_group_gaps'] and port_ids:
        retained=pending_interface_ids(Path(project)/(NAME+'.kicad_pcb'),port_ids,current['pad_groups_missing'])
        if set(retained)!=set(port_ids):
            announce('retire completed interface reservations')
            call('cleanup',tag='completed-interface-cleanup',protected_ids=sorted(set(protected)|set(retained)))
            verified=call('verify',tag='completed-interface-connectivity')
            if verified['pad_groups_missing']!=current['pad_groups_missing']:
                raise ValueError('Interface retirement changed native pad groups')
            current=verified
    cfg=json.loads((Path(project)/'.smilepcb-keyboard.json').read_text());planes=set(cfg['plane_nets'])
    while current['pad_group_gaps']:
        before=current['pad_group_gaps'];missing=set(current['pad_groups_missing'])
        strategies=(['signal_negotiate'] if missing-planes else [])+(['plane_negotiate'] if missing&planes else [])
        for strategy in strategies:
            accepted,_=attempt(strategy)
            if accepted:break
        # A joined signal changes which generated conductors can be moved
        # next. Retry against the newly verified board, never a stale trial.
        # Strictly falling gap counts and the existing deadline bound retries.
        if current['pad_group_gaps']>=before:break
    if current['pad_group_gaps']:
        from .keyboard_lookahead import run_lookahead
        current=run_lookahead(project,output,current,protected,deadline,announce,routing_quality=routing_quality)
    if not current['pad_group_gaps']:return current
    # A batch may sacrifice one old signal after joining several returns.
    # Retry native island pairs separately, retaining only checked progress;
    # an unrepairable first island must not hide a safe later one.
    while current['pad_group_gaps']:
        before=current['pad_group_gaps']
        for pair in plane_pairs(current,planes):
            accepted,_=attempt('plane_negotiate',pad_pair=pair)
            if accepted:break
        if current['pad_group_gaps']>=before:break
    for strategy in ('route','spokes','route'):
        if not current['pad_group_gaps']:return current
        attempt(strategy)
    for net in sorted(set(current['pad_groups_missing'])-planes):
        hard=set()
        for retry in range(4):
            if not current['pad_group_gaps']:return current
            if net not in current['pad_groups_missing']:break
            success,result=attempt('via_shift',net=net,hard_ids=sorted(hard))
            if success:break
            failed=set(result.get('failed_vias',()))
            if not failed or failed<=hard:break
            hard|=failed
    # Re-discover from each improved saved board. No previous trial IDs or
    # failed-search logs are inputs. Native worker owns full partition guards.
    for sweep in range(2):
        if not current['pad_group_gaps']:return current
        discovered=call('completion_candidates',tag='detour-candidates-'+str(sweep),protected_ids=protected)['candidates']
        improved=False
        for candidate in balanced_candidates(discovered,6):
            for variant in range(4):
                if not current['pad_group_gaps']:return current
                success,result=attempt('detour',candidate=candidate,variant=variant)
                if success:improved=True;break
                if result.get('reason')=='No legal detour variant':break
            if improved:break
        if not improved:break
    if not current['pad_group_gaps']:return current
    for strategy in ('spokes','route'):
        if not current['pad_group_gaps']:return current
        attempt(strategy)
    if current['pad_group_gaps']:
        # Later shifts/detours change the state that the first conservative
        # lookahead saw. Explore that new topology within the same deadline.
        current=run_lookahead(project,output,current,protected,deadline,announce,
                              routing_quality=routing_quality,elastic=True)
    return current
