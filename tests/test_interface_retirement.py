"""Retiring a reservation must remove only dispensable completed-net tails."""
import json,os,subprocess
from pathlib import Path
import pytest
import sexpdata


def test_completed_tail_is_retired_without_losing_pending_escape_or_two_face_join(tmp_path):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native cleanup regression')
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_interface_retirement_probe.py')),str(tmp_path)],cwd=root,env={**os.environ,'PYTHONPATH':os.pathsep.join((str(root),str(Path(sexpdata.__file__).parent)))},capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stdout+'\n'+run.stderr
    report=json.loads((tmp_path/'report.json').read_text())
    assert report=={'completed_tail_removed':True,'pending_escape_unchanged':True,
                   'necessary_two_face_via_unchanged':True,'pad_partition_unchanged':True,
                   'remaining_gaps':{'WAITING':1},'source_unchanged':True}


def test_live_pending_interfaces_are_selected_for_indexed_and_inline_net_names(tmp_path):
    from smilepcb import keyboard_recovery as recovery
    select=getattr(recovery,'pending_interface_ids',None)
    assert callable(select),'Pending-interface selection is required'
    board=tmp_path/'keyboard.kicad_pcb'
    board.write_text('(kicad_pcb (net 1 "READY") (net 2 "WAITING") (segment (net 1) (uuid ready)) (via (net 2) (uuid waiting)) (arc (net "WAITING") (uuid inline)) (segment (net 2) (uuid unowned)))')
    assert select(board,['ready','waiting','inline','retired'],{'WAITING'})==['inline','waiting']


def test_duplicate_actual_copper_identity_fails_closed(tmp_path):
    from smilepcb import keyboard_recovery as recovery
    select=getattr(recovery,'pending_interface_ids',None)
    assert callable(select),'Pending-interface selection is required'
    board=tmp_path/'keyboard.kicad_pcb'
    board.write_text('(kicad_pcb (segment (net "READY") (uuid duplicate)) (via (net "WAITING") (uuid duplicate)))')
    with pytest.raises(ValueError,match='Duplicate'):
        select(board,['duplicate'],{'WAITING'})
