"""Execute the actual regenerated source branch and its common postconditions."""
import ast
import builtins
from copy import deepcopy
from types import SimpleNamespace

import pytest

from smilepcb.keyboard_routing.conditional_sources import SourceSearchBudget, geometry_signature
from test_mixed_source_heads import SOURCE, fixture


def run_source(*, reason='No legal outward interface candidates', full_success=False,
               full_unchanged=False, expired=False, corrupt_full=False,coherent_via_banks=False):
    f = fixture()
    env = f.env
    full_calls = []
    def full_then_single(board, **kwargs):
        if kwargs['include_nets'] != {q['net'] for q in f.ports}:
            return f.generate(board, **kwargs)
        full_calls.append(kwargs)
        assert not f.calls
        kwargs['source_budget'].charge_candidates(f.ports[-1]['pad_ids'], 20)
        if expired:
            f.clock.now = kwargs['deadline']
        if corrupt_full:
            board.tracks['unexpected-delta'] = deepcopy(f.heads['old-0'])
            board.physics['unexpected-delta'] = 'unowned'
        if not full_success:
            return {'accepted': False, 'reason': reason}
        new_ports = deepcopy(f.ports)
        for i,q in enumerate(new_ports):
            shape = deepcopy(f.heads[q['added_ids'][0]])
            if not full_unchanged or i != 0:
                shape['end'][0] += 1
            uid = 'full-' + str(i)
            board.tracks[uid] = shape
            board.physics[uid] = 'exact native properties'
            q.update(added_ids=[uid], terminal_id=uid)
        return {'accepted': True, 'ports': new_ports}
    original_import = builtins.__import__
    def importer(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 1 and name == 'controller_ports':
            return SimpleNamespace(route_controller_ports=full_then_single)
        return original_import(name, globals, locals, fromlist, level)
    env.update(__builtins__={**vars(builtins), '__import__': importer},
        __package__='smilepcb.keyboard_routing', option='regenerated',
        SourceSearchBudget=SourceSearchBudget, qs=deepcopy(f.ports), old_heads=deepcopy(f.heads),
        old_signature=geometry_signature(f.heads), selected={q['net'] for q in f.ports},
        feedback=[tuple(q['pad_ids']) for q in reversed(f.ports)],
        check=f.board, board=f.original, stop=25., row={},
        cfg={'parts':[{'ref':'U_MCU','region':'controller'}],'plane_nets':set(),
            **({'routing_preparation':{'coherent_via_banks':True}} if coherent_via_banks else {})},
        exit_bounds_nm=None, temporary=SimpleNamespace(frame=env['snapshot'](f.board)),
        parent=env['snapshot'](f.board),
        _retained=lambda board, frame: all(board.tracks.get(u)==q for u,q in frame['tracks'].items()))
    tree = ast.parse(SOURCE.read_text())
    source_if = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
        and n.body and isinstance(n.body[0], ast.Assign)
        and isinstance(n.body[0].value, ast.Call)
        and getattr(n.body[0].value.func, 'id', None) == '_admit_heads')
    parent_body = next(n.body for n in ast.walk(tree) if isinstance(n, ast.With) and source_if in n.body)
    start = parent_body.index(source_if)
    end = next(i for i in range(start+1, len(parent_body)) if isinstance(parent_body[i], ast.With))
    code = compile(ast.fix_missing_locations(ast.Module(body=parent_body[start:end], type_ignores=[])), str(SOURCE), 'exec')
    error = None
    with f.launches.reserve_launches(f.outer):
        try:
            exec(code, env)
        except (ValueError, TimeoutError) as caught:
            error = caught
    return f, env, full_calls, error


def test_full_first_then_mixed_uses_same_absolute_end_and_same_budget():
    f, env, full, error = run_source()
    assert error is None, error
    assert len(full) == 1 and len(f.calls) == 2
    assert env['row']['source_strategy'] == 'mixed_single_interface'
    assert env['row']['mixed_head_generation']['trigger_reason'] == 'No legal outward interface candidates'
    assert env['bank_ports'][0] == f.ports[0]
    assert {q['net'] for q in env['bank_ports']} == {q['net'] for q in f.ports}
    assert all(q[1]['deadline'] == full[0]['deadline'] == 10. for q in f.calls)
    assert all(q[1]['source_budget'] is full[0]['source_budget'] for q in f.calls)
    assert full[0]['source_budget'].candidate_limit(f.ports[-1]['pad_ids'], 96) == 76
    assert not env['row'].get('accepted', False)


@pytest.mark.parametrize('reason', ['Port candidate generation budget exhausted',
    'Joint port search budget exhausted', 'unknown source failure'])
def test_timeout_and_unknown_failures_do_not_start_mixed_trials(reason):
    f, env, full, error = run_source(reason=reason)
    assert error is not None and len(full) == 1 and not f.calls
    assert 'mixed_head_generation' not in env['row']


def test_expired_finite_failure_cannot_refresh_source_deadline():
    f, env, full, error = run_source(expired=True)
    assert error is not None and not f.calls and full[0]['deadline'] == 10.


def test_full_success_keeps_full_strategy_without_fallback():
    f, env, full, error = run_source(full_success=True)
    assert error is None
    assert env['row']['source_strategy'] == 'full_regeneration'
    assert not f.calls and 'mixed_head_generation' not in env['row']


def test_full_mode_still_requires_every_failed_interface_to_change():
    f, env, full, error = run_source(full_success=True, full_unchanged=True)
    assert error is not None and 'did not change geometry' in str(error)
    assert not f.calls


def test_failed_full_generator_must_restore_exact_frame_before_fallback():
    f, env, full, error = run_source(corrupt_full=True)
    assert error is not None and not f.calls


def test_requested_preference_reaches_full_and_mixed_regeneration_without_new_budget():
    f,env,full,error=run_source(coherent_via_banks=True)
    assert error is None and full[0]['coherent_via_banks'] is True
    assert f.calls and all(kwargs['coherent_via_banks'] is True for _,kwargs in f.calls)
    assert all(kwargs['deadline']==full[0]['deadline'] and kwargs['source_budget'] is full[0]['source_budget'] for _,kwargs in f.calls)


def test_default_regeneration_arguments_do_not_add_policy_key():
    f,env,full,error=run_source()
    assert error is None and 'coherent_via_banks' not in full[0]
    assert all('coherent_via_banks' not in kwargs for _,kwargs in f.calls)
