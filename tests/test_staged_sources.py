"""Staged source-head geometry and checkpoint contracts without Native."""
from copy import deepcopy
import time
import pytest
from smilepcb.keyboard_routing.staged_sources import source_head_proposals,admit_source_heads,staged_group


def sources():
    return [dict(net=str(i),source=(0,i*1000),source_layer=2,heading=(1,0),width=100,
        source_forward_extent_nm=900+i*100,source_backward_extent_nm=1500+i*100,
        source_via_copper_margin_nm=100,source_drill_land_margin_nm=80) for i in range(2)]


def propose(rows=None,**kwargs):
    return source_head_proposals(sources() if rows is None else rows,body_face=0,via_width_nm=300,via_drill_nm=200,
        clearance_nm=100,outlet_nm=500,deadline=time.monotonic()+2,**kwargs)


def test_source_only_proposals_need_no_synthetic_field_target_and_keep_stagger():
    answer=propose()
    assert answer and len(answer)<=64
    assert [q['escape_side'] for q in answer[:2]]==['outward','inward']
    assert any(len({r['legs'][-1][1][-1][0] for r in q['routes']})>1 for q in answer)
    for q in answer:
        for row,route in zip(sources(),q['routes']):
            assert route['legs'][0][1][0]==row['source']
            assert route['legs'][-1][0]==0
            assert route['legs'][-1][1][-1][0]-route['legs'][-1][1][0][0]==500
            side=1 if q['escape_side']=='outward' else -1
            extent=row['source_forward_extent_nm'] if side==1 else row['source_backward_extent_nm']
            assert side*(route['vias'][0][0]-row['source'][0])>extent+250


def test_rotated_asymmetric_source_heads_and_actual_dimensions():
    a=sources();b=deepcopy(a)
    for q in b:q.update(source=(-q['source'][1],q['source'][0]),heading=(0,1))
    left,right=propose(a),propose(b)
    assert len(left)==len(right)
    for x,y in zip(left,right):
        for r,s in zip(x['routes'],y['routes']):assert s['vias']==[(-p[1],p[0]) for p in r['vias']]
    wider=source_head_proposals(a,body_face=0,via_width_nm=600,via_drill_nm=500,clearance_nm=100,
        outlet_nm=500,deadline=time.monotonic()+2)
    assert wider[0]['routes'][0]['vias'][0][0]>left[0]['routes'][0]['vias'][0][0]


class World:
    def __init__(self):self.operations=[{'fixed_suffix':True}];self.track_width=100
    def checkpoint(self):return deepcopy(self.operations)
    def restore(self,mark):self.operations=deepcopy(mark)


def test_failed_head_lane_restores_entire_group_and_keeps_original_deadline():
    world=World();deadline=time.monotonic()+2;calls=[]
    def checker(world,route,lane):
        calls.append(lane['net']);world.operations.append({'net':lane['net']})
        return lane['net']=='0',{'reason':'blocked'}
    result=admit_source_heads(world,sources(),body_face=0,via_width_nm=300,via_drill_nm=200,
        clearance_nm=100,outlet_nm=500,deadline=deadline,checker=checker,limit=2)
    assert not result['accepted'] and world.operations==[{'fixed_suffix':True}]
    assert calls==['0','1','0','1']
    assert result['attempts'][0]['net']=='1'
    expired=admit_source_heads(world,sources(),body_face=0,via_width_nm=300,via_drill_nm=200,
        clearance_nm=100,deadline=0,checker=lambda *a:pytest.fail('Expired head admission'))
    assert not expired['accepted']


def accepted_heads():
    world=World()
    def checker(world,route,lane):
        for face,path in route['legs']:world.operations.append(dict(kind='track',net=lane['net'],layer=face,path=path,width=100))
        for position in route['vias']:world.operations.append(dict(kind='via',net=lane['net'],position=position,width=300,drill=200))
        return True,{}
    receipt=admit_source_heads(world,sources(),body_face=0,via_width_nm=300,via_drill_nm=200,
        clearance_nm=100,outlet_nm=500,deadline=time.monotonic()+2,checker=checker)
    return world,receipt


def test_admitted_checkpoint_keeps_heads_while_body_alternatives_roll_back():
    world,receipt=accepted_heads();assert receipt['accepted']
    checkpoint=world.checkpoint();head_count=len(world.operations)
    world.operations.append({'body':'rejected'});world.restore(checkpoint)
    assert len(world.operations)==head_count and head_count>1
    assert set(receipt['operations'])=={'0','1'}


def test_internal_staged_rows_retain_real_outlets_prefix_and_new_vias():
    world,receipt=accepted_heads()
    rows=[dict(net=s['net'],heading=s['heading'],width=100,window=dict(source=s['source'],source_layer=2,
        prefix_legs=[],suffix_legs=[(0,[(10000,i*1000),(11000,i*1000)])])) for i,s in enumerate(sources())]
    original=deepcopy(rows);new,via_ops=staged_group(rows,receipt)
    assert rows==original
    for row,outlet in zip(new,receipt['outlets']):
        assert row['window']['source']==tuple(outlet['exit_nm']) and row['window']['source_layer']==0
        assert row['window']['prefix_legs'] and via_ops[row['net']][0]['kind']=='via'
        assert not via_ops[row['net']][0].get('fixed')
        assert 'source_prefix_witness' not in row
    with pytest.raises(ValueError):staged_group(rows,dict(accepted=False))


def test_same_face_heads_need_no_redundant_via_or_inward_turn():
    rows=sources()
    for row in rows:row['source_layer']=0
    answer=propose(rows)
    assert answer and all(q['escape_side']=='outward' for q in answer)
    assert all(not route['vias'] and len(route['legs'])==1 for q in answer for route in q['routes'])
    with pytest.raises(ValueError):propose(limit=65)


def test_expiring_original_deadline_rolls_back_a_partially_admitted_group(monkeypatch):
    from smilepcb.keyboard_routing import staged_sources
    clock=[100.];world=World();calls=[]
    monkeypatch.setattr(staged_sources.time,'monotonic',lambda:clock[0])
    def checker(world,route,lane):
        calls.append(lane['net']);world.operations.append({'net':lane['net']});clock[0]=102.;return True,{}
    result=admit_source_heads(world,sources(),body_face=0,via_width_nm=300,via_drill_nm=200,
        clearance_nm=100,outlet_nm=500,deadline=101.,checker=checker)
    assert not result['accepted'] and result['reason']=='source-head-deadline' and calls==['0']
    assert world.operations==[{'fixed_suffix':True}]
