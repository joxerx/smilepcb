"""Native copper copy shared by the controller recipe."""


def copy_copper(source,target,net):
    clone=source.Duplicate()
    target.Add(clone)
    clone.SetNet(net)
    return clone


def mirror_items(items):
    from smilepcb.native_worker import p
    for item in items:
        item.Flip(p.VECTOR2I(0,0),p.FLIP_DIRECTION_LEFT_RIGHT)
