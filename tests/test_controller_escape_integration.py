"""Host orchestration controls; native escape/contact proof is tested separately.

Execute the real caller bodies with controlled time and phase outcomes. A mocked
escape success always meets an explicitly rejected downstream gate here: none of
these controls certifies physical routing or a complete matrix.
"""
import ast
from collections import defaultdict
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import traceback

import pytest


ROOT = Path(__file__).resolve().parents[1]


def body(module, function, namespace):
    path = ROOT / 'smilepcb' / 'keyboard_routing' / (module + '.py')
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    namespace.update(__name__='smilepcb.keyboard_routing.' + module,
                     __package__='smilepcb.keyboard_routing')
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[function]


def module_stub(monkeypatch, name, **exports):
    full = 'smilepcb.keyboard_routing.' + name
    module = ModuleType(full)
    module.__dict__.update(exports)
    monkeypatch.setitem(sys.modules, full, module)


class Board:
    def GetTracks(self):
        return []

    def GetFootprints(self):
        return []

    def BuildConnectivity(self):
        pass

    def GetDesignSettings(self):
        return SimpleNamespace(m_TrackMinWidth=150000, m_MinClearance=150000)


def snapshot(board):
    return {'tracks': {}, 'footprints': {}}


def collector_case(monkeypatch, *, remaining=200, stock_elapsed=35,
                   stock_accepted=False, profile='dsn-process-v2',coherent_via_banks=False):
    clock = SimpleNamespace(now=1000.0)
    clock.monotonic = lambda: clock.now
    calls = {'stock': [], 'fallback': [], 'downstream': []}
    caller_deadline = clock.now + remaining

    def stock(board, **kwargs):
        calls['stock'].append((clock.now, kwargs))
        clock.now = min(kwargs['deadline'], clock.now + stock_elapsed)
        return {'accepted': stock_accepted, 'ports': [], 'added_ids': [],
                'reason': 'Controlled stock rejection'}

    def fallback(board, **kwargs):
        calls['fallback'].append((clock.now, kwargs))
        # Only the caller's handoff is under test; this is not a native receipt.
        return {'accepted': True, 'ports': [], 'added_ids': []}

    def downstream(*args, **kwargs):
        calls['downstream'].append(clock.now)
        raise ValueError('Controlled downstream proof rejection')

    module_stub(monkeypatch, 'generation', current_generation_profile=lambda: SimpleNamespace(name=profile))
    module_stub(monkeypatch, 'controller_escape', route_controller_escapes=fallback)
    ns = dict(time=clock, snapshot=snapshot, transient_access_ids=lambda *a: set(),
              reserve_launches=lambda *a: nullcontext(), route_controller_ports=stock,
              route_controller_escapes=fallback, ordered_nets=lambda cfg: [('rows', ['ROW']), ('columns', ['COL'])],
              collector_assignment=downstream, Path=Path, traceback=traceback,
              deepcopy=deepcopy)
    fn = body('collector_preparation', 'prepare_collector_matrix', ns)
    cfg = {'plane_nets': ['GND']}
    original = deepcopy(cfg)
    updated, result = fn(Board(), cfg, controller_refs={'U_MCU', 'C_CORE'},
                         nets={'ROW', 'COL'}, targets={}, fixed_ports=[],
                         exit_bounds_nm=(1, 2, 3, 4), deadline=caller_deadline,coherent_via_banks=coherent_via_banks)
    assert updated == original == cfg
    assert not result['candidate_accepted']
    return calls, result, caller_deadline


@pytest.mark.parametrize('remaining, expected_deadline', [(200, 1090), (60, 1048)])
def test_v2_stock_rejection_uses_one_fallback_with_shared_absolute_budget(monkeypatch, remaining, expected_deadline):
    calls, result, caller_deadline = collector_case(monkeypatch, remaining=remaining)
    assert len(calls['fallback']) == 1, calls
    started, kwargs = calls['fallback'][0]
    assert started == 1035
    assert kwargs['deadline'] == expected_deadline
    assert started < kwargs['deadline'] <= caller_deadline - 12
    assert kwargs['deadline'] <= 1000 + 90  # Stock time belongs to this allowance.
    assert kwargs['include_nets'] == {'ROW', 'COL'}
    assert kwargs['controller_refs'] == {'U_MCU', 'C_CORE'}
    assert kwargs['mcu_refs'] == {'U_MCU'}
    assert kwargs['clearance_nm'] >= 173000
    assert calls['downstream']  # Mock source success must reach the rejecting gate.
    assert any('Controlled downstream proof rejection' in t.get('reason', '') for t in result['trials'])
    assert all(kw['deadline'] <= min(caller_deadline - 12, 1000 + 90)
               and kw['deadline'] - start <= 35
               for start, kw in calls['stock'])


@pytest.mark.parametrize('remaining', [0, 1, 19, 20, 25])
def test_tiny_source_tail_cannot_replenish_time_or_claim_success(monkeypatch, remaining):
    calls, result, caller_deadline = collector_case(monkeypatch, remaining=remaining)
    assert not calls['fallback'] and not calls['downstream'], calls
    assert not result['candidate_accepted']
    assert all(start < kw['deadline'] <= caller_deadline - 12 for start, kw in calls['stock'])


@pytest.mark.parametrize('profile', ['dsn-safe', 'native'])
def test_legacy_profiles_do_not_invoke_v2_fallback(monkeypatch, profile):
    calls, _, _ = collector_case(monkeypatch, profile=profile)
    assert calls['stock'] and not calls['fallback']


def test_stock_success_does_not_invoke_fallback_or_bypass_downstream_proof(monkeypatch):
    calls, result, _ = collector_case(monkeypatch, stock_accepted=True, stock_elapsed=0)
    assert calls['stock'] and calls['downstream'] and not calls['fallback']
    assert not result['candidate_accepted']


def test_same_face_preflight_misses_stay_uncertain_in_electrical_allocation(monkeypatch):
    seen = []
    accepted = lambda *a, **kw: {'accepted': True, 'ports': [], 'added_ids': []}
    ownership = lambda *a, **kw: SimpleNamespace(generation_profile='dsn-process-v2', tracks={}, pads={})
    module_stub(monkeypatch, 'generation', current_generation_profile=lambda: SimpleNamespace(name='dsn-process-v2'))
    module_stub(monkeypatch, 'modules', seed_key_data_access=accepted)
    module_stub(monkeypatch, 'controller_local', prepare_controller_local=accepted)
    module_stub(monkeypatch, 'collector_preparation', verified_provisional_ownership=ownership,
                ProvisionalOwnership=lambda *a: ownership())
    module_stub(monkeypatch, 'interface_launches', reserve_launches=lambda *a: nullcontext())

    def allocate(board, cfg, **kwargs):
        seen.append(kwargs)
        raise ValueError('Stop after allocation handoff')

    preflight = {'excluded_gpios': ['F0', 'C15'], 'uncertain_gpios': ['C14'],
                 'certified_gpios': ['A0'], 'scope': 'Individual same-face proposals only'}
    fn = body('ordered_controller', 'prepare_ordered_controller', dict(
        time=SimpleNamespace(monotonic=lambda: 1000), snapshot=snapshot,
        seed_controller_returns=accepted, route_controller_ports=accepted,
        ordered_nets=lambda cfg: [('rows', ['ROW']), ('columns', ['COL'])],
        mcu_signal_nets=lambda cfg: {'ROW', 'COL', 'FIXED'},
        probe_gpio_access=lambda *a, **kw: deepcopy(preflight),
        apply_matrix_gpio=allocate, defaultdict=defaultdict))
    cfg = {'parts': [{'ref': 'U_MCU', 'region': 'controller'}], 'plane_nets': ['GND']}
    _, result = fn(Board(), cfg, deadline=1200, complete_matrix=True)
    assert len(seen) == 1
    assert not seen[0]['excluded_gpios']
    assert set(seen[0]['uncertain_gpios']) == {'F0', 'C15', 'C14'}
    assert set(seen[0]['unwitnessed_gpios']) == {'F0', 'C15'}
    assert not result['accepted']



def test_native_gpio_adapter_forwards_miss_confidence_separately():
    class StopAfterAssignment(Exception):pass
    seen=[]
    def assign(cfg,locations,targets,**kwargs):
        seen.append(kwargs)
        raise StopAfterAssignment
    def pad(number,net):
        return SimpleNamespace(GetNumber=lambda:number,GetNetname=lambda:net,
                               GetPosition=lambda:SimpleNamespace(x=10,y=20))
    chip=SimpleNamespace(GetReference=lambda:'U_MCU',Pads=lambda:[pad('1','ROW'),pad('2','COL')])
    target=SimpleNamespace(GetReference=lambda:'target',Pads=lambda:[pad('1','ROW'),pad('2','COL')])
    board=SimpleNamespace(GetTracks=lambda:[],GetFootprints=lambda:[chip,target])
    cfg={'parts':[{'ref':'U_MCU','nets':{'1':'ROW','2':'COL'}}],
         'mcu_gpio_pads':{'A0':'1','A1':'2'},'layout':{'gpio':{'rows':['A0'],'cols':['A1']}}}
    fn=body('gpio','apply_matrix_gpio',dict(assign_matrix_gpio=assign,defaultdict=defaultdict,
                                           ROW_PINS=(),COL_PINS=('A0','A1')))
    with pytest.raises(StopAfterAssignment):
        fn(board,cfg,uncertain_gpios=['A0','A1'],unwitnessed_gpios=['A0'])
    assert len(seen)==1 and seen[0]['excluded_gpios']==()
    assert seen[0]['uncertain_gpios']==['A0','A1']
    assert seen[0]['unwitnessed_gpios']==['A0']
