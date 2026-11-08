"""Goal-domain direction policy; no Native physical feasibility claims."""
import pytest
from test_constrained_frontier_schedule import scenario,failed


def test_interchangeable_pair_prefers_larger_start_domain_and_smaller_goal(monkeypatch):
    s=scenario(monkeypatch,failed,source_count=4,field_count=1,live=False)
    s.run(prefer_small_goal=True)
    assert s.calls[0]['from_source'] and s.calls[0]['sources']==4 and s.calls[0]['targets']==1


@pytest.mark.parametrize('reverse_groups',[False,True])
def test_each_authoritative_head_orientation_keeps_source_priority(monkeypatch,reverse_groups):
    s=scenario(monkeypatch,failed,source_count=1,field_count=4,live=True,reverse_groups=reverse_groups)
    s.run(prefer_small_goal=True)
    assert s.calls[0]['from_source'] and s.calls[0]['sources']==1 and s.calls[0]['targets']==4


def test_first_contact_contract_retains_legacy_direction(monkeypatch):
    s=scenario(monkeypatch,failed,source_count=4,field_count=1,live=False)
    s.run(prefer_small_goal=True,interface_refs={'source'})
    assert not s.calls[0]['from_source'] and s.calls[0]['sources']==1


def test_equal_domains_keep_deterministic_legacy_tie(monkeypatch):
    legacy=scenario(monkeypatch,failed,source_count=2,field_count=2,live=False);legacy.run()
    candidate=scenario(monkeypatch,failed,source_count=2,field_count=2,live=False);candidate.run(prefer_small_goal=True)
    assert candidate.calls==legacy.calls


def test_direction_and_reverse_share_original_connection_deadline(monkeypatch):
    s=scenario(monkeypatch,lambda row,index:(False,'search_budget',4 if index==1 else 2),source_count=4,field_count=1,live=False)
    s.run(prefer_small_goal=True)
    assert s.calls[0]['from_source'] and not s.calls[1]['from_source']
    assert [c['deadline'] for c in s.calls]==[6,6] and s.router.deadline==100
    assert s.clock.now==6


def test_legacy_default_still_starts_from_smaller_domain(monkeypatch):
    s=scenario(monkeypatch,failed,source_count=4,field_count=1,live=False);s.run()
    assert not s.calls[0]['from_source'] and s.calls[0]['sources']==1 and s.calls[0]['targets']==4


def test_goal_preference_survives_current_component_refresh_dispatch(monkeypatch):
    from test_current_component_domains import scene
    s=scene(monkeypatch);assert s.run(refresh_components=True,prefer_small_goal=True)==2
    tree=[c for c in s.calls if c['net']=='tree']
    assert ((20,10),0) in tree[1]['starts'] and len(tree[1]['targets'])==1
