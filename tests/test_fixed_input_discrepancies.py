"""Only native-safe, fixed, completed same-net via pairs may differ in router DRC."""
from copy import deepcopy
import pytest
from smilepcb import fixed_copper_exchange as api

FRAME={'tracks':{q:{'class':'PCB_VIA','net':'lane','start':[x,0],'end':[x,0],'width':500000,'drill':200000,'top':0,'bottom':2} for q,x in [('a',0),('b',630000)]}}
REPORT={'coordinateUnits':'mm','violations':[{'type':'holeClearance','severity':'error','items':[{'description':'Via [lane]','pos':{'x':0,'y':0}},{'description':'Via [lane]','pos':{'x':.63,'y':0}}]}]}

def verify(report=None,frame=None,protected=None,completed=None):
 return api.verify_fixed_input_discrepancies(REPORT if report is None else report,FRAME if frame is None else frame,protected_ids={'a','b'} if protected is None else protected,completed_nets={'lane'} if completed is None else completed,hole_clearance_nm=250000)

def test_fixed_same_net_copper_gap_may_be_smaller_than_foreign_net_clearance():
 report=deepcopy(REPORT);frame=deepcopy(FRAME);proof=verify(report,frame)
 assert proof['accepted'] and proof['violations']==1
 assert proof['pairs']==[{'ids':['a','b'],'net':'lane','hole_gap_nm':430000.0}]
 assert report==REPORT and frame==FRAME

@pytest.mark.parametrize('change',['foreign_net','editable','open','new_position','hole_gap','bad_drill','wrong_type','not_via','ambiguous','units'])
def test_unknown_or_manufacturing_invalid_discrepancies_are_rejected(change):
 report=deepcopy(REPORT);frame=deepcopy(FRAME);protected={'a','b'};completed={'lane'}
 if change=='foreign_net':report['violations'][0]['items'][1]['description']='Via [other]'
 if change=='editable':protected={'a'}
 if change=='open':completed=set()
 if change=='new_position':report['violations'][0]['items'][1]['pos']['x']=2
 if change=='hole_gap':frame['tracks']['b'].update(drill=600000,width=900000)
 if change=='bad_drill':frame['tracks']['b']['drill']=700000
 if change=='wrong_type':report['violations'][0]['type']='clearance'
 if change=='not_via':report['violations'][0]['items'][1]['description']='Pin [lane]'
 if change=='ambiguous':frame['tracks']['c']=deepcopy(frame['tracks']['a']);protected.add('c')
 if change=='units':report['coordinateUnits']='inch'
 with pytest.raises(ValueError):verify(report,frame,protected,completed)

def test_two_faces_of_the_same_fixed_pair_keep_one_physical_pair():
 report=deepcopy(REPORT);report['violations'].append(deepcopy(report['violations'][0]));proof=verify(report)
 assert proof['violations']==2 and len(proof['pairs'])==1

def test_clean_router_input_needs_no_exceptions():
 proof=verify({**REPORT,'violations':[]});assert proof['accepted'] and not proof['pairs'] and proof['violations']==0
