from copy import deepcopy
import pytest
from smilepcb.keyboard_routing import conditional_search as search

BASIS='completed_final_native_graph_after_scheduled_routing'
SOURCES=[['early'],['late'],['alias','last']]


def rejected(failures=(('late',),), *, basis=BASIS, sources=SOURCES):
    return {'accepted':False,'failed_interfaces':deepcopy(failures),
            'source_interfaces':deepcopy(sources),'feedback_basis':basis}


def accepted(bank,option):return {'accepted':True,'plan':{'bank':bank,'id':option}}


def run(attempt,*,order=('bank',),**kwargs):
    return search.conditional_search(order,attempt,deadline=10,
        leaf_validator=kwargs.pop('leaf_validator',lambda _: {'accepted':True}),
        retry_unresolved_first=True,**kwargs)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    now=[0.0];monkeypatch.setattr(search,'monotonic',lambda:now[0]);return now


def test_certified_failed_interface_retry_can_complete_before_head_regeneration():
    calls=[]
    def attempt(bank,option,prefix,feedback,deadline):
        calls.append((option,deepcopy(feedback)))
        return rejected() if option=='original' else accepted(bank,option)
    result=run(attempt)
    assert result['accepted']
    assert calls==[('original',[]),('original_priority',[('late',)])]
    assert result['selection'][0]['id']=='original_priority'
    assert result['selection_attempts']==[2]
    assert result['attempt_records'][1]['feedback_origin_attempt']==1


@pytest.mark.parametrize('record',[
    rejected(basis='native_original_head_admission'),
    rejected(basis='final_assessment_unavailable_due_deadline'),
    rejected(sources=None),
    rejected(failures=[['unknown']]),
    rejected(failures=[['alias']]),
    rejected(sources=[['early'],['late'],['late']]),
    rejected(sources=[['early'],['late','alias'],['alias','last']]),
    rejected(failures=[['early']]),
    rejected(failures=SOURCES),
])
def test_uncertified_unsupported_or_noop_feedback_does_not_schedule_priority(record):
    options=[]
    def attempt(bank,option,prefix,feedback,deadline):
        options.append(option);return deepcopy(record) if option=='original' else accepted(bank,option)
    assert run(attempt)['accepted']
    assert options==['original','regenerated']


def test_regeneration_uses_original_feedback_not_priority_failure():
    calls=[]
    def attempt(bank,option,prefix,feedback,deadline):
        calls.append((option,deepcopy(feedback)))
        if option=='original':return rejected()
        if option=='original_priority':return rejected([['alias','last']])
        return accepted(bank,option)
    assert run(attempt)['accepted']
    assert calls==[('original',[]),('original_priority',[('late',)]),('regenerated',[('late',)])]


def test_accepted_original_never_receives_descendant_failure_feedback():
    calls=[]
    def attempt(bank,option,prefix,feedback,deadline):
        calls.append((bank,option,[q['id'] for q in prefix],deepcopy(feedback)))
        if bank=='child' and prefix[0]['id']=='original':return rejected()
        return accepted(bank,option)
    result=run(attempt,order=('parent','child'))
    assert result['accepted']
    assert not any(b=='parent' and o=='original_priority' for b,o,_,_ in calls)
    assert next(f for b,o,p,f in calls if b=='parent' and o=='regenerated')==[]
    assert len([1 for b,o,_,_ in calls if b=='child' and o=='original_priority'])==1
    assert [p['id'] for p in result['selection']]==['regenerated','original']


def test_retry_is_once_per_exact_prefix_and_attempt_provenance_separates_parents():
    calls=[]
    def attempt(bank,option,prefix,feedback,deadline):
        calls.append((bank,option,tuple(q['id'] for q in prefix)))
        if bank=='parent':return accepted(bank,option)
        if option=='original':return rejected()
        if option=='original_priority' and prefix[0]['id']=='regenerated':return accepted(bank,option)
        return {'accepted':False}
    result=run(attempt,order=('parent','child'))
    assert result['accepted']
    retries=[row for row in result['attempt_records'] if row['option']=='original_priority']
    assert len(retries)==2 and retries[0]['prefix_attempts']!=retries[1]['prefix_attempts']
    assert retries[0]['feedback_origin_attempt']!=retries[1]['feedback_origin_attempt']


def test_attempt_cap_and_frontier_include_priority_before_regeneration():
    result=run(lambda *args:rejected(),max_attempts=1)
    assert result['reason']=='attempt_limit'
    assert [q['option'] for q in result['frontier']]==['original_priority','regenerated']
    assert len(result['attempt_records'])==1


def test_deadline_after_original_does_not_run_priority(clock):
    def attempt(*args):clock[0]=11;return rejected()
    result=run(attempt)
    assert result['reason']=='deadline' and len(result['attempt_records'])==1
    assert [q['option'] for q in result['frontier']]==['original_priority','regenerated']


def test_retry_cannot_mutate_certified_feedback():
    def attempt(bank,option,prefix,feedback,deadline):
        if option=='original':return rejected()
        feedback.clear();return accepted(bank,option)
    with pytest.raises(ValueError,match='mutated'):run(attempt)


def test_stable_full_alias_priority_preserves_both_relative_orders():
    original=[['early'],['alias','last'],['late'],['tail']]
    assert search.failed_first_order(original,[['late'],['last','alias']])==[
        ('alias','last'),('late',),('early',),('tail',)]
    assert original==[['early'],['alias','last'],['late'],['tail']]
