"""Positive, heading-aware costs; admission remains a separate native gate."""
import math
import pytest


def test_default_and_request_context_reset_even_after_failure():
    from smilepcb.routing_quality import current_quality,using_quality_profile
    assert current_quality().name=='legacy'
    with pytest.raises(RuntimeError):
        with using_quality_profile('turns'):
            assert current_quality().bend_penalty_nm==1000000
            raise RuntimeError('operation failed')
    assert current_quality().name=='legacy'


@pytest.mark.parametrize('mode',[None,False,'','experimental',{},['turns']])
def test_invalid_modes_are_rejected(mode):
    from smilepcb.routing_quality import using_quality_profile
    with pytest.raises(ValueError):
        with using_quality_profile(mode):pass


def test_split_straights_exact_leads_and_reversals_count_correctly():
    from smilepcb.routing_quality import path_cost,using_quality_profile
    path=[(0,0),(1000000,0),(2000000,0),(2000000,1000000)]
    assert path_cost(path)==3000000
    with using_quality_profile('turns'):
        assert path_cost(path)==4000000
        assert path_cost(path,initial_heading=(0,1))==5000000
        assert path_cost([(0,0),(0,0),(1000000,0)])==1000000
        assert path_cost([(0,0),(1000000,0),(0,0)])==3000000
        # A via starts a fresh face: no incoming heading is carried across it.
        assert path_cost([(0,0),(0,1000000)],initial_heading=None)==1000000


def test_whole_copper_metrics_include_planes_signals_and_vias():
    from smilepcb.routing_quality import copper_metrics
    def track(a,b,net):return dict(start=a,end=b,net=net,layer=0,**{'class':'PCB_TRACK'})
    result=copper_metrics({'a':track((0,0),(1000000,0),'GND'),
                          'b':track((1000000,0),(2000000,0),'GND'),
                          'c':track((2000000,0),(2000000,1000000),'GND'),
                          'd':track((0,0),(3000000,0),'RGB'),
                          'v':{'class':'PCB_VIA'}})
    assert result['length_nm']==6000000
    assert result['bends']==1 and result['vias']==1


def test_shortest_legal_simple_path_is_ranked_before_doglegs():
    from smilepcb.routing_quality import ranked_paths,using_quality_profile
    paths=[[(0,0),(0,1000000),(2000000,1000000)],
           [(0,0),(1000000,1000000),(2000000,1000000)]]
    assert list(ranked_paths(paths))==paths
    with using_quality_profile('turns'):
        assert list(ranked_paths(paths))==list(reversed(paths))


def test_heading_is_part_of_turns_dominance_and_resets_at_vias():
    from smilepcb.routing_quality import search_state,using_quality_profile
    assert search_state((1,2,0),(1,0))==(1,2,0)
    with using_quality_profile('turns'):
        assert search_state((1,2,0),(1,0))!=search_state((1,2,0),(0,1))
        assert search_state((1,2,2),None)==(1,2,2,None)


def test_exact_final_lead_can_outweigh_the_nearest_target():
    from smilepcb.routing_quality import path_cost,using_quality_profile
    with using_quality_profile('turns'):
        assert path_cost([(0,0),(750000,0)],initial_heading=(1,0)) < path_cost([(0,0),(0,250000)],initial_heading=(1,0))


@pytest.mark.parametrize('args',[['--routing-quality'],['--routing-quality','bad'],['--routing-quality','turns','--routing-quality','legacy'],
                                ['--routing-quality=bogus'],['--routing-quality='],['--routing-quality','turns','--routing-quality=legacy']])
def test_recipe_stage_rejects_missing_invalid_and_repeated_quality(args):
    from smilepcb.routing_quality import quality_from_args
    with pytest.raises(ValueError):quality_from_args(args)


@pytest.mark.parametrize('mode',['legacy','turns'])
def test_recipe_stage_accepts_equals_form(mode):
    from smilepcb.routing_quality import quality_from_args
    assert quality_from_args(['--routing-quality='+mode])==mode
