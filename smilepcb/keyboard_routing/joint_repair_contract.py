"""Pure, fail-closed contracts for a joint copper/field replacement.

These checks are necessary, not sufficient: the native caller also proves
all conductor components, source admission, full-union clearance and reload.
"""
from copy import deepcopy
import json
from pathlib import Path


class NativeRepairResult(dict):
    """JSON-compatible result whose private owners outlive native postguards.

    SWIG borrowed aliases must not outlive the board/detached owning wrappers.
    The caller owns this bounded final-world lifetime; no global registry,
    ownership-bit changes, frames, or non-JSON result entries are introduced.
    """
    def retain_native(self,*owners):
        if any(owner is self for owner in owners):raise ValueError('Result cannot retain itself')
        seen=set();kept=[]
        for owner in (*getattr(self,'_native_owners',()),*owners):
            if owner is not None and id(owner) not in seen:seen.add(id(owner));kept.append(owner)
        self._native_owners=tuple(kept)
        return self


def replace_field(before, after, contract, *, removed, generated_ids, protected_ids, new_roles):
    from .field_contract import validate
    validate(before, contract)
    removed=set(removed);old=before['tracks'];new=after['tracks']
    field=set(contract['tracks']);affected=removed&field
    if (not removed<=old.keys() or removed&(set(protected_ids)|set(contract.get('prerequisite_tracks',{})))
            or not removed-field<=set(generated_ids)
            or any(old[u]['locked'] for u in removed)):
        raise ValueError('Removal lacks unlocked generated/field ownership')
    if before['footprints']!=after['footprints']:
        raise ValueError('Physical terminals changed')
    if set(old)-set(new)!=removed or any(new.get(u)!=q for u,q in old.items() if u not in removed):
        raise ValueError('Retained copper changed')
    added=set(new)-set(old)
    if not set(new_roles)<=added:raise ValueError('New field ownership is not fresh copper')
    old_bindings={(contract['roles'][u],old[u]['net']) for u in affected}
    new_bindings={(role,new[u]['net']) for u,role in new_roles.items()}
    if old_bindings!=new_bindings:raise ValueError('Field role/net binding changed')
    matrix_nets={q[1] for q in contract['terminals'].values()}
    if {u for u in added if new[u]['net'] in matrix_nets}!=set(new_roles):
        raise ValueError('New matrix copper lacks exact role ownership')
    result=deepcopy(contract)
    result['roles']={u:r for u,r in contract['roles'].items() if u not in removed}
    result['roles'].update(new_roles)
    result['tracks']={u:deepcopy(new[u]) for u in result['roles']}
    result['prerequisite_tracks']={u:deepcopy(q) for u,q in contract.get('prerequisite_tracks',{}).items() if u not in removed}
    result['prerequisite_tracks'].update({u:deepcopy(new[u]) for u in sorted(added-set(new_roles))})
    validate(after,result)
    return result


def component_obligations(before_groups, removed):
    removed=set(removed);groups=[frozenset(q) for q in before_groups]
    if any(group and group<=removed for group in groups):raise ValueError('Replacement would erase an original conductor island')
    return [group-removed for group in groups if group-removed]


def require_components(obligations, after_groups, *, added=()):
    expected={frozenset(q) for q in obligations};old=set().union(set(),*expected)
    groups=[set(q) for q in after_groups]
    if {frozenset(q&old) for q in groups if q&old}!=expected:
        raise ValueError('Retained conductor component missing, split or merged')
    added=set(added)
    if not added<=set().union(set(),*groups) or any(q&added and not q&old for q in groups):
        raise ValueError('New copper detached from original conductors')


def require_partitions(before, after, target_ids):
    before={frozenset(q) for q in before};after={frozenset(q) for q in after};target=frozenset(target_ids)
    if not target or set().union(set(),*before)!=set().union(set(),*after):raise ValueError('Pad inventory changed')
    touched={q for q in before if q&target}
    if len(touched)<2 or set().union(set(),*touched)!=target:raise ValueError('Target omits complete original pad components')
    if after!=(before-touched)|{target}:raise ValueError('Target incomplete or another pad partition changed')


def publish_verified(board_path, receipt_path, board_bytes, receipt, verify):
    """Publish the coupled candidate; restore both exact files on any failure."""
    paths=[Path(board_path),Path(receipt_path)]
    original={p:p.read_bytes() if p.exists() else None for p in paths}
    try:
        paths[1].write_text(json.dumps(receipt,indent=2)+'\n')
        paths[0].write_bytes(board_bytes)
        return verify()
    except BaseException:
        for path,value in original.items():
            if value is None:path.unlink(missing_ok=True)
            else:path.write_bytes(value)
        raise


def _scan(text,start):
    if start>=len(text):raise ValueError('Missing expression')
    if text[start]=='(':
        i=start+1
        while True:
            while i<len(text) and text[i].isspace():i+=1
            if i>=len(text):raise ValueError('Unclosed expression')
            if text[i]==')':return i+1
            i=_scan(text,i)
    if text[start]=='"':
        i=start+1
        while i<len(text):
            if text[i]=='\\':i+=2;continue
            if text[i]=='"':return i+1
            i+=1
        raise ValueError('Unclosed string')
    if text[start]==')':raise ValueError('Unexpected close')
    i=start
    while i<len(text) and not text[i].isspace() and text[i] not in '()"':i+=1
    if i==start:raise ValueError('Empty token')
    return i


def child_spans(text):
    start=len(text)-len(text.lstrip())
    if start>=len(text) or text[start]!='(':raise ValueError('Expected expression')
    end=_scan(text,start)
    if text[end:].strip():raise ValueError('Trailing expression')
    i=start+1;result=[]
    while i<end-1:
        if text[i].isspace():i+=1;continue
        j=_scan(text,i);result.append((i,j));i=j
    return result


def children(text):return [text[a:b] for a,b in child_spans(text)]
def tag(text):return children(text)[0] if text.lstrip().startswith('(') else text


def node_uuid(text):
    if not text.lstrip().startswith('('):return None
    rows=[children(q) for q in children(text)[1:] if tag(q)=='uuid']
    if len(rows)>1 or any(len(q)!=2 for q in rows):raise ValueError('Ambiguous UUID')
    return json.loads(rows[0][1]) if rows else None


def all_uuids(text):
    result=[]
    if text.lstrip().startswith('('):
        for q in children(text)[1:]:
            if tag(q)=='uuid':result.append(json.loads(children(q)[1]))
            elif q.startswith('('):result.extend(all_uuids(q))
    return result


def splice_copper(original, removed, native_text, added, *, endpoint_replacements=None):
    """Keep all untouched raw nodes, including unmodeled properties, exact."""
    removed=set(removed);added=set(added);inventory=all_uuids(original);new_inventory=all_uuids(native_text)
    if tag(original)!='kicad_pcb' or tag(native_text)!='kicad_pcb':raise ValueError('Expected boards')
    if len(inventory)!=len(set(inventory)) or len(new_inventory)!=len(set(new_inventory)) or added&set(inventory):
        raise ValueError('Duplicate or colliding copper UUID')
    ranges=[];found=set()
    for a,b in child_spans(original)[1:]:
        node=original[a:b];uid=node_uuid(node)
        if uid in removed:
            if tag(node) not in {'segment','via'}:raise ValueError('Only copper replacement permitted')
            ranges.append((a,b));found.add(uid)
    if found!=removed:raise ValueError('Missing removed copper')
    nodes=[q for q in children(native_text)[1:] if node_uuid(q) in added]
    if {node_uuid(q) for q in nodes}!=added or any(tag(q) not in {'segment','via'} for q in nodes):
        raise ValueError('Missing new copper')
    replacements=endpoint_replacements or {}
    if not replacements.keys()<=removed:raise ValueError('Undeclared raw endpoint replacement')
    copied={}
    originals={node_uuid(original[a:b]):original[a:b] for a,b in ranges}
    for old,row in replacements.items():
        fields=set(row)-{'new_uuid'};uid=row.get('new_uuid')
        if len(fields)!=1 or not fields<={'start_nm','end_nm'} or uid not in added or uid in copied:
            raise ValueError('Expected one endpoint and a distinct new copper UUID')
        key=next(iter(fields));point=row[key];node=originals[old]
        if tag(node)!='segment' or len(point)!=2 or any(type(v) is not int for v in point):
            raise ValueError('Expected integer track endpoint replacement')
        def mm(value):return ('-' if value<0 else '')+str(abs(value)//1000000)+'.'+str(abs(value)%1000000).zfill(6)
        values={key[:-3]:'('+key[:-3]+' '+' '.join(mm(v) for v in point)+')','uuid':'(uuid '+json.dumps(uid)+')'}
        edits=[];found_fields=set()
        for a,b in child_spans(node)[1:]:
            kind=tag(node[a:b])
            if kind in values:
                if kind in found_fields:raise ValueError('Ambiguous raw endpoint field')
                found_fields.add(kind);edits.append((a,b,values[kind]))
        if found_fields!=set(values):raise ValueError('Missing raw endpoint field')
        for a,b,value in reversed(edits):node=node[:a]+value+node[b:]
        copied[uid]=node
    nodes=[copied.get(node_uuid(node),node) for node in nodes]
    output=original
    for a,b in reversed(ranges):output=output[:a]+output[b:]
    end=len(output.rstrip())-1
    return output[:end]+'\n'+'\n'.join(sorted(nodes,key=node_uuid))+'\n'+output[end:]
