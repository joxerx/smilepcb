"""A narrow removal-only transition for positively owned DRC-reported tails."""
from copy import deepcopy


def reported_ids(findings):
    if not isinstance(findings,list) or not 0<len(findings)<=10000:raise ValueError('Native tail findings required')
    selected=set()
    for row in findings:
        if (not isinstance(row,dict) or row.get('type')!='track_dangling' or row.get('severity')!='warning'
                or not isinstance(row.get('items'),list) or len(row['items'])!=1):
            raise ValueError('Only native track-dangling warnings are eligible')
        uid=row['items'][0].get('uuid')
        if not isinstance(uid,str) or not uid or uid in selected:raise ValueError('Unique reported tail identities required')
        selected.add(uid)
    return selected


def interface_source_ids(project):
    from .interface_contract import load_interfaces
    receipt=load_interfaces(project)
    if receipt is None:return set()
    if not isinstance(receipt,dict) or not isinstance(receipt.get('tracks'),dict):
        raise ValueError('Actual controller source receipt required')
    return set(receipt['tracks'])


def authorize_reported_ids(findings,*,generated_ids,protected_ids,matrix_ids=()):
    selected=reported_ids(findings)
    if not selected<=set(generated_ids) or selected&(set(protected_ids)|set(matrix_ids)):
        raise ValueError('Reported tail lacks unprotected generated prerequisite ownership')
    return selected


def retire_prerequisites(contract,findings,*,generated_ids,protected_ids):
    selected=reported_ids(findings)
    if (not isinstance(contract,dict) or contract.get('schema')!=1
            or not isinstance(contract.get('roles'),dict) or not isinstance(contract.get('prerequisite_tracks'),dict)):
        raise ValueError('Actual field prerequisite receipt required')
    authorize_reported_ids(findings,generated_ids=generated_ids,protected_ids=protected_ids,matrix_ids=contract['roles'])
    result=deepcopy(contract)
    result['prerequisite_tracks']={u:q for u,q in result['prerequisite_tracks'].items() if u not in selected}
    return result


def require_exact_tail_delta(before,after,selected):
    selected=set(selected);old=before['tracks'];new=after['tracks']
    if (not selected or not selected<=old.keys() or any(old[u].get('locked',False) for u in selected)
            or before['footprints']!=after['footprints'] or set(old)-set(new)!=selected or set(new)-set(old)
            or any(new.get(u)!=q for u,q in old.items() if u not in selected)):
        raise ValueError('Reported-tail transition changed unreported copper or terminals')
