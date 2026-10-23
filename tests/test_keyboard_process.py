import pytest

def test_legacy_process_defaults_keep_the_existing_rules():
    from smilepcb.keyboard_process import process_rules
    assert process_rules()=={'min_clearance':.15,'min_copper_edge_clearance':.2,'min_track_width':.15,'min_via_annular_width':.15,'min_via_diameter':.6,'min_through_hole_diameter':.3}

def test_standard_compact_vias_keep_the_annulus_track_and_clearance():
    from smilepcb.keyboard_process import process_rules,COMPACT_VIAS
    legacy=process_rules();compact=process_rules(COMPACT_VIAS)
    assert compact['min_via_diameter']==.5 and compact['min_through_hole_diameter']==.2
    assert (compact['min_via_diameter']-compact['min_through_hole_diameter'])/2==.15
    assert {k:v for k,v in compact.items() if k not in ('min_via_diameter','min_through_hole_diameter')}=={k:v for k,v in legacy.items() if k not in ('min_via_diameter','min_through_hole_diameter')}
    compact['min_clearance']=0
    assert process_rules(COMPACT_VIAS)['min_clearance']==.15

@pytest.mark.parametrize('bad',['unknown','',False,{},['fr4']])
def test_unknown_process_is_rejected(bad):
    from smilepcb.keyboard_process import process_rules
    with pytest.raises(ValueError):process_rules(bad)

def test_saved_project_and_default_netclass_share_the_explicit_via_profile():
    from smilepcb.keyboard_process import project_settings,COMPACT_VIAS
    pro=project_settings(COMPACT_VIAS)
    rules=pro['board']['design_settings']['rules'];netclass=pro['net_settings']['classes'][0]
    assert rules['min_via_diameter']==netclass['via_diameter']==.5
    assert rules['min_through_hole_diameter']==netclass['via_drill']==.2
    assert rules['min_clearance']==netclass['clearance']==.15
    assert rules['min_track_width']==netclass['track_width']==.15
    assert project_settings()['board']['design_settings']['rules']['min_via_diameter']==.6
    pro['net_settings']['classes'][0]['via_drill']=0
    assert project_settings(COMPACT_VIAS)['net_settings']['classes'][0]['via_drill']==.2
