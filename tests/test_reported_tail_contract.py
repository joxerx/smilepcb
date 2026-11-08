"""DRC cleanup may retire owned prerequisites, never mutate a field spine."""
from copy import deepcopy
import pytest
from smilepcb.keyboard_routing.reported_tail_contract import retire_prerequisites,require_exact_tail_delta


def findings(uid='tail'):
 return [{'type':'track_dangling','severity':'warning','items':[{'uuid':uid}]}]


def contract():
 return {'schema':1,'roles':{'spine':'column_trunk'},'tracks':{'spine':{'net':'c'}},
  'prerequisite_tracks':{'tail':{'net':'power'},'fixed':{'net':'other'}},'terminals':{'pad':['columns','c']}}


def test_only_reported_generated_prerequisite_is_retired():
 old=contract();result=retire_prerequisites(old,findings(),generated_ids={'tail'},protected_ids=set())
 expected=deepcopy(old);del expected['prerequisite_tracks']['tail']
 assert result==expected and 'tail' in old['prerequisite_tracks']


@pytest.mark.parametrize('uid,generated,protected',[('spine',{'spine'},set()),('tail',set(),set()),('tail',{'tail'},{'tail'})])
def test_matrix_roles_unknown_and_hard_protection_remain_immutable(uid,generated,protected):
 with pytest.raises(ValueError):retire_prerequisites(contract(),findings(uid),generated_ids=generated,protected_ids=protected)


def test_non_prerequisite_generated_tail_needs_no_receipt_change():
 old=contract()
 assert retire_prerequisites(old,findings('later'),generated_ids={'later'},protected_ids=set())==old


def test_any_extra_copper_delta_is_rejected():
 before={'tracks':{'tail':{'locked':False},'fixed':{'locked':False}},'footprints':{'pads':'exact'}}
 after=deepcopy(before);del after['tracks']['tail'];require_exact_tail_delta(before,after,{'tail'})
 for kind in ('added','changed','pad','locked'):
  a=deepcopy(after);b=deepcopy(before)
  if kind=='added':a['tracks']['extra']={}
  elif kind=='changed':a['tracks']['fixed']['locked']=True
  elif kind=='pad':a['footprints']={}
  else:b['tracks']['tail']['locked']=True
  with pytest.raises(ValueError):require_exact_tail_delta(b,a,{'tail'})
