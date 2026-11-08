"""Early creator output remains editable without releasing copied core copper."""
from copy import deepcopy
from types import SimpleNamespace as S
import json,time
import pytest
from smilepcb.keyboard_routing import controller_recovery as cr
from smilepcb.keyboard_routing.generation import generation_profile
from test_controller_participant_ownership import scene
from test_transient_generated_access import kernel


def recovery_scene(monkeypatch):
    api=kernel();board,cfg,before,proof=scene(monkeypatch,local=True)
    cfg['plane_nets']=['return-plane']
    board.state['footprints']['field']['pads'][0]['net']='other-signal'
    cfg['parts'][2]['nets']['1']='other-signal'
    before['footprints']=deepcopy(board.state['footprints'])
    state=board.state
    selected={d['uuid']:{'ref':f['ref'],'pad':deepcopy(d)} for f in state['footprints'].values() for d in f['pads'] if f['ref']!='LED7'}
    report={'accepted':True,'additive':{'added_ids':['left','right','via']},'attempts':[],'positive_compiled_supply_ids':['seed'],'local_closed':{'nets':{'signal-control':{'pads':selected}}}}
    graph_module=__import__('sys').modules['smilepcb.keyboard_routing.native_graph']
    tools=S(snapshot=lambda b:deepcopy(b.state),graph=graph_module.copper_graph,ordered_nets=lambda c:[('rows',['row']),('columns',['col'])])
    monkeypatch.setitem(__import__('sys').modules,'smilepcb.keyboard_routing.collector_preparation',api)
    return api,tools,board,cfg,before,report


def test_only_fresh_attached_controller_signal_tracks_gain_ownership(monkeypatch):
    api,t,b,cfg,before,report=recovery_scene(monkeypatch)
    # A newly generated supply is still outside local-signal authority.
    b.state['tracks']['new-supply']=dict(b.state['tracks']['left'],net='return-plane')
    with generation_profile('dsn-process-v2'):
        owner=cr._recovered_local_ownership(t,b,cfg,before,report,deadline=time.monotonic()+3)
        assert api.transient_access_ids(b.state,owner,{'row','col'})=={'left','right','via'}
    assert set(owner.pads)=={'controller-pad','controller-load'}
    assert not {'new-supply','imported','seed'}&owner.tracks.keys()


@pytest.mark.parametrize('mutation',['rejected','floating','pad-binding'])
def test_recovery_cannot_issue_ownership_for_unproved_objects(monkeypatch,mutation):
    _,t,b,cfg,before,report=recovery_scene(monkeypatch)
    if mutation=='rejected':report['accepted']=False
    elif mutation=='floating':b.state['tracks']['left']['start']=[5000000,0];b.state['tracks']['left']['end']=[6000000,0]
    else:b.state['footprints']['chip']['pads'][0]['net']='wrong-binding'
    with generation_profile('dsn-process-v2'),pytest.raises(ValueError):
        cr._recovered_local_ownership(t,b,cfg,before,report,deadline=time.monotonic()+3)


def test_serializing_a_recovery_report_does_not_serialize_authority():
    report=cr.ControllerRecovery(accepted=True);report.controller_ownership=object()
    assert json.loads(json.dumps(report))=={'accepted':True}



def test_untraced_fresh_local_object_cannot_gain_authority(monkeypatch):
    _,t,b,cfg,before,report=recovery_scene(monkeypatch)
    b.state['tracks']['unknown-new']=deepcopy(b.state['tracks']['left'])
    with generation_profile('dsn-process-v2'),pytest.raises(ValueError,match='creator trace'):
        cr._recovered_local_ownership(t,b,cfg,before,report,deadline=time.monotonic()+3)


def test_an_existing_controller_local_body_stays_outside_recovery_authority(monkeypatch):
    _,t,b,cfg,before,report=recovery_scene(monkeypatch)
    before['tracks']['left']=deepcopy(b.state['tracks']['left'])
    with generation_profile('dsn-process-v2'):
        owner=cr._recovered_local_ownership(t,b,cfg,before,report,deadline=time.monotonic()+3)
    assert set(owner.tracks)=={'right','via'}
