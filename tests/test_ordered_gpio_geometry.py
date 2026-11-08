from copy import deepcopy
import pytest
from smilepcb.keyboard_routing.bank_geometry import bank_allocation,exit_permutation


def test_matrix_families_use_exclusive_real_banks_in_target_order():
 banks={'N':['n0','n1','n2'],'E':['e0','e1','e2','e3'],'S':['s0','s1','s2','s3','s4'],'W':['w0','w1']};before=deepcopy(banks)
 assert bank_allocation(banks,{'rows':5,'columns':7},preferred=('E','S'))=={'rows':['s0','s1','s2','s3','s4'],'columns':['n0','n1','n2','e0','e1','e2','e3']}
 assert banks==before


def test_insufficient_physical_capacity_is_not_silently_reused():
 assert bank_allocation({'E':['a','b'],'S':['c','d']},{'rows':3,'columns':3}) is None


@pytest.mark.parametrize('banks,needs',[
 ({'E':['same'],'S':['same']},{'rows':1}),
 ({'E':['a','b']},{'rows':True}),
 ({'E':['a','b']},{'rows':-1}),
])
def test_ambiguous_or_invalid_bank_capacity_fails_before_assignment(banks,needs):
 with pytest.raises(ValueError):bank_allocation(banks,needs)


def test_signal_identity_follows_final_lane_order_after_a_turn():
 ports=[dict(net=n,bank=['chip',1,1],exit_layer=2,exit_nm=[30000-i*10000,20000],heading_origin_nm=[30000-i*10000,10000]) for i,n in enumerate(('alpha','beta','gamma'))]
 targets={'alpha':(100000,0),'beta':(100000,10000),'gamma':(100000,20000)}
 assert exit_permutation(ports,targets,{n:'rows' for n in targets})=={'alpha':'gamma','beta':'beta','gamma':'alpha'}


def test_physical_bundle_can_mix_roles_without_swapping_rows_with_columns():
 ports=[dict(net=n,bank=['chip',0,1],exit_layer=0,exit_nm=[10000,i*10000],heading_origin_nm=[0,i*10000]) for i,n in enumerate(('r0','c0','r1','c1'))]
 targets={'r0':(0,20000),'r1':(0,0),'c0':(20000,0),'c1':(0,0)}
 assert exit_permutation(ports,targets,{'r0':'rows','r1':'rows','c0':'columns','c1':'columns'})=={'r0':'r1','r1':'r0','c0':'c1','c1':'c0'}


def test_bank_assignment_updates_shared_pins_without_inventing_net_names():
 from smilepcb.controller_gpio import assign_matrix_gpio
 gpios=['A0','A1','A2','A3','A4','A5','A6'];numbers={g:str(i+1) for i,g in enumerate(gpios)}
 cfg={'parts':[{'ref':'U_MCU','nets':{'1':'rA','2':'rB','3':'cA','4':'cB','5':'cC','6':'NC_MCU_6','7':'NC_MCU_7','8':'USB_DP'},'no_connect':['6','7']}], 'mcu_gpio_pads':numbers,'layout':{'gpio':{'rows':['A0','A1'],'cols':['A2','A3','A4']}},'qmk':{'matrix_pins':{'rows':['A0','A1'],'cols':['A2','A3','A4']}}}
 old=deepcopy(cfg);locations={g:(i*1000000,0) for i,g in enumerate(gpios)};targets={n:(10000000,i*1000000) for i,n in enumerate(('rA','rB','cA','cB','cC'))}
 result,proof=assign_matrix_gpio(cfg,locations,targets,banks={'N':['A0','A1','A2','A3'],'E':['A4','A5','A6']})
 assert cfg==old and result['layout']['gpio']==result['qmk']['matrix_pins']
 row_banks={g in {'A0','A1','A2','A3'} for g in result['layout']['gpio']['rows']}
 col_banks={g in {'A0','A1','A2','A3'} for g in result['layout']['gpio']['cols']}
 assert len(row_banks)==len(col_banks)==1 and row_banks!=col_banks
 for role,names in (('rows',['rA','rB']),('cols',['cA','cB','cC'])):
  assert [result['parts'][0]['nets'][numbers[g]] for g in result['layout']['gpio'][role]]==names
 assert result['parts'][0]['nets']['8']=='USB_DP'
 assert set(proof['assignment'])=={'rA','rB','cA','cB','cC'}


@pytest.mark.parametrize('rotation',range(4))
def test_prefix_misses_break_confidence_ties_without_displacing_certified_capacity(rotation):
 # 27 real-bank slots: certified E10/S3, weak N4/W6, missed N3/S1.
 sides=('N','E','S','W');rename={s:sides[(i+rotation)%4] for i,s in enumerate(sides)}
 banks={'N':['n_miss0',*[f'n_weak{i}' for i in range(4)],'n_miss1','n_miss2'],
        'E':[f'e_cert{i}' for i in range(10)],
        'S':[f's_cert{i}' for i in range(3)]+['s_miss'],
        'W':[f'w_weak{i}' for i in range(6)]}
 missed={g for pins in banks.values() for g in pins if 'miss' in g}
 uncertain=missed|{g for pins in banks.values() for g in pins if 'weak' in g}
 moved={rename[s]:pins for s,pins in banks.items()};before=deepcopy(moved)
 allocation=bank_allocation(moved,{'rows':5,'columns':14},uncertain_pins=uncertain,unwitnessed_pins=missed)
 selected=sum(allocation.values(),[])
 assert len(allocation['rows'])==5 and len(allocation['columns'])==14
 assert len(selected)==len(set(selected))==19
 assert len(set(selected)-uncertain)==13 and len(set(selected)&uncertain)==6
 assert not set(selected)&missed
 used={family:{side for side,pins in moved.items() if set(pins)&set(chosen)}
       for family,chosen in allocation.items()}
 assert not used['rows']&used['columns']
 # Score is equivariant even where direction/repr resolves equal-cost chunks.
 assert (len(set(selected)&uncertain),len(set(selected)&missed),sum(map(len,used.values())),len(selected))==(6,0,4,19)
 assert moved==before


def test_unwitnessed_pins_remain_available_when_required():
 assert bank_allocation({'E':['good','miss']},{'rows':2},uncertain_pins=['miss'],unwitnessed_pins=['miss'])=={'rows':['good','miss']}
 with pytest.raises(ValueError):bank_allocation({'E':['good']},{'rows':1},unwitnessed_pins=['absent'])


def test_gpio_receipt_records_misses_without_excluding_spares_or_desynchronizing_model():
 from smilepcb.controller_gpio import assign_matrix_gpio
 gpios=['A0','A1','A2'];pads={g:str(i+1) for i,g in enumerate(gpios)}
 cfg={'parts':[{'ref':'U_MCU','nets':{'1':'row','2':'col','3':'NC_MCU_3'},'no_connect':['3']}],
      'mcu_gpio_pads':pads,'layout':{'gpio':{'rows':['A0'],'cols':['A1']}},'qmk':{'matrix_pins':{'rows':['A0'],'cols':['A1']}}}
 updated,proof=assign_matrix_gpio(cfg,{g:(i,0) for i,g in enumerate(gpios)},{'row':(0,0),'col':(10,0)},
     banks={'N':['A0'],'E':['A1'],'W':['A2']},uncertain_gpios=['A0','A2'],unwitnessed_gpios=['A0'])
 assert proof['safe_available_pins']==3 and proof['excluded_gpios']==[]
 assert proof['unwitnessed_selected']==[] and proof['uncertain_selected']==['A2']
 assert updated['layout']['gpio']==updated['qmk']['matrix_pins']
 assert updated['parts'][0]['nets'][pads[updated['layout']['gpio']['rows'][0]]]=='row'
 _,required=assign_matrix_gpio(cfg,{'A0':(0,0),'A1':(1,0)},{'row':(0,0),'col':(10,0)},
     banks={'N':['A0'],'E':['A1']},uncertain_gpios=['A0'],unwitnessed_gpios=['A0'])
 assert required['unwitnessed_selected']==['A0'] and required['safe_available_pins']==2



def test_witnessed_prefix_wins_equal_uncertainty_tie_and_bank_order_is_confidence_first():
 banks={'N':['miss'],'E':['cert0','cert1'],'W':['weak']}
 assert bank_allocation(banks,{'rows':1,'columns':2},uncertain_pins=['miss','weak'],unwitnessed_pins=['miss'])=={'rows':['weak'],'columns':['cert0','cert1']}
 assert bank_allocation({'E':['miss','weak','cert']},{'rows':3},uncertain_pins=['miss','weak'],unwitnessed_pins=['miss'])=={'rows':['cert','weak','miss']}
