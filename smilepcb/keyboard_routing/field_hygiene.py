"""Exact cleanup of editable matrix copper before sealing."""
import time
from smilepcb.routing_copper import uncovered


def covered_ids(tracks,editable_ids):
    """Drop wholly covered straight traces, retaining the canonical long pieces."""
    editable=set(editable_ids)
    if not editable<=tracks.keys():raise ValueError('Unknown editable matrix copper')
    live=dict(tracks);removed=[]
    def length(uid):
        t=live[uid]
        return sum((a-b)**2 for a,b in zip(t['start'],t['end']))
    for uid in sorted(editable,key=lambda u:(length(u),u)):
        item=live[uid]
        if item['class']!='PCB_TRACK' or item.get('locked') or not length(uid):continue
        if not uncovered(item,[t for key,t in live.items() if key!=uid]):
            del live[uid];removed.append(uid)
    return removed


def remove_covered(board,editable_ids):
    from smilepcb.native_worker import snapshot
    removed=covered_ids(snapshot(board)['tracks'],editable_ids)
    for item in list(board.GetTracks()):
        if item.m_Uuid.AsString() in removed:board.Delete(item)
    return removed


def editable_matrix_tracks(tracks,*,owned_ids,source_ids,nets):
    """Release only explicitly generated straight tracks before the receipt."""
    owned=set(owned_ids);source=set(source_ids);nets=set(nets)
    if not (owned|source)<=tracks.keys() or owned&source:
        raise ValueError('Unproven matrix ownership or overlapping original source')
    if any(tracks[uid]['net'] not in nets for uid in owned):
        raise ValueError('Generated matrix ownership includes another net')
    return {uid for uid in owned if tracks[uid]['class']=='PCB_TRACK' and not tracks[uid]['locked']}


def clean_matrix_tracks(board,*,owned_ids,source_ids,nets,deadline):
    """Remove generated tails/duplicates transactionally before fixed capture.

    Original source and every via remain exact. Removing or trimming an owned
    track must retain the entire real numbered-pad partition, including other
    nets, opposite faces and filled-plane contacts.
    """
    from smilepcb.native_worker import p,snapshot
    from .completion import pad_partitions
    from .cleanup import remove_tails
    before=snapshot(board)
    editable=editable_matrix_tracks(before['tracks'],owned_ids=owned_ids,source_ids=source_ids,nets=nets)
    protected=set(before['tracks'])-editable
    def remaining():
        left=deadline-time.monotonic()
        if left<=0:raise TimeoutError('Pre-receipt matrix cleanup deadline exhausted')
        return left
    def partition():
        remaining()
        return {frozenset(group) for group in pad_partitions(board,deadline)}
    def refill():
        if board.Zones() and not p.ZONE_FILLER(board).Fill(board.Zones()):
            raise ValueError('Cannot refill matrix cleanup contacts')
    original=partition();copies=[]
    for item in board.GetTracks():
        clone=item.Duplicate();clone.SetUuid(p.KIID(item.m_Uuid.AsString()));copies.append(clone)
    try:
        duplicates=remove_covered(board,editable)
        # Retain half of the existing remainder for exact native proof.
        remove_tails(board,protected=protected,budget_s=min(30,remaining()/2))
        live=set(snapshot(board)['tracks'])
        duplicates+=remove_covered(board,editable&live)
        board.BuildConnectivity();refill()
        after=snapshot(board)
        if before['footprints']!=after['footprints'] or set(after['tracks'])-set(before['tracks']):
            raise ValueError('Matrix cleanup changed components or added copper')
        if any(after['tracks'].get(uid)!=before['tracks'][uid] for uid in protected):
            raise ValueError('Matrix cleanup changed original source, locked, via or unowned copper')
        for uid in editable&after['tracks'].keys():
            old=before['tracks'][uid];new=after['tracks'][uid]
            if new!=old and uncovered(new,[old]):
                raise ValueError('Matrix cleanup moved generated copper outside its original geometry')
        if partition()!=original:raise ValueError('Matrix cleanup changed actual pad contacts')
        remaining()
        return {'removed_ids':sorted(set(before['tracks'])-set(after['tracks'])),
                'trimmed_ids':sorted(uid for uid in editable&after['tracks'].keys()
                                     if after['tracks'][uid]!=before['tracks'][uid]),
                'duplicate_ids':sorted(set(duplicates)),'all_pad_partitions_exact':True,
                'source_exact':True}
    except BaseException:
        for item in list(board.GetTracks()):board.Delete(item)
        for item in copies:board.Add(item)
        board.BuildConnectivity();refill()
        if snapshot(board)!=before:raise ValueError('Matrix cleanup rollback changed original geometry')
        raise
