"""Conservative frame from positively owned, already placed module objects."""


def core_exit_bounds_nm(core,*,footprint_type,fanout_bounds_nm=None):
    boxes=[]
    for item in core:
        box=item.GetBoundingBox(False,False) if isinstance(item,footprint_type) else item.GetBoundingBox()
        boxes.append((box.GetLeft(),box.GetTop(),box.GetRight(),box.GetBottom()))
    if not boxes:raise ValueError('Positive core object inventory is empty')
    if fanout_bounds_nm is not None:boxes.append(tuple(fanout_bounds_nm))
    return (min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))
