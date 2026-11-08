"""Exact reductions of the captured Compact68 continuation rejections."""
from smilepcb.keyboard_routing.bundle_geometry import paths_clear
from smilepcb.keyboard_routing.layered_simple_route import safe_layered_route
from smilepcb.keyboard_routing.polish_geometry import valid_shape


def test_two_legal_fragments_can_form_a_disallowed_right_angle_at_body_seam():
    body = [(130432483, 78663000), (130134741, 78365258)]
    tail = [body[-1], (129742699, 78757300)]
    assert valid_shape(body) and valid_shape(tail)
    assert not valid_shape(body + tail[1:])


def test_legal_tail_via_touches_previously_traversed_own_body_after_leaving_cap():
    via = (130700364, 78134742)
    body = [(134567517, 78337000), (130567517, 78337000), (130365259, 78134742)]
    tail = [body[-1], (130365259, 78056796), (130482196, 77939859),
            (130505481, 77939859), via]
    back = [via, (130598900, 78236206)]
    proposed = dict(legs=[(0, tail), (2, back)], vias=[via], via_widths=[500000])
    whole = dict(proposed, legs=[(0, body+tail[1:]), (2, back)])
    assert safe_layered_route(proposed, 150000, 500000)
    assert all(valid_shape(path) and paths_clear([path], [150000], 0) for _, path in whole['legs'])
    assert not safe_layered_route(whole, 150000, 500000)
