"""Host controls for phase order and rollback, not native geometry proof."""
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_controller_escape_integration import body, module_stub


class Item:
    def __init__(self, uid, net):
        self.m_Uuid = SimpleNamespace(AsString=lambda: uid)
        self.net = net
    def GetNetname(self): return self.net
    def SetNet(self, net): self.net = net
    def GetNumber(self): return '1'


class Board:
    def __init__(self):
        self.tracks = [Item('retained', 'OLD')]
        self.pad = Item('pad', 'ROW')
        self.fp = SimpleNamespace(GetReference=lambda: 'U_MCU', Pads=lambda: [self.pad])
    def GetTracks(self): return self.tracks
    def GetFootprints(self): return [self.fp]
    def GetDesignSettings(self): return SimpleNamespace(m_TrackMinWidth=150000, m_MinClearance=150000)
    def FindNet(self, name): return name
    def Delete(self, track): self.tracks.remove(track)
    def BuildConnectivity(self): pass


def snapshot(board):
    return {'tracks': {t.m_Uuid.AsString(): {'net': t.net} for t in board.tracks},
            'footprints': {'U_MCU': board.pad.net}}


def run_case(monkeypatch, *, profile='dsn-process-v2', complete=True, failure=None,coherent_via_banks=False,unified=False):
    board = Board(); initial = snapshot(board); events = []; scopes = []
    fixed = [{'net': 'FIXED', 'terminal_id': 'fixed'}]
    cfg = {'parts': [{'ref': 'U_MCU', 'region': 'controller'}], 'plane_nets': ['GND']}
    original = deepcopy(cfg)
    accepted = lambda *a, **kw: {'accepted': True, 'ports': [], 'added_ids': []}
    ownership = lambda *a, **kw: SimpleNamespace(generation_profile=profile, tracks={}, pads={})
    @contextmanager
    def reserve(ports):
        scopes.append(ports)
        try: yield
        finally: scopes.pop()
    def allocate(board, cfg, **kw):
        events.append(('allocate', {})); board.pad.SetNet('COL')
        changed = deepcopy(cfg); changed['allocation_marker'] = True
        return changed, {'accepted': True}
    module_result = {'accepted': failure is None, 'connections': 312, 'marker': 'prelude', 'reason': 'fixture reject'}
    def modules(board, cfg, **kw):
        events.append(('modules', kw))
        assert cfg['allocation_marker']
        assert scopes == ([[]] if unified else [fixed])
        board.tracks.append(Item('module-new', 'GND'))
        board.tracks[0].SetNet('CHANGED')
        if failure == 'exception': raise RuntimeError('fixture module exception')
        return module_result
    def collector(board, cfg, **kw):
        events.append(('collector', kw))
        return cfg, {'candidate_accepted': False, 'reason': 'controlled downstream rejection'}
    def stop_incomplete(*a, **kw):
        events.append(('bundles', kw)); raise ValueError('controlled incomplete stop')
    module_stub(monkeypatch, 'generation', current_generation_profile=lambda: SimpleNamespace(name=profile))
    module_stub(monkeypatch, 'modules', seed_key_data_access=accepted, connect_key_modules=modules)
    module_stub(monkeypatch, 'controller_local', prepare_controller_local=accepted)
    module_stub(monkeypatch, 'collector_preparation', verified_provisional_ownership=ownership,
                ProvisionalOwnership=lambda *a: ownership(), prepare_collector_matrix=collector)
    module_stub(monkeypatch, 'interface_launches', reserve_launches=reserve)
    def unified_sources(board,model,**kw):
        events.append(('unified',kw));assert model['allocation_marker']
        return object(),{'accepted':True,'ports':fixed,'local_closed':{'nets':{}}},{'accepted':True}
    module_stub(monkeypatch,'controller_plan',prepare_unified_sources=unified_sources)
    source_calls=[]
    def sources(*args,**kwargs):
        source_calls.append(kwargs);return {'accepted':True,'ports':fixed,'added_ids':[]}
    fn = body('ordered_controller', 'prepare_ordered_controller', dict(
        time=SimpleNamespace(monotonic=lambda: 1000), snapshot=snapshot,
        seed_controller_returns=accepted,
        route_controller_ports=sources,
        ordered_nets=lambda cfg: [('rows', ['ROW']), ('columns', ['COL'])],
        mcu_signal_nets=lambda cfg: {'ROW', 'COL', 'FIXED'},
        probe_gpio_access=lambda *a, **kw: {'excluded_gpios': [], 'uncertain_gpios': []},
        apply_matrix_gpio=allocate, defaultdict=defaultdict, extend_controller_bundles=stop_incomplete))
    error = None; result = None
    try: _, result = fn(board, cfg, deadline=1200, complete_matrix=complete,coherent_via_banks=coherent_via_banks,**({'unified_controller_plan':True} if unified else {}))
    except RuntimeError as exc: error = exc
    assert snapshot(board) == initial and cfg == original
    if unified:assert not source_calls
    if coherent_via_banks and not unified:
        assert 'coherent_via_banks' not in source_calls[0] # fixed nonmatrix interfaces
        if complete:
            assert next(kw for name,kw in events if name=='collector')['coherent_via_banks'] is True
        else:
            assert source_calls[1]['coherent_via_banks'] is True
    return events, result, error, module_result


def test_v2_complete_modules_follow_allocation_and_precede_collector(monkeypatch):
    events, result, error, module_result = run_case(monkeypatch)
    assert [name for name, _ in events] == ['allocate', 'modules', 'collector']
    assert error is None and not result['accepted']
    assert result['local_modules'] == module_result
    assert events[1][1]['deadline'] <= 1180
    assert events[2][1]['require_existing_modules'] is True


@pytest.mark.parametrize('failure', ['rejection', 'exception'])
def test_module_failure_stops_before_source_and_rolls_back_owned_delta_and_pad_rebinding(monkeypatch, failure):
    events, result, error, _ = run_case(monkeypatch, failure=failure)
    assert [name for name, _ in events] == ['allocate', 'modules']
    if failure == 'exception': assert str(error) == 'fixture module exception'
    else: assert error is None and not result['accepted'] and 'fixture reject' in result['reason']


@pytest.mark.parametrize('profile,complete', [('dsn-safe', True), ('native', True), ('dsn-process-v2', False), ('native', False)])
def test_other_profiles_and_incomplete_mode_do_not_preorder_modules(monkeypatch, profile, complete):
    events, result, error, _ = run_case(monkeypatch, profile=profile, complete=complete)
    assert error is None and not result['accepted']
    assert 'modules' not in [name for name, _ in events]
    assert 'local_modules' not in result
    for name, kw in events:
        if name == 'collector': assert not kw.get('require_existing_modules', False)


@pytest.mark.parametrize('complete',[False,True])
def test_opt_in_only_reaches_matrix_source_paths(monkeypatch,complete):
    run_case(monkeypatch,complete=complete,coherent_via_banks=True)


def test_unified_path_allocates_then_builds_modules_before_one_shared_source_batch(monkeypatch):
    events,result,error,_=run_case(monkeypatch,unified=True)
    assert error is None and not result['accepted']
    assert [name for name,_ in events]==['allocate','modules','unified','collector']
    unified=events[2][1];collector=events[3][1]
    assert unified['matrix_nets']=={'ROW','COL'}
    assert unified['deadline']==1200 and collector['deadline']==1192
    assert collector['preplanned_source'] is not None
    assert collector['fixed_ports'][0]['net']=='FIXED'


@pytest.mark.parametrize('accepted',[True,False])
def test_positive_compiled_power_recovery_precedes_gpio_and_has_outer_rollback(monkeypatch,accepted):
    from smilepcb.keyboard_routing.controller_recovery import ControllerRecovery
    board=Board();initial=snapshot(board);events=[];merges=[]
    early_owner,late_owner,merged_owner=object(),object(),object()
    cfg={'parts':[{'ref':'U_MCU','region':'controller'}],'plane_nets':['GND']}
    def recovery(b,c,**kw):
        events.append(('local-recovery',kw));b.tracks[0].SetNet('TEMP')
        report=ControllerRecovery(accepted=accepted,reason='actual closure rejected',local_closed={'nets':{'LOCAL':{}}})
        report.controller_ownership=early_owner if accepted else None
        return report
    def restore(b,frame,copies):
        events.append(('rollback',{}));b.tracks[0].SetNet('OLD')
    def probe(*a,**kw):events.append(('gpio',{}));return {'excluded_gpios':[],'uncertain_gpios':[]}
    def sources(b,c,**kw):
        events.append(('sources',kw))
        return SimpleNamespace(batch=SimpleNamespace(model=c),controller_ownership=late_owner),{'ports':[],'local_closed':{'nets':{'LOCAL':{}}}},{'accepted':True}
    def merge(state,nets,*owners):merges.append(owners);assert nets=={'ROW','COL'};return merged_owner
    def collector(b,c,**kw):events.append(('collector',kw));return c,{'candidate_accepted':False,'reason':'controlled collector stop'}
    module_stub(monkeypatch,'controller_plan',prepare_unified_sources=sources)
    module_stub(monkeypatch,'modules',seed_key_data_access=lambda *a,**kw:{'accepted':True})
    module_stub(monkeypatch,'collector_preparation',verified_provisional_ownership=lambda *a,**kw:None,prepare_collector_matrix=collector,merge_provisional_ownership=merge)
    module_stub(monkeypatch,'generation',current_generation_profile=lambda:SimpleNamespace(name='dsn-process-v2'))
    module_stub(monkeypatch,'controller_recovery',recover_controller_locals=recovery,restore_tracks=restore)
    module_stub(monkeypatch,'conditional_geometry',copy_native_tracks=lambda b:{'retained':'copy'})
    fn=body('ordered_controller','prepare_ordered_controller',dict(
        time=SimpleNamespace(monotonic=lambda:1000),snapshot=snapshot,
        seed_controller_returns=lambda *a,**k:{'accepted':True},
        ordered_nets=lambda c:[('rows',['ROW']),('columns',['COL'])],
        mcu_signal_nets=lambda c:{'ROW','COL','LOCAL'},probe_gpio_access=probe,
        apply_matrix_gpio=lambda b,c,**kw:(c,{}),defaultdict=defaultdict,deepcopy=deepcopy))
    module_stub(monkeypatch,'interface_launches',reserve_launches=lambda *a:__import__('contextlib').nullcontext())
    _,result=fn(board,cfg,deadline=1200,recovery_deadline=1600,complete_matrix=True,unified_controller_plan=True,
        component_corridors=True,module_power_ids={'retained'})
    assert [q[0] for q in events]==(['local-recovery','gpio','sources','collector','rollback'] if accepted else ['local-recovery','rollback'])
    if accepted:
        assert merges==[(early_owner,late_owner)]
        assert next(kw for name,kw in events if name=='collector')['preplanned_source'].controller_ownership is merged_owner
        assert next(kw for name,kw in events if name=='sources')['deadline']==1200
        assert next(kw for name,kw in events if name=='sources')['corridor_valid_until']==1600
        assert next(kw for name,kw in events if name=='collector')['deadline']==1592
    assert events[0][1]['module_power_ids']=={'retained'} and snapshot(board)==initial
    assert not result['accepted']
