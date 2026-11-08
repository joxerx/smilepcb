"""HOST-only chain/pose regression; no pcbnew import or physical qualification."""
from copy import deepcopy
import hashlib
import itertools
import json
import math
from pathlib import Path

import pytest
from smilepcb.keyboard_layout import parse_layout
from smilepcb.keyboard_model import design

ROOT=Path(__file__).resolve().parents[1]


def layout(name):
    fixture=ROOT/'smilepcb/stm32/inputs'/name
    return parse_layout((fixture/'layout.kle.json').read_text(),json.loads((fixture/'keyboard.json').read_text()))


@pytest.mark.parametrize('name,links',[('ansi61',56),('compact68',63)])
def test_model_directed_same_row_data_pads_face_the_next_package(name,links):
    cfg=design(layout(name),controller_origin_mm=(120,70));parts={p['ref']:p for p in cfg['parts']};checked=0
    # Source footprint DIN is left, DOUT right. LEFT_RIGHT back-face placement
    # mirrors X before the recipe rotation: B.Cu recipe0 departs left;180 right.
    for a,b in zip(cfg['layout']['rgb_order'],cfg['layout']['rgb_order'][1:]):
        source,target=parts['LED'+str(a)],parts['LED'+str(b)]
        if source['pcb'][1]!=target['pcb'][1]:continue
        want=0 if target['pcb'][0]<source['pcb'][0] else 180;checked+=1
        assert (source['rotation'],target['rotation'])==(want,want)
    assert checked==links


@pytest.mark.parametrize('name,want',[
    ('ansi61','3adb2ccce7d8218663a9cc7303697a49953b5145dcbffa5c6e27168c278f3b8d'),
    ('compact68','f68b8d4318503fc63f9d325a269f04be850fcf49eaf6adcfbbfea95dcc4d4332'),
])
def test_model_changes_only_led_rotation_from_frozen_7f(name,want):
    source=layout(name);old=deepcopy(source);cfg=design(source,controller_origin_mm=(120,70))
    assert source==old
    for part in cfg['parts']:
        if part['symbol']=='KeyboardParts:MINI_E':part.pop('rotation')
    assert hashlib.sha256(json.dumps(cfg,sort_keys=True,separators=(',',':')).encode()).hexdigest()==want


def test_no_rgb_model_is_unchanged_from_frozen_7f():
    cfg=design(layout('st40-grid48'),controller_origin_mm=(120,70))
    assert hashlib.sha256(json.dumps(cfg,sort_keys=True,separators=(',',':')).encode()).hexdigest()=='c2d18250ec23084b278aa883f864a6be4d467ccc88fb008236e5f5ac7da58769'


def choose():
    from smilepcb.led_orientation import chain_rotations
    return chain_rotations


def test_pad_offsets_come_from_the_materialized_recipe_footprint():
    from smilepcb.led_orientation import data_offsets_nm
    assert data_offsets_nm()==((-2725000,-750000),(2725000,750000))


@pytest.mark.parametrize('side,rotation,want',[
    ('F.Cu',0,(3000000,1000000)),('F.Cu',180,(-3000000,-1000000)),
    ('B.Cu',0,(-3000000,1000000)),('B.Cu',180,(3000000,-1000000)),
])
def test_recipe_flip_precedes_rotation(side,rotation,want):
    from smilepcb.led_orientation import posed_offset
    assert posed_offset((3000000,1000000),side,rotation)==want


@pytest.mark.parametrize('order,want',[
    (('a','b','c'),{'a':180,'b':180,'c':180}),
    (('c','b','a'),{'a':0,'b':0,'c':0}),
])
def test_reversing_chain_reverses_physical_data_flow(order,want):
    assert choose()(order,{'a':(0,0),'b':(20000000,0),'c':(40000000,0)},((-3000000,-1000000),(3000000,1000000)),side='B.Cu')==want


@pytest.mark.parametrize('lead,want',[((-20000000,0),180),((20000000,0),0)])
def test_single_led_uses_its_actual_input_lead_not_the_unused_output(lead,want):
    assert choose()(['one'],{'one':(0,0)},((-3000000,-1000000),(3000000,1000000)),side='B.Cu',lead_nm=lead)=={'one':want}


def independent_cost(order,positions,angles,lead):
    # Hand-defined asymmetric front-side pad positions deliberately differ
    # from Mini-E. This exhaustive oracle does not call production geometry.
    def at(key,rotation,is_input):
        x,y=(-4000000,-2000000) if is_input else (1000000,3000000)
        x=-x  # actual LEFT_RIGHT backside transform
        if rotation==180:x,y=-x,-y
        return positions[key][0]+x,positions[key][1]+y
    value=math.dist(lead,at(order[0],angles[0],True))
    for i in range(len(order)-1):value+=math.dist(at(order[i],angles[i],False),at(order[i+1],angles[i+1],True))
    return value


@pytest.mark.parametrize('reverse,cut',[(False,0),(False,1),(False,3),(True,0),(True,2),(True,4)])
def test_staggered_cycle_cut_uses_global_open_chain_cost(reverse,cut):
    positions={'p':(0,0),'q':(19000000,0),'r':(40000000,0),'s':(35000000,19000000),'t':(14000000,19000000)}
    order=list(positions)
    if reverse:order.reverse()
    order=order[cut:]+order[:cut];lead=(27000000,9000000)
    candidates=itertools.product((0,180),repeat=len(order))
    expected=min(candidates,key=lambda angles:(independent_cost(order,positions,angles,lead),angles))
    actual=choose()(order,positions,((-4000000,-2000000),(1000000,3000000)),side='B.Cu',lead_nm=lead)
    assert tuple(actual[key] for key in order)==expected
    # Renaming/permuting the mapping and translating the geometry cannot
    # change physical pose choices. The cut is not silently closed again.
    renamed={key:'site '+str(i) for i,key in enumerate(reversed(order))};delta=(17000000,-5000000)
    shifted={renamed[k]:tuple(q[i]+delta[i] for i in (0,1)) for k,q in reversed(list(positions.items()))}
    again=choose()([renamed[k] for k in order],shifted,((-4000000,-2000000),(1000000,3000000)),side='B.Cu',lead_nm=tuple(lead[i]+delta[i] for i in (0,1)))
    assert tuple(again[renamed[key]] for key in order)==expected


def test_empty_no_rgb_and_deterministic_single_site_tie():
    assert choose()([],{},((-1,0),(1,0)),side='B.Cu')=={}
    assert choose()(['x'],{'x':(0,0)},((-1,0),(1,0)),side='B.Cu')=={'x':0}
