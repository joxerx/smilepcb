"""Existing local vias should prevent duplicated plane-access copper."""
import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def access_report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native access reuse checks')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('native-access-reuse')
 run=subprocess.run([interpreter,str(Path(__file__).with_name('native_plane_access_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=70)
 assert run.returncode==0,run.stdout+'\n'+run.stderr
 return json.loads((out/'report.json').read_text())

def test_later_pass_reuses_local_access_without_overlaying_a_different_width(access_report):
 assert access_report['repeat']=={'first_connected':True,'second_connected':True,'same_via':True,'no_new_ids':True,'all_copper_unchanged':True}

def test_disconnected_same_net_via_is_not_mistaken_for_connected_access(access_report):
 assert access_report['disconnected_same_net']=={'connected':True,'created_new_access':True}

def test_connected_pads_share_one_new_access_via(access_report):
 assert access_report['shared_group']=={'both_connected':True,'one_via':True,'second_has_no_additions':True}

def test_reuse_still_respects_the_requested_local_distance(access_report):
 assert access_report['distant_access']=={'connected':True,'new_local_access':True}


def test_reuse_does_not_bypass_a_stricter_solder_land_margin(access_report):
 assert access_report['stricter_land_margin']=={'connected':True,'new_access':True}


def test_unfilled_planes_cannot_substitute_for_explicit_access(access_report):
 assert access_report['unfilled_plane']=={'default_graph_rejects':True,'explicit_groups_separate':True,'routing_still_rejects_unfilled_planes':True}
