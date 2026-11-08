"""Controller preparation must honor the proven full-matrix lifecycle."""
from copy import deepcopy
import json
import sys
from types import SimpleNamespace

import pytest
from test_post_matrix_interface_scope import receipt_scene, operation_kernel


def scene(monkeypatch, tmp_path, *, full=True, closed=('row','column'), corrupt=False):
    from smilepcb.keyboard_routing import field_contract
    api, board, cfg, receipt, state = receipt_scene(monkeypatch, closed=closed)
    run, _ = operation_kernel(monkeypatch)
    run.__globals__['p'].LoadBoard = lambda path: board
    run.__globals__['w'] = sys.modules['smilepcb.native_worker']
    board.GetDesignSettings = lambda: SimpleNamespace(m_CopperEdgeClearance=250000, m_MinClearance=150000)
    board.GetTracks = lambda: []
    if corrupt: receipt['controller_frame'][0]['rotation'] = 90
    (tmp_path/'.smilepcb-keyboard.json').write_text(json.dumps(cfg))
    (tmp_path/api.MARKER).write_text(json.dumps(receipt))
    monkeypatch.setattr(field_contract,'load',lambda path: {'scope':'matrix-with-controller' if full else 'key-field'})
    # Native routing is external to this host lifecycle test. The verifier,
    # receipt loader, metadata and physical-graph retirement remain real.
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_ports',SimpleNamespace(route_controller_ports=lambda *a,**k:{'accepted':False}))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.power',SimpleNamespace(seed_controller_returns=lambda *a,**k:None))
    return lambda:run({'operation':'controller_ports','board':str(tmp_path/'keyboard.kicad_pcb')}), state


def test_closed_matrix_can_enter_controller_preparation_after_retired_heads(monkeypatch,tmp_path):
    run,state=scene(monkeypatch,tmp_path);before=deepcopy(state)
    assert run()['ports']['accepted'] is False
    assert state==before


@pytest.mark.parametrize('change',['partial','reopened','corrupt'])
def test_controller_preparation_still_rejects_unproven_retirement(monkeypatch,tmp_path,change):
    run,state=scene(monkeypatch,tmp_path,full=change!='partial',closed=('column',) if change=='reopened' else ('row','column'),corrupt=change=='corrupt')
    before=deepcopy(state)
    with pytest.raises(ValueError):run()
    assert state==before
