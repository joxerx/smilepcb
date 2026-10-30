"""Support origins follow connected signal contacts, independent of core pose."""
from smilepcb.keyboard_placement import support_signal_preference


def test_signal_contacts_override_unrelated_template_origin():
    origin,nets=support_signal_preference([('debug',(2,1)),('GND',(-2,1))],
        [('debug',(104,81)),('GND',(500,500))],{'GND'},(10,20))
    assert origin==(102,80) and nets==['debug']


def test_each_signal_has_equal_weight_and_power_or_unconnected_pads_do_not_anchor():
    origin,nets=support_signal_preference([('a',(0,0)),('b',(0,0)),('+5V',(0,0)),('NC_end',(0,0))],
        [('a',(10,10)),('a',(10,10)),('b',(20,20)),('+5V',(1000,1000)),('NC_end',(1000,1000))],set(),(0,0))
    assert origin==(15,15) and nets==['a','b']


def test_preference_rotates_with_actual_contacts_and_pad_offsets():
    pads=[('a',(2,1))];contacts=[('a',(104,81))]
    rotate=lambda q:(-q[1],q[0])
    a,_=support_signal_preference(pads,contacts,set(),(0,0))
    b,_=support_signal_preference([(n,rotate(q)) for n,q in pads],[(n,rotate(q)) for n,q in contacts],set(),(0,0))
    assert b==rotate(a)


def test_unconnected_support_keeps_existing_fallback():
    assert support_signal_preference([('a',(0,0))],[('different',(2,3))],set(),(8,9))==((8,9),[])
