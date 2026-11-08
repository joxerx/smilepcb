"""Admission for routing stages that must retain already connected pads."""


def preserves_contacts(before,after):
    """Allow merging native pad groups, never splitting an existing contact."""
    def membership(groups):
        found={}
        if not isinstance(groups,list) or not groups:raise ValueError('Missing native pad partitions')
        for index,group in enumerate(groups):
            if not isinstance(group,list) or not group:raise ValueError('Empty native pad group')
            for uid in group:
                if not isinstance(uid,str) or uid in found:raise ValueError('Ambiguous native pad identity')
                found[uid]=index
        return found
    old,new=membership(before),membership(after)
    return old.keys()==new.keys() and all(len({new[uid] for uid in group})==1 for group in before)


def completed_pad_nets(partitions,pad_bindings):
    """Mark a net complete only when every actual pad is in one native group."""
    preserves_contacts(partitions,partitions)
    if (not isinstance(pad_bindings,dict) or not pad_bindings or
        {u for group in partitions for u in group}!=set(pad_bindings) or
        any(not isinstance(n,str) or not n for n in pad_bindings.values())):
        raise ValueError('Incomplete physical pad binding inventory')
    counts={}
    for group in partitions:
        names={pad_bindings[u] for u in group}
        if len(names)!=1:raise ValueError('Native group crosses different nets')
        net=next(iter(names));counts[net]=counts.get(net,0)+1
    return {net for net,count in counts.items() if count==1}
