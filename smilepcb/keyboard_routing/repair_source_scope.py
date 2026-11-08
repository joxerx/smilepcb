"""Keep recorded controller sources usable across complex repair transactions.

A completed net may retire its old source outside repair. Without a verified
replacement head, complex repair must keep that current net closed instead
of carving its conductor and inventing a different original source receipt.
"""
import time
from .interface_launches import interface_identity


def repair_source_scope(state,ports,resolved,*,deadline):
    """Use the existing verified entry snapshot; do not build another graph."""
    if time.monotonic()>=deadline:raise TimeoutError('Repair source scope deadline exhausted')
    tracks=state['tracks'];pads={d['uuid']:d for f in state['footprints'].values() for d in f['pads']}
    resolved={tuple(key) for key in resolved};protected={};frozen=set()
    for port in ports:
        if time.monotonic()>=deadline:raise TimeoutError('Repair source scope deadline exhausted')
        shapes=port['source_shapes']
        if any(uid not in pads or shapes.get(uid)!=pads[uid] for uid in port['pad_ids']):
            raise ValueError('Repair source physical pad differs from its verified receipt')
        if all(uid in tracks and shapes.get(uid)==tracks[uid] for uid in port['added_ids']):
            protected.update((uid,tracks[uid]) for uid in port['added_ids'])
        elif interface_identity(port) in resolved:
            frozen.add(port['net'])
        else:raise ValueError('Repair cannot replace an unverified live controller source')
    protected.update((uid,shape) for uid,shape in tracks.items() if shape['net'] in frozen)
    return protected,frozen


def verify_frozen_repair_nets(before,after,nets,*,deadline):
    """A raw backend cannot ignore hard flags and reopen a retired source."""
    if time.monotonic()>=deadline:raise TimeoutError('Repair source preservation deadline exhausted')
    for net in nets:
        if time.monotonic()>=deadline:raise TimeoutError('Repair source preservation deadline exhausted')
        old={uid:q for uid,q in before['tracks'].items() if q['net']==net}
        current={uid:q for uid,q in after['tracks'].items() if q['net']==net}
        if old!=current:raise ValueError('Retired controller source net changed during repair')
