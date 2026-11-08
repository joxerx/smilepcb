"""Owned callback scenes exercise the bounded selector without Native."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from smilepcb.stm32 import core_pose_selection as selection


CHECKS = ('assembly', 'mechanics', 'local_pads', 'returns', 'usb', 'context', 'probe_cleanup')
KEYS = [['U:1'], ['U:2']]


class Resource:
    def __deepcopy__(self, memo):
        raise AssertionError('Owned scenes must not be deepcopied')


def pose(index):
    return dict(origin_nm=[index*100, 0], rotation_deg=0, pose_key=str(index))


def scene(p):
    return dict(pose=deepcopy(p), resource=Resource(), expected_source_keys=deepcopy(KEYS), native_source_manifest=dict(
        mandatory_pad_keys=['U:1', 'U:2'], pads=[dict(pad_key='U:1', pad_id='native-1'), dict(pad_key='U:2', pad_id='native-2')],
        roots=[dict(source_key=['U:1'], pad_ids=['native-1']), dict(source_key=['U:2'], pad_ids=['native-2'])]))


def proof(slack=100):
    return dict(schema='native-core-local-v1', mandatory_source_keys=deepcopy(KEYS), proved_source_keys=deepcopy(KEYS),
        checks={name: True for name in CHECKS}, worst_bank_deficit=0, worst_bank_slack_nm=slack, via_count=2, length_nm=1000)


def run(monkeypatch, proofs, *, assemble_cost=1, evaluate_cost=1, deadline=30, max_candidates=3, mutate_scene=None):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(selection.time, 'monotonic', lambda: clock.now)
    events, alive, assembled = [], set(), []
    def assemble(p, *, deadline):
        events.append(('assemble', p['pose_key'], deadline, clock.now))
        clock.now += assemble_cost
        s = scene(p)
        if mutate_scene:
            mutate_scene(s)
        alive.add(id(s)); assembled.append(s)
        assert len(alive) <= 2
        return s
    def evaluate(s, *, deadline):
        assert s in assembled
        events.append(('evaluate', s['pose']['pose_key'], deadline, clock.now))
        clock.now += evaluate_cost
        value = proofs[int(s['pose']['pose_key'])]
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value(s, clock)
        return deepcopy(value)
    def dispose(s):
        events.append(('dispose', s['pose']['pose_key']))
        alive.remove(id(s))
    result = selection.select_core_pose([pose(i) for i in range(len(proofs))], assemble=assemble, evaluate=evaluate,
        dispose=dispose, deadline=deadline, max_candidates=max_candidates)
    return result, events, assembled, alive


def test_zero_unproved_root_slack_improvement_selects_the_actual_owned_scene(monkeypatch):
    result, events, scenes, alive = run(monkeypatch, [proof(100), proof(200), proof(150)])
    assert result['selected_scene'] is scenes[1]
    assert result['selected_pose']['origin_nm'] == [100, 0]
    assert result['improved'] is True
    assert result['reason'] == 'candidate_strictly_improves_baseline'
    assert alive == {id(scenes[1])}
    assert events.index(('dispose', '0')) > next(i for i, e in enumerate(events) if e[:2] == ('evaluate', '1'))


def test_native_metric_tie_keeps_baseline_and_disposes_candidate(monkeypatch):
    result, _, scenes, alive = run(monkeypatch, [proof(), proof()])
    assert result['selected_scene'] is scenes[0] and result['improved'] is False
    assert result['reason'] == 'baseline_retained_no_improvement'
    assert alive == {id(scenes[0])}


@pytest.mark.parametrize('check', CHECKS)
def test_every_literal_hard_gate_is_mandatory(monkeypatch, check):
    bad = proof(); bad['checks'][check] = False
    result, _, _, alive = run(monkeypatch, [bad])
    assert result['selected_scene'] is None and result['selected_pose'] is None
    assert result['improved'] is False and not alive


@pytest.mark.parametrize('change', [
    lambda p: p.update(schema='estimated-only'),
    lambda p: p['checks'].update(usb=1),
    lambda p: p['checks'].pop('context'),
    lambda p: p.update(proved_source_keys=[['U:1']]),
    lambda p: p.update(mandatory_source_keys=[['U:1']], proved_source_keys=[['U:1']]),
    lambda p: p.update(worst_bank_slack_nm=float('nan')),
    lambda p: p.update(via_count=True),
    lambda p: p.update(length_nm=-1),
])
def test_estimates_partial_roots_nonliteral_checks_and_invalid_metrics_are_ineligible(monkeypatch, change):
    bad = proof(); change(bad)
    result, _, _, alive = run(monkeypatch, [bad])
    assert result['selected_scene'] is None and not alive


@pytest.mark.parametrize('change', [
    lambda s: s['native_source_manifest']['pads'][1].update(pad_id='native-1'),
    lambda s: s['native_source_manifest']['roots'].pop(),
    lambda s: s['native_source_manifest']['mandatory_pad_keys'].append('U:3'),
    lambda s: s.update(expected_source_keys=[['U:1']]),
])
def test_manifest_bijection_partition_and_expected_root_inventory_are_checked_before_evaluation(monkeypatch, change):
    result, events, _, alive = run(monkeypatch, [proof()], mutate_scene=change)
    assert result['selected_scene'] is None and not alive
    assert not any(e[0] == 'evaluate' for e in events)


def test_failed_baseline_can_be_replaced_only_by_a_complete_admitted_candidate(monkeypatch):
    baseline = proof(); baseline['checks']['usb'] = False
    result, _, scenes, alive = run(monkeypatch, [baseline, proof(50)])
    assert result['selected_scene'] is scenes[1] and alive == {id(scenes[1])}
    assert result['reason'] == 'candidate_admitted_baseline_unproved'
    assert result['attempts'][0]['comparison_key'] is None


def test_one_allowance_is_fixed_for_assembly_and_evaluation_without_replenishment(monkeypatch):
    result, events, _, _ = run(monkeypatch, [proof(), RuntimeError('rejected'), proof()], assemble_cost=1, evaluate_cost=1)
    starts = [e for e in events if e[0] == 'assemble']
    evaluations = [e for e in events if e[0] == 'evaluate']
    assert [e[2]-e[3] for e in starts] == [10, 10, 10]
    assert [e[2] for e in evaluations] == [e[2] for e in starts]
    assert all(e[2] <= 30 for e in evaluations)
    assert result['selected_scene'] is not None


def test_later_expiry_disposes_active_scene_and_retains_incumbent(monkeypatch):
    def expire(s, clock):
        clock.now = 40
        return proof(999)
    result, _, scenes, alive = run(monkeypatch, [proof(), expire, proof(1000)])
    assert result['selected_scene'] is scenes[0] and alive == {id(scenes[0])}
    assert len(result['attempts']) == 2


def test_explicit_timeout_keeps_baseline_and_three_trial_cap(monkeypatch):
    result, events, scenes, alive = run(monkeypatch, [proof(), TimeoutError('expired'), proof(), proof(999)])
    assert len(result['attempts']) == 3
    assert result['selected_scene'] is scenes[0] and alive == {id(scenes[0])}
    assert [e[1] for e in events if e[0] == 'assemble'] == ['0', '1', '2']


def test_initially_expired_deadline_runs_no_callbacks(monkeypatch):
    monkeypatch.setattr(selection.time, 'monotonic', lambda: 30)
    def forbidden(*args, **kwargs):
        raise AssertionError('Expired selector must not call a callback')
    result = selection.select_core_pose([pose(0)], assemble=forbidden, evaluate=forbidden, dispose=forbidden, deadline=30)
    assert result['selected_scene'] is None and result['attempts'] == []


@pytest.mark.parametrize('change', [
    lambda s: s.update(expected_source_keys=[['U:1']]),
    lambda s: s['native_source_manifest']['pads'][0].update(pad_id='changed-native-pad'),
    lambda s: s['pose'].update(rotation_deg=90),
])
def test_evaluation_cannot_change_initial_manifest_obligations_native_ids_or_pose(monkeypatch, change):
    def mutate(s, clock):
        change(s)
        return proof()
    result, _, _, alive = run(monkeypatch, [mutate])
    assert result['selected_scene'] is None and not alive


def test_proof_validation_is_inside_the_same_trial_cutoff(monkeypatch):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(selection.time, 'monotonic', lambda: clock.now)
    original = selection._proof
    def slow_validation(value, expected):
        result = original(value, expected)
        clock.now = 11
        return result
    monkeypatch.setattr(selection, '_proof', slow_validation)
    disposed = []
    result = selection.select_core_pose([pose(0)], assemble=lambda p, **kw: scene(p),
        evaluate=lambda s, **kw: proof(), dispose=disposed.append, deadline=10)
    assert result['selected_scene'] is None and len(disposed) == 1


def balanced_proof(spaces, *, headroom=0, adverse=0):
    value=proof(); value['checks']['four_side_metrics']=True
    value.update(side_space_nm=dict(zip(('left','right','top','bottom'),spaces)),
                 bank_headroom_nm=headroom,adverse_departures=adverse)
    return value


def test_validated_assembled_four_side_leximin_precedes_old_local_metrics(monkeypatch):
    base=balanced_proof((5,5,10,90));base['worst_bank_slack_nm']=999
    better=balanced_proof((5,5,50,50));better['worst_bank_slack_nm']=0
    result,_,scenes,alive=run(monkeypatch,[base,better])
    assert result['selected_scene'] is scenes[1] and result['improved']
    assert alive=={id(scenes[1])}


def test_four_side_headroom_then_target_direction_break_balanced_space_ties(monkeypatch):
    result,_,scenes,_=run(monkeypatch,[balanced_proof((10,20,30,40),headroom=-5),
        balanced_proof((40,30,20,10),headroom=0,adverse=2),balanced_proof((10,20,30,40),headroom=0,adverse=1)])
    assert result['selected_scene'] is scenes[2]


@pytest.mark.parametrize('invalid', ['unchecked','usb','negative','legacy_baseline'])
def test_balanced_metrics_never_bypass_native_gates_or_mix_unmeasured_baseline(monkeypatch,invalid):
    baseline=balanced_proof((10,20,30,40));candidate=balanced_proof((100,100,100,100))
    candidate['worst_bank_slack_nm']=1000
    if invalid=='unchecked': candidate['checks'].pop('four_side_metrics')
    elif invalid=='usb': candidate['checks']['usb']=False
    elif invalid=='negative': candidate['side_space_nm']['left']=-1
    else: baseline=proof()
    result,_,scenes,alive=run(monkeypatch,[baseline,candidate])
    assert result['selected_scene'] is scenes[0] and not result['improved']
    assert alive=={id(scenes[0])}


def test_legacy_headroom_diagnostic_alone_does_not_opt_in_to_balanced_metrics(monkeypatch):
    legacy=proof();legacy['bank_headroom_nm']=-5
    result,_,scenes,alive=run(monkeypatch,[legacy])
    assert result['selected_scene'] is scenes[0] and alive=={id(scenes[0])}
