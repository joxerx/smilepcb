"""Pure directional placement estimates; no Native imports or proof claims."""
from copy import deepcopy
import json
import pytest
from smilepcb.stm32.core_pose_geometry import shortlist_core_poses


PROCESS = dict(track_width_nm=100, clearance_nm=20, via_width_nm=100)
BANK = ['chip', 0, 1]


def pose(key, x=0, y=0, rotation=0):
    return dict(pose_key=key, origin_nm=[x, y], rotation_deg=rotation)


def roots(owner='arbitrary'):
    return [dict(source_key=[f'U:{i+1}'], net=owner,
                 departures=[dict(point_nm=[0, i*50], heading=[1, 0], layer=2, bank=BANK)]) for i in range(2)]


def windows(width, depth=100):
    return [dict(bank=BANK, layer=2, depth_nm=depth, intervals_nm=[[0, width]])]


def rank(candidates, channels, *, sources=None, targets=None, process=None, limit=6, baseline=None, preferred=(0, 0), supports=None):
    return shortlist_core_poses(candidates, sources if sources is not None else roots(),
        targets if targets is not None else {'arbitrary': [dict(point=[10000, 0], layers=[2], pad_id='target')]},
        channels, supports if supports is not None else {p['pose_key']: [dict(ref='support', domains=[{'candidate': 1}])] for p in candidates},
        process=process or PROCESS, preferred_nm=preferred, baseline=baseline or candidates[0], limit=limit)


def test_two_same_net_roots_count_twice_and_worst_passage_beats_free_area():
    base, area, better = pose('base'), pose('area', 100), pose('better', 200)
    area['free_area_nm2'] = 1000000000
    result = rank([base, area, better], {'base': windows(200), 'area': windows(200), 'better': windows(380)})
    assert [p['pose_key'] for p in result] == ['base', 'better', 'area']
    assert result[0]['diagnostics']['root_count'] == 2
    assert result[0]['diagnostics']['worst_bank_deficit'] == 1
    assert result[1]['diagnostics']['worst_bank_deficit'] == 0
    assert result[1]['diagnostics']['estimated'] is True
    assert json.loads(json.dumps(result))[1]['pose_key'] == 'better'


def test_each_depth_is_a_bottleneck_and_disjoint_intervals_are_not_merged_across_a_blocker():
    base, narrow, separated = pose('base'), pose('narrow', 100), pose('separated', 200)
    channels = {'base': windows(380), 'narrow': windows(1000)+windows(200, 200),
                'separated': [dict(bank=BANK, layer=2, depth_nm=100, intervals_nm=[[0, 150], [250, 400]])]}
    result = rank([base, narrow, separated], channels)
    diag = {p['pose_key']: p['diagnostics'] for p in result}
    assert diag['narrow']['worst_bank_deficit'] == 1
    assert diag['separated']['worst_bank_deficit'] == 0
    assert diag['separated']['banks'][0]['capacity'] == 2


def test_actual_target_direction_and_cardinal_pose_precede_preferred_distance():
    base, closer_wrong, farther_right = pose('base'), pose('turned', 100, rotation=90), pose('forward', 900)
    result = rank([base, closer_wrong, farther_right], {k: windows(380) for k in ('base', 'turned', 'forward')}, preferred=(100, 0))
    assert [p['pose_key'] for p in result] == ['base', 'forward', 'turned']
    assert result[1]['diagnostics']['adverse_departures'] == 0
    assert result[2]['diagnostics']['adverse_departures'] == 2


@pytest.mark.parametrize('process', [dict(track_width_nm=200, clearance_nm=20, via_width_nm=100),
    dict(track_width_nm=100, clearance_nm=60, via_width_nm=100),
    dict(track_width_nm=100, clearance_nm=20, via_width_nm=300),
    dict(track_width_nm=100, clearance_nm=20, via_width_nm=100, via_envelope_radius_nm=170)])
def test_actual_process_dimensions_cannot_increase_channel_capacity(process):
    base = pose('base')
    original = rank([base], {'base': windows(380)})[0]['diagnostics']['banks'][0]['capacity']
    constrained = rank([base], {'base': windows(380)}, process=process)[0]['diagnostics']['banks'][0]['capacity']
    assert constrained < original


def test_geometry_ranking_is_translation_and_electrical_renaming_invariant():
    candidates = [pose('base'), pose('good', 100), pose('bad', 200, rotation=90)]
    channel = {p['pose_key']: windows(380) for p in candidates}
    before = deepcopy((candidates, channel))
    original = rank(candidates, channel)
    moved = deepcopy(candidates)
    for p in moved:
        p['origin_nm'] = [v+1 for v in p['origin_nm']]
    translated = rank(moved, channel, sources=roots('renamed'), targets={'renamed': [dict(point=[10001, 1], layers=[2], pad_id='target')]}, preferred=(1, 1))
    assert [p['pose_key'] for p in original] == [p['pose_key'] for p in translated]
    assert [p['diagnostics']['score'] for p in original] == [p['diagnostics']['score'] for p in translated]
    assert before == (candidates, channel)


def test_baseline_cannot_be_filtered_and_shortlist_has_cardinal_position_diversity():
    baseline = pose('baseline')
    candidates = [pose(f'p{i}', i*100, rotation=0) for i in range(1, 8)]+[pose('turned', 800, rotation=90)]
    result = rank(candidates, {p['pose_key']: windows(380) for p in [baseline, *candidates]}, baseline=baseline, limit=3)
    assert result[0]['pose_key'] == 'baseline'
    assert len(result) == 3 and {p['rotation_deg'] for p in result} == {0, 90}
    assert len({tuple(p['origin_nm']) for p in result}) == 3


def test_empty_support_domain_loses_before_preferred_distance():
    base, blocked, available = pose('base'), pose('blocked', 100), pose('available', 200)
    supports = {'base': [], 'blocked': [dict(ref='USB', domains=[])], 'available': [dict(ref='USB', domains=[{'id': 1}])]}
    result = rank([base, blocked, available], {k: windows(380) for k in supports}, supports=supports, preferred=(100, 0))
    assert [p['pose_key'] for p in result] == ['base', 'available', 'blocked']


@pytest.mark.parametrize('limit', [0, 7, True])
def test_shortlist_limit_is_one_to_six_including_baseline(limit):
    with pytest.raises(ValueError):
        rank([pose('base')], {'base': windows(380)}, limit=limit)


def test_overlapping_source_obligations_and_overlapping_channel_intervals_are_rejected():
    sources = roots()
    sources[1]['source_key'] = sources[0]['source_key']
    with pytest.raises(ValueError):
        rank([pose('base')], {'base': windows(380)}, sources=sources)
    with pytest.raises(ValueError):
        rank([pose('base')], {'base': [dict(bank=BANK, layer=2, depth_nm=100, intervals_nm=[[0, 300], [200, 500]])]})


# Whole-board balanced-space amendment. Oracles below are deliberately pure.
from smilepcb.stm32 import core_pose_geometry as geometry


def balanced_oracle(spaces, *, legal=True, headroom=0, adverse=0):
    return dict(legal=legal, side_space_nm=dict(zip(('left','right','top','bottom'), spaces)),
                bank_headroom_nm=headroom, adverse_departures=adverse)


def search(oracle, *, bounds=(0, 0, 100, 100), baseline=None, coarse=50, fine=1, budget=500, limit=6):
    return geometry.balanced_pose_search(bounds, baseline or pose('baseline', 10, 10),
        coarse_step_nm=coarse, min_step_nm=fine, oracle=oracle, deadline=geometry.time.monotonic()+10,
        max_evaluations=budget, limit=limit)


def test_board_wide_search_finds_and_refines_a_remote_legal_cardinal_pocket():
    def oracle(p, *, deadline):
        x, y = p['origin_nm']
        legal = x >= 80 and y >= 80 and p['rotation_deg'] == 90
        return balanced_oracle((max(0,x-80),max(0,100-x),max(0,y-80),max(0,100-y)), legal=legal)
    result = search(oracle)
    assert result['poses'][0]['pose_key'] == 'baseline'
    assert result['poses'][0]['diagnostics']['legal'] is False
    assert result['poses'][1]['origin_nm'] == [90, 90]
    assert result['poses'][1]['rotation_deg'] == 90
    assert result['stats']['coverage_complete'] is True
    assert result['stats']['min_step_reached'] is True
    assert result['stats']['global_optimum_proved'] is False
    assert {q['pose']['rotation_deg'] for q in result['samples'] if q['phase']=='coarse'} == {0,90,180,270}


def test_leximin_centres_remaining_sides_on_a_flat_minimum_side_plateau():
    def oracle(p, *, deadline):
        x, _ = p['origin_nm']
        return balanced_oracle((x,100-x,5,5))
    result = search(oracle, bounds=(0,0,100,20), baseline=pose('baseline',10), coarse=40, fine=5, budget=800)
    assert result['poses'][1]['origin_nm'][0] == 50
    assert sorted(result['poses'][1]['diagnostics']['side_space_nm'].values()) == [5,5,50,50]


def test_shortlist_preserves_distinct_xy_pockets_not_only_rotations():
    def oracle(p, *, deadline):
        x,y=p['origin_nm']; legal=(x<=20 or x>=80) and 40<=y<=60
        a=x if x<=20 else x-80
        return balanced_oracle((max(0,a),max(0,20-a),max(0,y-40),max(0,60-y)),legal=legal)
    result=search(oracle,baseline=pose('baseline',10,50),coarse=25,fine=1,budget=600,limit=3)
    xs=[p['origin_nm'][0] for p in result['poses']]
    assert min(xs)<=20 and max(xs)>=80
    assert len({tuple(p['origin_nm']) for p in result['poses']})>=2


def test_search_respects_fixed_bounds_budget_and_reports_partial_coverage():
    seen=[]
    def oracle(p, *, deadline):
        seen.append(deepcopy(p)); x,y=p['origin_nm']
        assert -10<=x<=90 and 20<=y<=120
        return balanced_oracle((x+10,90-x,y-20,120-y))
    result=search(oracle,bounds=(-10,20,90,120),baseline=pose('baseline',0,30),coarse=5,fine=1,budget=17)
    assert len(seen)==result['stats']['evaluations']<=17
    assert result['stats']['coverage_complete'] is False and result['stats']['truncated'] is True
    assert result['stats']['global_optimum_proved'] is False
    assert max(p['origin_nm'][0] for p in seen)==90
    assert max(p['origin_nm'][1] for p in seen)==120


def test_balanced_search_is_deterministic_and_translation_invariant():
    def oracle(p, *, deadline):
        x,y=p['origin_nm']; return balanced_oracle((x,100-x,y,100-y),headroom=x-20)
    first=search(oracle,coarse=50,fine=5,budget=300)
    again=search(oracle,coarse=50,fine=5,budget=300)
    def shifted(p, *, deadline):
        q=deepcopy(p);q['origin_nm']=[v-7 for v in q['origin_nm']];return oracle(q,deadline=deadline)
    moved=search(shifted,bounds=(7,7,107,107),baseline=pose('baseline',17,17),coarse=50,fine=5,budget=300)
    assert first['samples']==again['samples']
    assert [(p['origin_nm'],p['rotation_deg']) for p in first['poses']] == [
        ([v-7 for v in p['origin_nm']],p['rotation_deg']) for p in moved['poses']]


def test_expired_search_keeps_baseline_reference_without_calling_oracle(monkeypatch):
    monkeypatch.setattr(geometry.time,'monotonic',lambda:10)
    def forbidden(*a,**kw): raise AssertionError('expired')
    result=geometry.balanced_pose_search((0,0,100,100),pose('baseline',10,10),coarse_step_nm=50,min_step_nm=1,
        oracle=forbidden,deadline=10,max_evaluations=100)
    assert len(result['poses'])==1 and result['stats']['evaluations']==0
    assert result['stats']['stop_reason']=='deadline'


def test_narrow_distant_pocket_uses_board_lattice_not_old_origin_phase():
    def oracle(p, *, deadline):
        x,y=p['origin_nm']; legal=89<=x<=91 and 89<=y<=91
        return balanced_oracle((max(0,x-89),max(0,91-x),max(0,y-89),max(0,91-y)),legal=legal)
    first=search(oracle,baseline=pose('baseline',10,10),coarse=5,fine=1,budget=2400)
    shifted_old_origin=search(oracle,baseline=pose('baseline',11,11),coarse=5,fine=1,budget=2400)
    for result in (first,shifted_old_origin):
        assert result['poses'][1]['origin_nm']==[90,90]
        assert result['stats']['coverage_complete'] and result['stats']['coarse_planned']==1764
    coarse=lambda r:{(*q['pose']['origin_nm'],q['pose']['rotation_deg']) for q in r['samples'] if q['phase']=='coarse' or q.get('coarse_sample')}
    assert coarse(first)==coarse(shifted_old_origin)


def test_cached_baseline_counts_toward_exact_coarse_lattice_budget():
    def oracle(p, *, deadline): return balanced_oracle((1,1,1,1))
    result=search(oracle,bounds=(0,0,10,10),baseline=pose('baseline',0,0),coarse=10,fine=10,budget=16)
    assert result['stats']['evaluations']==16
    assert result['stats']['coarse_planned']==result['stats']['coarse_evaluated']==16
    assert result['stats']['coverage_complete'] and not result['stats']['truncated']


def test_native_baseline_plus_two_leaders_covers_separate_coarse_components_before_repeats():
    def oracle(p, *, deadline):
        x,y=p['origin_nm']
        left=40<=x<=140 and 25<=y<=75
        right=240<=x<=260 and 25<=y<=75
        # Neighbouring legal XY sites may use DIFFERENT orientations; the
        # coarse component is the union of legal orientations at each site.
        angle=(0 if x<=100 else 90) if left else 270
        legal=(left or right) and p['rotation_deg']==angle
        space=max(0,1000-abs(x-90)-abs(y-50)) if left else max(0,200-abs(x-250)-abs(y-50))
        return balanced_oracle((space,space,space,space),legal=legal)
    result=search(oracle,bounds=(0,0,300,100),baseline=pose('baseline',0,0),coarse=25,fine=5,budget=2000,limit=6)
    leaders=result['poses'][1:3]
    assert len(leaders)==2
    assert leaders[0]['origin_nm'][0]<150
    assert leaders[1]['origin_nm'][0]>=240
    assert result['stats']['coarse_components']==2
    assert len({p['diagnostics']['coarse_component'] for p in leaders})==2
    left_labels={s['coarse_component'] for s in result['samples'] if s['phase']=='coarse' and s['legal'] and s['pose']['origin_nm'][0]<150}
    assert len(left_labels)==1


def test_shortened_boundary_cells_do_not_collapse_distinct_coarse_components():
    def oracle(p, *, deadline):
        return balanced_oracle((10,10,10,10),legal=tuple(p['origin_nm']) in {(80,80),(100,100)})
    result=search(oracle,baseline=pose('baseline',10,10),coarse=40,fine=40,budget=100,limit=3)
    assert result['stats']['coarse_components']==2
    assert {tuple(p['origin_nm']) for p in result['poses'][1:]}=={(80,80),(100,100)}


def test_escape_box_rotates_its_offset_about_the_actual_mcu_origin():
    from smilepcb.stm32.core_pose_geometry import expanded_pose_box
    baseline={'origin_nm':[10,10],'rotation_deg':90}
    pose={'origin_nm':[50,80],'rotation_deg':180}
    assert expanded_pose_box([11,8,15,12],baseline,pose,margin_nm=2)==[46,79,54,87]


def test_spatial_overlap_uses_the_whole_escape_box_including_margin():
    from smilepcb.stm32.core_pose_geometry import expanded_pose_box,boxes_overlap
    base={'origin_nm':[0,0],'rotation_deg':0};other={'origin_nm':[9,0],'rotation_deg':0}
    a=expanded_pose_box([-5,-2,5,2],base,base,margin_nm=1)
    b=expanded_pose_box([-5,-2,5,2],base,other,margin_nm=1)
    assert boxes_overlap(a,b)
    assert not boxes_overlap(a,expanded_pose_box([-5,-2,5,2],base,{'origin_nm':[30,0],'rotation_deg':0},margin_nm=1))
