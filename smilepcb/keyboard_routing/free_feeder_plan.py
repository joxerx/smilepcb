"""Pure time allocation and physical-prefix selection; native admission stays separate."""
from copy import deepcopy
import math


def fallback_cutoffs(started,deadline,enabled=True):
    """Reserve one fallback inside the supplied clock, including its proof tail."""
    if (type(started) not in (int,float) or type(deadline) not in (int,float) or type(enabled) is not bool):
        raise ValueError('Fallback requires finite monotonic clocks and boolean enablement')
    try:
        remaining=deadline-started
        finite=all(math.isfinite(value) for value in (started,deadline,remaining))
    except OverflowError:finite=False
    if not finite:raise ValueError('Fallback clock interval must be finite')
    active=enabled and remaining>=40
    reserve=min(210.,remaining/2) if active else 0.
    proof=max(5.,min(30.,reserve/7)) if active else 0.
    return {'ordered_deadline':deadline-reserve,'routing_deadline':deadline-proof,
            'proof_deadline':deadline,'reserved_s':reserve,'enabled':active}


def _physical_interfaces(plan):
    ports=plan.get('ports')
    if not isinstance(ports,list) or not ports:raise ValueError('Accepted prefix has no physical interfaces')
    identities=set();claimed=set()
    for port in ports:
        ids=port.get('pad_ids') if isinstance(port,dict) else None
        if (not isinstance(ids,(list,tuple)) or not ids or
                any(not isinstance(uid,str) or not uid for uid in ids) or len(ids)!=len(set(ids))):
            raise ValueError('Accepted prefix has an invalid physical source identity')
        if claimed.intersection(ids):raise ValueError('Accepted prefix physical interfaces overlap')
        claimed.update(ids);identities.add(tuple(sorted(ids)))
    return identities,claimed


def best_accepted_prefix(search):
    """Recover one exact accepted ancestry, ranked by physical interface count.

    Native geometry, proof hashes and current source bindings must still be
    revalidated by the caller's rebuild. Rejected attempts contribute no copper.
    Equal scores retain the earliest accepted chain; labels and geometry do not
    select the winner. Returned plans never alias the search evidence.
    """
    if not isinstance(search,dict) or not isinstance(search.get('attempt_records'),list):
        raise ValueError('Fallback requires conditional search attempt records')
    records={};plans={};inventories={};best=[];best_count=0
    for index,row in enumerate(search['attempt_records'],1):
        if (not isinstance(row,dict) or type(row.get('index')) is not int or row['index']!=index or
                type(row.get('accepted')) is not bool or not isinstance(row.get('bank'),str) or not row['bank']):
            raise ValueError('Invalid conditional search attempt identity')
        prefix=row.get('prefix_attempts');depth=row.get('depth')
        if (not isinstance(prefix,list) or type(depth) is not int or depth!=len(prefix) or
                any(type(parent) is not int or not 0<parent<index for parent in prefix) or
                len(prefix)!=len(set(prefix))):
            raise ValueError('Invalid conditional prefix ancestry')
        banks=set();physical=set();count=0
        for level,parent in enumerate(prefix):
            ancestor=records[parent]
            if not ancestor['accepted'] or ancestor['prefix_attempts']!=prefix[:level]:
                raise ValueError('Conditional prefix mixes unrelated or rejected parents')
            if ancestor['bank'] in banks:raise ValueError('Conditional prefix repeats a physical bank')
            banks.add(ancestor['bank']);keys,pads=inventories[parent]
            if physical.intersection(pads):raise ValueError('Conditional prefix source identities overlap')
            physical.update(pads);count+=len(keys)
        if row['bank'] in banks:raise ValueError('Conditional prefix repeats a physical bank')
        if row['accepted']:
            plan=row.get('plan')
            if not isinstance(plan,dict) or plan.get('bank')!=row['bank']:
                raise ValueError('Accepted conditional plan has different bank ownership')
            keys,pads=_physical_interfaces(plan)
            if physical.intersection(pads):raise ValueError('Conditional prefix source identities overlap')
            plans[index]=plan;inventories[index]=(keys,pads);count+=len(keys)
            if count>best_count:
                best=[plans[parent] for parent in prefix]+[plan];best_count=count
        elif 'plan' in row:
            raise ValueError('Rejected conditional attempt cannot own an accepted plan')
        records[index]=row
    return deepcopy(best)
