"""Positive creation provenance carried with the saved generated project.

This local integrity ledger is not a signature. Authority comes from a trusted
creation boundary, never a worker's claimed added_ids or current-minus-protected
inventory. Existing unclassified copper remains immutable. Native electrical,
source-interface and field guards still decide whether an edit is admissible.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import uuid

from smilepcb import schematic as s
from .generated_seed import _read

MARKER='.smilepcb-generated.json'
READ_ONLY={'verify','inspect','field_verify','export_open','completion_candidates','final_frame'}
CREATORS={'usb','key_data_access','controller_ports','local_modules','controller_return_tree',
    'matrix_field','components','planes','stitch','complete','fanout','cleanup',
    'prune_reported_tails','import_open','negotiate','local','seed','joint_repair','provisional'}


def _tree(board):
    import sexpdata
    try:tree=sexpdata.loads(_read(Path(board)).decode('utf-8'),nil=None,true=None,false=None)
    except (ValueError,AssertionError) as error:raise ValueError('Invalid saved board expression') from error
    if s.tag(tree)!='kicad_pcb':raise ValueError('Invalid saved board root')
    return tree


def _canonical(value):
    if isinstance(value,list):return [_canonical(v) for v in value]
    if isinstance(value,(int,float)):return float(value)
    return str(value)


def inventory(board):
    """Bind every copper UUID to its net name and complete serialized properties."""
    tree=_tree(board)
    names={n[1]:str(n[2]) for n in tree if s.tag(n)=='net' and len(n)>2}
    result={}
    for node in tree:
        if s.tag(node) not in ('segment','via','arc'):continue
        identity=s.one(node,'uuid');binding=s.one(node,'net')
        if identity is None or binding is None:raise ValueError('Missing actual copper identity or net')
        uid=str(identity[1])
        if str(uuid.UUID(uid))!=uid or uid in result:raise ValueError('Invalid or duplicate copper identity')
        result[uid]={'net':names.get(binding[1],str(binding[1])),
            'shape':_canonical([node[0],*[n for n in node[1:] if s.tag(n) not in ('net','uuid')]])}
    return result


def has_zones(board):
    return any(s.tag(node)=='zone' and s.one(node,'keepout') is None for node in _tree(board))


def _load(board,before):
    path=Path(board).parent/MARKER
    if not path.exists() and not path.is_symlink():
        return {'schema':1,'generated':{},'imported':{},'retired_ids':[]}
    ledger=json.loads(_read(path,16*1024*1024))
    if (ledger.get('schema')!=1 or not isinstance(ledger.get('generated'),dict)
            or not isinstance(ledger.get('imported'),dict) or not isinstance(ledger.get('retired_ids'),list)
            or set(ledger['generated'])&set(ledger['imported'])):
        raise ValueError('Invalid generated provenance ledger')
    if set(ledger['retired_ids'])&set(before):raise ValueError('A retired copper identity returned')
    for kind in ('generated','imported'):
        for uid,entry in ledger[kind].items():
            if (not isinstance(entry,dict) or not isinstance(entry.get('creator'),str)
                    or not entry['creator'] or entry.get('binding')!=before.get(uid) or uid not in before):
                raise ValueError('Stale generated provenance binding')
    return ledger


def generated_ids(board):
    return sorted(_load(board,inventory(board))['generated'])


def _write(board,ledger):
    path=Path(board).parent/MARKER
    if path.is_symlink():raise ValueError('Generated provenance must not be a symlink')
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.smilepcb-provenance-',delete=False) as stream:
        staged=Path(stream.name)
        stream.write((json.dumps(ledger,indent=2,sort_keys=True)+'\n').encode())
    try:os.replace(staged,path)
    finally:staged.unlink(missing_ok=True)


def _advance(ledger,before,after,creator,protected=()):
    result=deepcopy(ledger)
    hard=(set(before)-set(ledger['generated']))|set(protected)
    if not set(protected)<=set(before):raise ValueError('Missing explicitly protected copper')
    if any(after.get(uid)!=before[uid] for uid in hard):raise ValueError('Changed protected or unknown copper')
    if set(after)&set(ledger['retired_ids']):raise ValueError('A retired copper identity returned')
    removed=set(before)-set(after)
    result['retired_ids']=sorted(set(ledger['retired_ids'])|removed)
    result['generated']={uid:{'binding':after[uid],'creator':entry['creator']}
        for uid,entry in ledger['generated'].items() if uid in after}
    for uid in set(after)-set(before):result['generated'][uid]={'binding':after[uid],'creator':creator}
    return result


def record_creation(board,before,*,creator,imported_ids=()):
    """Record an accepted controlled creator's observed before/after UUID delta.

    `before` must be captured immediately before the trusted creation operation.
    Imported IDs must already occur in that baseline; they never become generated.
    """
    if not isinstance(creator,str) or not creator:raise ValueError('Missing creation operation')
    ledger=_load(board,before)
    if not set(imported_ids)<=set(before) or set(imported_ids)&set(ledger['generated']):
        raise ValueError('Invalid imported creation baseline')
    for uid in imported_ids:ledger['imported'][uid]={'binding':before[uid],'creator':'imported-core'}
    _write(board,_advance(ledger,before,inventory(board),creator))



def record_import_creation(board,before,*,creator,immutable_ids,generated_ids):
    """Classify an observed fresh import at its trusted Native copy boundary.

    Only explicitly copied compiled copper can be generated. Existing unknown
    copper stays immutable; the two declared scopes must cover the exact delta.
    """
    if not isinstance(creator,str) or not creator:raise ValueError('Missing import creation operation')
    ledger=_load(board,before);after=inventory(board)
    immutable_ids=set(immutable_ids);generated_ids=set(generated_ids)
    if immutable_ids&generated_ids or immutable_ids|generated_ids != set(after)-set(before):
        raise ValueError('Invalid fresh import creation scope')
    result=_advance(ledger,before,after,creator)
    for uid in immutable_ids:
        result['generated'].pop(uid)
        result['imported'][uid]={'binding':after[uid],'creator':creator}
    _write(board,result)


def rebind_after_compose(board,before,*,net_remap):
    """Rebind only declared net names; identity and all other raw copper stay exact."""
    ledger=_load(board,before);after=inventory(board)
    expected={uid:{**binding,'net':net_remap.get(binding['net'],binding['net'])} for uid,binding in before.items()}
    if expected!=after:raise ValueError('Composition changed undeclared copper geometry or identities')
    for kind in ('generated','imported'):
        for uid,entry in ledger[kind].items():entry['binding']=after[uid]
    _write(board,ledger)



@contextmanager
def rollback_revision(board):
    """Restore the saved PCB and policy receipts if publication raises."""
    board=Path(board)
    paths=[board,board.parent/MARKER,board.parent/'.smilepcb-field.json',board.parent/'.smilepcb-interfaces.json']
    original={path:_read(path) if path.exists() or path.is_symlink() else None for path in paths}
    try:yield
    except BaseException:
        for path,data in original.items():
            if data is None:path.unlink(missing_ok=True)
            else:path.write_bytes(data)
        raise


def tracked_worker(worker,script,request,log,**kwargs):
    """Publish ownership only for a successful controlled saved-board operation.

    False acceptance, worker failure, stale provenance, or changed hard copper
    restores board and integrity receipts byte for byte. Candidate copies carry
    their ledger; the outer routing transaction promotes the accepted revision.
    """
    if request.get('operation') not in READ_ONLY|CREATORS:
        raise ValueError('Unknown controlled creation operation')
    board=Path(request['board']);before=inventory(board);ledger=_load(board,before)
    hard=sorted((set(before)-set(ledger['generated']))|set(request.get('protected_ids',())))
    if not set(hard)<=set(before):raise ValueError('Missing explicitly protected copper')
    if request['operation']=='prune_reported_tails':
        from .reported_tail_contract import interface_source_ids,authorize_reported_ids
        sources=interface_source_ids(board.parent)
        hard=sorted(set(hard)|(sources&before.keys()))
        authorize_reported_ids(request['findings'],generated_ids=ledger['generated'],protected_ids=hard)
    checked={**request,'protected_ids':hard}
    if request['operation'] in ('joint_repair','prune_reported_tails'):checked['generated_ids']=sorted(ledger['generated'])
    paths=[board,board.parent/MARKER,board.parent/'.smilepcb-field.json',board.parent/'.smilepcb-interfaces.json']
    original={p:_read(p) if p.exists() or p.is_symlink() else None for p in paths}
    try:
        result=worker(script,checked,log,**kwargs)
        if not isinstance(result,dict):raise ValueError('Invalid controlled operation result')
        if ('generation_profile' in request and
                result.get('generation_profile')!=request['generation_profile']):
            raise ValueError('Native worker generation profile differs from requested policy')
        if result.get('accepted') is False:
            if any((_read(p) if p.exists() else None)!=data for p,data in original.items()):
                raise ValueError('Rejected '+request['operation']+' controlled operation changed saved files')
            return result
        # These native workers consume source-interface policy; none publishes
        # it. Only the explicit field publishers may change the field receipt.
        immutable=[board.parent/MARKER,board.parent/'.smilepcb-interfaces.json']
        field=board.parent/'.smilepcb-field.json'
        tail_transition=request['operation']=='prune_reported_tails' and original[field] is not None
        if tail_transition:
            from .reported_tail_contract import retire_prerequisites
            expected=retire_prerequisites(json.loads(original[field]),request['findings'],
                generated_ids=ledger['generated'],protected_ids=hard)
            if not field.exists() or json.loads(_read(field))!=expected:
                raise ValueError('Reported-tail field receipt differs from the exact permitted removal')
        elif request['operation'] not in ('matrix_field','joint_repair'):
            immutable.append(field)
        if any((_read(path) if path.exists() or path.is_symlink() else None)!=original[path] for path in immutable):
            raise ValueError('Controlled operation changed an immutable receipt')
        after=inventory(board)
        if request['operation']=='prune_reported_tails':
            from .reported_tail_contract import reported_ids,require_exact_tail_delta
            require_exact_tail_delta({'tracks':before,'footprints':None},
                {'tracks':after,'footprints':None},reported_ids(request['findings']))
        if request['operation'] in READ_ONLY and after!=before:
            raise ValueError('Read-only operation changed copper')
        _write(board,_advance(ledger,before,after,str(request['operation']),hard))
        return result
    except BaseException:
        for path,data in original.items():
            if data is None:path.unlink(missing_ok=True)
            else:path.write_bytes(data)
        raise
