"""Request-scoped lifecycle of verified physical controller interfaces."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

_current=ContextVar('smilepcb_interface_launches',default=None)


def interface_identity(port):
    """Advancing a terminal keeps the same physical source-pad inventory."""
    return tuple(sorted(port['pad_ids']))


def resolved_interfaces(ports,previous,graph):
    """Retire only nets whose complete physical pad inventory is connected.

    A pair of source aliases joining each other is not a field connection.
    Recompute covered nets so removal/rollback can make an interface live again.
    """
    from smilepcb.native_worker import p
    covered=graph.get('nets',{node['net'] for node in graph['nodes']})
    roots={};pads={};source={}
    for port in ports:source.setdefault(port['net'],set()).update(port['pad_ids'])
    for i,node in enumerate(graph['nodes']):
        item=node['item']
        if isinstance(item,p.PAD) and item.GetNumber():
            net=node['net'];roots.setdefault(net,set()).add(graph['find'](i));pads.setdefault(net,set()).add(item.m_Uuid.AsString())
    result=set(previous)
    for port in ports:
        key=interface_identity(port);net=port['net']
        if net not in covered:continue
        actual=pads.get(net,set());expected=source[net]
        if len(roots.get(net,()))==1 and expected<=actual and actual-expected:result.add(key)
        else:result.discard(key)
    return result


class LaunchInventory:
    def __init__(self,ports,*,resolved=(),repair_reserved=()):
        self.ports={};self.update(ports)
        self.resolved={tuple(key) for key in resolved}&self.ports.keys()
        self.repair_reserved={tuple(key) for key in repair_reserved}&self.ports.keys()

    def update(self,ports):
        for port in ports:self.ports[interface_identity(port)]=deepcopy(port)

    def refresh(self,graph):
        self.resolved=resolved_interfaces(self.ports.values(),self.resolved,graph)


def current_launch_inventory():
    return _current.get()


def current_launches():
    inventory=current_launch_inventory()
    return tuple(inventory.ports.values()) if inventory is not None else ()


def publish_launches(ports,graph=None,*,resolved=None):
    """Publish accepted advancement only, never a speculative candidate."""
    inventory=current_launch_inventory()
    if inventory is not None:
        ports=tuple(ports);inventory.update(ports)
        if graph is not None:inventory.refresh(graph)
        if resolved is not None:
            # Transfer an accepted nested transaction's native closure without
            # changing unrelated interfaces in the caller's scope.
            keys={interface_identity(q) for q in ports}
            inventory.resolved.difference_update(keys)
            inventory.resolved.update(set(resolved)&keys)


@contextmanager
def reserve_launches(ports,*,resolved=(),repair_reserved=None):
    """Carry one physical interface inventory through this bounded phase."""
    parent=_current.get()
    if repair_reserved is None:repair_reserved=parent.repair_reserved if parent is not None else ()
    token=_current.set(LaunchInventory(ports,resolved=resolved,repair_reserved=repair_reserved))
    try:
        yield
    finally:
        _current.reset(token)
