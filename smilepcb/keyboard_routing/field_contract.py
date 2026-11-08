"""Integrity receipt for explicitly constructed matrix field copper and terminals.

This is not a manufacturing certificate. Native connectivity is checked again
at each routing boundary. The constructed field stays fixed while other
copper is routed.
"""
from copy import deepcopy
import json
from pathlib import Path
from .field_policy import FieldPathPolicy

MARKER='.smilepcb-field.json'
ROLES={'row_trunk','row_link','row_access','column_trunk','column_access'}


def retired_ids(contract):
    import uuid
    values=contract.get('retired_source_ids',[])
    if (not isinstance(values,list) or len(values)>10000 or
        (values and contract.get('scope')!='matrix-with-controller')):
        raise ValueError('Invalid matrix interface retirement')
    for value in values:
        try:valid=isinstance(value,str) and str(uuid.UUID(value))==value
        except (ValueError,TypeError,AttributeError):valid=False
        if not valid:raise ValueError('Invalid retired matrix copper identity')
    return set(values)


def capture(state,report,*,prerequisite_ids=()):
    if report.get('complete') is not True:
        raise ValueError('Cannot hand off an incomplete matrix field')
    roles=report.get('roles',{})
    if not roles or set(roles)!=set(report.get('owned_ids',())) or not set(roles.values())<=ROLES:
        raise ValueError('Invalid matrix ownership record')
    ids=set(roles);prerequisites=set(prerequisite_ids)-ids
    if not prerequisites<=state['tracks'].keys():raise ValueError('Missing prerequisite copper')
    if not ids<=state['tracks'].keys():raise ValueError('Missing constructed matrix copper')
    from .generation import current_generation_profile
    result=deepcopy({'schema':1,'generation_profile':current_generation_profile().name,'roles':{u:roles[u] for u in sorted(ids)},
        'tracks':{u:state['tracks'][u] for u in sorted(ids)},
        'prerequisite_tracks':{u:state['tracks'][u] for u in sorted(prerequisites)},
        'footprints':state['footprints'],'terminals':report.get('terminals',{}),
        'scope':report.get('scope','key-field'),'retired_source_ids':report.get('retired_source_ids',[])})
    validate(state,result)
    return result


def validate(state,contract):
    if retired_ids(contract)&state['tracks'].keys():raise ValueError('Retired matrix interface unexpectedly returned')
    if (contract.get('schema')!=1 or not contract.get('roles') or
        set(contract['roles'])!=set(contract.get('tracks',{})) or
        not set(contract['roles'].values())<=ROLES or
        state['footprints']!=contract.get('footprints')):
        raise ValueError('Matrix handoff has changed ownership or physical terminals')
    for uid,shape in contract['tracks'].items():
        if state['tracks'].get(uid)!=shape:raise ValueError('Matrix spine was removed or changed')
        if shape['class']=='PCB_TRACK' and contract['roles'][uid]=='row_link':
            dx,dy=shape['end'][0]-shape['start'][0],shape['end'][1]-shape['start'][1]
            if dx and dy and abs(dx)!=abs(dy):raise ValueError('Row link violates octile geometry')
        if shape['class']=='PCB_TRACK' and contract['roles'][uid].endswith('_trunk'):
            role=contract['roles'][uid].split('_')[0]
            if not FieldPathPolicy(role).accepts_path([shape['start'],shape['end']]):
                raise ValueError('Matrix spine violates its direction policy')
    pads={d['uuid']:d['net'] for f in state['footprints'].values() for d in f['pads']}
    terminals=contract.get('terminals')
    if not isinstance(terminals,dict) or not terminals:raise ValueError('Matrix terminal inventory is empty')
    for uid,binding in terminals.items():
        if (not isinstance(binding,(list,tuple)) or len(binding)!=2 or binding[0] not in ('rows','columns')
            or pads.get(uid)!=binding[1]):raise ValueError('Matrix terminal binding changed')
    prerequisites=contract.get('prerequisite_tracks',{})
    if not isinstance(prerequisites,dict) or set(prerequisites)&set(contract['tracks']):
        raise ValueError('Invalid prerequisite copper ownership')
    for uid,shape in prerequisites.items():
        if state['tracks'].get(uid)!=shape:raise ValueError('Prerequisite copper was removed or changed')
    return sorted(set(contract['tracks'])|set(prerequisites))


def rebind_after_compose(before,after,contract,*,net_remap,paths):
    """Allow exactly the exported schematic paths/net labels, never new geometry.

    The caller must still prove native connectivity on the saved candidate.
    Rebinding is not permission to refresh a receipt from arbitrary PCB state.
    """
    validate(before,contract)
    expected=deepcopy(before)
    for item in expected['tracks'].values():item['net']=net_remap.get(item['net'],item['net'])
    for footprint in expected['footprints'].values():
        ref=footprint['ref']
        if ref not in paths:raise ValueError('Composition omitted a physical footprint path')
        footprint['path']=paths[ref]
        for pad in footprint['pads']:pad['net']=net_remap.get(pad['net'],pad['net'])
    if expected!=after:raise ValueError('Composition changed undeclared physical geometry or bindings')
    names={v[1] for v in contract['terminals'].values()}
    if len({net_remap.get(n,n) for n in names})!=len(names):raise ValueError('Composition merged distinct matrix signals')
    result=deepcopy(contract)
    result['tracks']={u:deepcopy(after['tracks'][u]) for u in contract['tracks']}
    result['prerequisite_tracks']={u:deepcopy(after['tracks'][u]) for u in contract.get('prerequisite_tracks',{})}
    result['footprints']=deepcopy(after['footprints'])
    result['terminals']={u:[role,net_remap.get(net,net)] for u,(role,net) in contract['terminals'].items()}
    validate(after,result)
    return result


def load(project):
    from .generated_seed import _read
    path=Path(project)/MARKER
    if not path.exists() and not path.is_symlink():return None
    return json.loads(_read(path,8*1024*1024))


def verify_native(board,contract,*,deadline=None):
    import time
    from smilepcb.native_worker import p,snapshot
    from .native_graph import copper_graph
    if board.GetCopperLayerCount()!=2:raise ValueError('Matrix handoff requires two copper layers')
    protected=validate(snapshot(board),contract)
    required=contract['terminals'];groups={};seen=set()
    if contract.get('scope')=='matrix-with-controller':
        nets={v[1] for v in required.values()}
        actual={d.m_Uuid.AsString() for f in board.GetFootprints() for d in f.Pads() if d.GetNumber() and d.GetNetname() in nets}
        if actual!=set(required):raise ValueError('Full matrix receipt omitted actual matrix pads')
    stop=time.monotonic()+15
    if deadline is not None:stop=min(stop,deadline)
    graph=copper_graph(board,{v[1] for v in required.values()},stop)
    for i,node in enumerate(graph['nodes']):
        if not isinstance(node['item'],p.PAD):continue
        uid=node['item'].m_Uuid.AsString()
        if uid not in required:continue
        seen.add(uid);binding=tuple(required[uid]);groups.setdefault(binding,set()).add(graph['find'](i))
    if seen!=set(required) or any(len(roots)!=1 for roots in groups.values()):
        raise ValueError('Matrix physical terminal connectivity was lost')
    return protected


def publish(board,path,contract,*,deadline=None):
    """Publish receipt before the PCB: hard termination can only fail closed."""
    import tempfile
    from smilepcb.native_worker import p
    path=Path(path)
    with tempfile.TemporaryDirectory(prefix='.smilepcb-field-',dir=path.parent) as folder:
        candidate=Path(folder)/'candidate.kicad_pcb'
        if not p.SaveBoard(str(candidate),board):raise ValueError('Cannot save matrix candidate')
        verify_native(p.LoadBoard(str(candidate)),contract,deadline=deadline)
        (path.parent/MARKER).write_text(json.dumps(contract,indent=2)+'\n')
        candidate.replace(path)


def guarded(request,operation):
    """Rollback a saved native operation if it violates the matrix boundary."""
    from smilepcb.native_worker import p
    path=Path(request['board']);contract=load(path.parent)
    if contract is None:
        if request.get('operation')!='matrix_field':return operation(request)
        original=path.read_bytes()
        try:return operation(request)
        except BaseException:
            if path.read_bytes()!=original:path.write_bytes(original)
            (path.parent/MARKER).unlink(missing_ok=True)
            raise
    original=path.read_bytes();receipt_path=path.parent/MARKER;original_receipt=receipt_path.read_bytes()
    entry=p.LoadBoard(str(path));ids=verify_native(entry,contract)
    if request.get('operation')=='joint_repair':
        from smilepcb.native_worker import snapshot
        from .joint_repair_contract import replace_field
        before=snapshot(entry)
        # Only this explicit operation may replace field ownership. All normal
        # callers retain the unchanged-receipt guard below.
        try:
            result=operation(request)
            if result.get('accepted') is True:
                transition=result.get('joint_field_transition',{})
                after_board=p.LoadBoard(str(path))
                expected=replace_field(before,snapshot(after_board),contract,
                    removed=transition.get('removed_ids',()),generated_ids=request.get('generated_ids',()),
                    protected_ids=request.get('protected_ids',()),new_roles=transition.get('new_roles',{}))
                if load(path.parent)!=expected:raise ValueError('Joint replacement receipt differs from permitted delta')
                verify_native(after_board,expected)
            elif path.read_bytes()!=original or receipt_path.read_bytes()!=original_receipt:
                raise ValueError('Rejected joint repair changed saved files')
        except BaseException:
            path.write_bytes(original);receipt_path.write_bytes(original_receipt)
            raise
        result['matrix_field_preserved']=True
        return result
    if request.get('operation')=='prune_reported_tails':
        from smilepcb.native_worker import snapshot
        from .reported_tail_contract import reported_ids,retire_prerequisites,require_exact_tail_delta,interface_source_ids
        sources=interface_source_ids(path.parent)
        hard=set(request.get('protected_ids',()))|sources
        expected=retire_prerequisites(contract,request['findings'],
            generated_ids=request.get('generated_ids',()),protected_ids=hard)
        selected=reported_ids(request['findings']);before=snapshot(entry)
        checked={**request,'protected_ids':sorted(set(request.get('protected_ids',()))|
            set(contract['tracks'])|set(expected['prerequisite_tracks'])|(sources&before['tracks'].keys()))}
        try:
            result=operation(checked)
            candidate=p.LoadBoard(str(path));after=snapshot(candidate)
            require_exact_tail_delta(before,after,selected)
            verify_native(candidate,expected)
            if receipt_path.read_bytes()!=original_receipt:
                raise ValueError('Tail worker changed its own field receipt')
            receipt_path.write_text(json.dumps(expected,indent=2)+'\n')
        except BaseException:
            path.write_bytes(original);receipt_path.write_bytes(original_receipt)
            raise
        result['matrix_field_preserved']=True
        result['retired_prerequisite_tail_ids']=sorted(selected&set(contract.get('prerequisite_tracks',{})))
        return result
    checked={**request,'protected_ids':sorted(set(request.get('protected_ids',()))|set(ids))}
    try:
        result=operation(checked)
        verify_native(p.LoadBoard(str(path)),contract)
        if receipt_path.read_bytes()!=original_receipt:raise ValueError('Ordinary routing changed field receipt')
    except BaseException:
        if path.read_bytes()!=original:path.write_bytes(original)
        if not receipt_path.exists() or receipt_path.read_bytes()!=original_receipt:receipt_path.write_bytes(original_receipt)
        raise
    result['matrix_field_preserved']=True
    return result
