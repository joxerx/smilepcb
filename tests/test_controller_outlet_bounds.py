"""Outlet admission measures a complete trace cap outside the reserved frame."""
import pytest
from smilepcb.keyboard_routing.bank_geometry import port_outside_bounds,bank_allocation

@pytest.mark.parametrize('point,heading,expected',[((0,-100),(0,-1),False),((0,-176),(0,-1),True),((0,176),(0,1),True),((176,0),(1,0),True),((-176,0),(-1,0),True)])
def test_complete_terminal_cap_must_clear_its_outward_frame(point,heading,expected):
 assert port_outside_bounds(point,heading,(-100,-100,100,100),150) is expected

def test_certified_outlets_take_precedence_over_fewer_gpio_banks():
 banks={'N':['north-clear','north-uncertain'],'E':['east-clear'],'S':['south-clear'],'W':['row-clear']}
 result=bank_allocation(banks,{'rows':1,'cols':3},uncertain_pins={'north-uncertain'})
 assert 'north-uncertain' not in result['rows']+result['cols'],result
 assert len(result['rows'])==1 and len(result['cols'])==3

@pytest.mark.parametrize('mirror',[1,-1])
def test_selected_bank_chunks_follow_target_geometry_instead_of_compass_names(mirror):
 from smilepcb.controller_gpio import assign_matrix_gpio
 gpios=['A0','A1','A2','A3'];numbers={g:str(i+1) for i,g in enumerate(gpios)}
 cfg={'parts':[{'ref':'U_MCU','nets':{'1':'a','2':'b','3':'c','4':'d'}}],
      'mcu_gpio_pads':numbers,'layout':{'gpio':{'rows':['A3'],'cols':['A0','A1','A2']}},
      'qmk':{'matrix_pins':{'rows':['A3'],'cols':['A0','A1','A2']}}}
 banks={'N':['A0'],'E':['A1'],'S':['A2'],'W':['A3']}
 locations={'A0':(0,-10),'A1':(10*mirror,0),'A2':(0,10),'A3':(-10*mirror,0)}
 targets={'a':(-100,0),'b':(0,0),'c':(100,0),'d':(0,0)}
 result,_=assign_matrix_gpio(cfg,locations,targets,banks=banks)
 xs=[locations[g][0] for g in result['layout']['gpio']['cols']]
 assert xs==sorted(xs),(mirror,result)
 assert result['layout']['gpio']==result['qmk']['matrix_pins']
