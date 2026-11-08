"""Completed physical PTH pads keep full occupancy in temporary exchange."""
from copy import deepcopy
import math
import pytest
from smilepcb import fixed_copper_exchange as api

DSN="""(pcb board (unit um)
 (structure (via v))
 (placement (component key (place SW1 10000 -10000 front 0) (place SW2 20000 -10000 front 0)))
 (library (image key (pin p 1 0 0))
  (padstack p (shape (circle F.Cu 3300)) (shape (circle B.Cu 3300)) (attach off))
  (padstack v (shape (circle F.Cu 500)) (shape (circle B.Cu 500))))
 (network (net lane (pins SW1-1 SW2-1)))
 (wiring (via v 10000 -8000 (net lane) (type fix))))"""
PADS=[{'ref':'SW1','number':'1','net':'lane','position':[10000000,10000000],'size':[3300000,3300000],'drill':[3000000,3000000],'uuid':'physical-pad'}]
REPORT={'coordinateUnits':'mm','violations':[{'severity':'error','description':'Hole clearance violation','items':[{'description':'Via [lane]','pos':{'x':10,'y':-8}},{'description':'Pin [lane]','pos':{'x':10,'y':-10}}]}]}

def transform(pads=None,report=None,completed=frozenset({'lane'}),text=DSN):
 return api.fixed_round_pin_exchange(text,REPORT if report is None else report,PADS if pads is None else pads,completed_nets=completed)

def test_completed_round_pin_can_export_without_reducing_via_or_other_instances():
 oldpads=deepcopy(PADS);oldreport=deepcopy(REPORT);out,receipt=transform();before=api._read(DSN);after=api._read(out)
 assert PADS==oldpads and REPORT==oldreport
 assert receipt['converted_pads']==[{'ref':'SW1','number':'1','net':'lane','uuid':'physical-pad'}]
 oldlib=api._child(before,'library');newlib=api._child(after,'library')
 assert newlib[1:len(oldlib)]==oldlib[1:]
 assert api._child(after,'wiring')[1]==api._child(before,'wiring')[1]
 places=api._child(after,'placement')[1:]
 assert places[0]==[api.sx.Symbol('component'),api.sx.Symbol('key'),[api.sx.Symbol('place'),api.sx.Symbol('SW2'),20000,-10000,api.sx.Symbol('front'),0]]
 assert len(places)==2 and str(places[1][2][1])=='SW1'
 assert len([q for q in api._child(after,'wiring')[1:] if api._child(q,'polygon') is not None])==2

def test_exchange_polygons_enclose_full_physical_pad_with_rounding_guard():
 out,receipt=transform();tree=api._read(out);polys=[api._child(q,'polygon') for q in api._child(tree,'wiring')[1:] if api._child(q,'polygon') is not None]
 for poly in polys:
  points=list(zip(poly[3::2],poly[4::2]))
  for i in range(720):
   phi=i*math.pi/360;q=(10000+1650*math.cos(phi),-10000+1650*math.sin(phi));cross=[(z[0]-a[0])*(q[1]-a[1])-(z[1]-a[1])*(q[0]-a[0]) for a,z in zip(points,points[1:]+points[:1])]
   assert min(cross)>=-1e-6 or max(cross)<=1e-6

@pytest.mark.parametrize('case',['open','different_net','no_error','no_via','no_pin','nonround','no_drill'])
def test_unqualified_pads_do_not_receive_fixed_connector_representation(case):
 pads=deepcopy(PADS);report=deepcopy(REPORT);completed={'lane'}
 if case=='open':completed=set()
 if case=='different_net':report['violations'][0]['items'][0]['description']='Via [other]'
 if case=='no_error':report['violations'][0]['severity']='warning'
 if case=='no_via':report['violations'][0]['items']=report['violations'][0]['items'][1:]
 if case=='no_pin':report['violations'][0]['items']=report['violations'][0]['items'][:1]
 if case=='nonround':pads[0]['size'][1]+=1
 if case=='no_drill':pads[0]['drill']=[0,0]
 text,receipt=transform(pads,report,completed);assert text==DSN and receipt['converted_pads']==[]


def test_ambiguous_physical_pin_is_rejected_instead_of_guessing():
 with pytest.raises(ValueError):transform([*PADS,{**PADS[0],'uuid':'another'}])


def test_unrecognized_report_units_are_rejected():
 with pytest.raises(ValueError):transform(report={**REPORT,'coordinateUnits':'inch'})


def alias_scene(*,both=False,rotated=False,mixed=False):
 tree=api._read(DSN);lib=api._child(tree,'library');image=next(q for q in lib[1:] if str(q[0])=='image')
 image.append([api.sx.Symbol('pin'),'p',*([[api.sx.Symbol('rotate'),90]] if rotated else []),'1@1',5000,0])
 net=api._child(api._child(tree,'network'),'net');api._child(net,'pins').append('SW1-1@1')
 pads=[{**PADS[0],'position':[15000000,10000000],'uuid':'second-circle'}]
 report=deepcopy(REPORT);report['violations'][0]['items'][1]['pos']['x']=15
 if mixed:
  image[2][1]='smd';lib.append([api.sx.Symbol('padstack'),'smd',[api.sx.Symbol('shape'),[api.sx.Symbol('rect'),api.sx.Symbol('F.Cu'),-100,-100,100,100]]])
 if both:
  pads=[*PADS,*pads];report['violations'].append(deepcopy(REPORT['violations'][0]))
 return api._write(tree),pads,report


@pytest.mark.parametrize('rotated,mixed',[(False,False),(True,False),(False,True)])
def test_physical_alias_is_matched_by_pose_instead_of_number(rotated,mixed):
 text,pads,report=alias_scene(rotated=rotated,mixed=mixed)
 out,proof=transform(pads,report,text=text);tree=api._read(out);lib=api._child(tree,'library')
 image=next(q for q in lib[1:] if str(q[0])=='image' and str(q[1]).startswith('SmilePCBFixedPinImage'))
 by_alias={str([v for v in q[2:] if not isinstance(v,list)][0]):q for q in image[2:] if str(q[0])=='pin'}
 assert str(by_alias['1'][1])==('smd' if mixed else 'p')
 assert str(by_alias['1@1'][1]).startswith('SmilePCBFixedPinConnector')
 assert proof['converted_pads'][0]['uuid']=='second-circle'


def test_two_physical_aliases_of_one_pad_number_are_not_collapsed():
 text,pads,report=alias_scene(both=True);out,proof=transform(pads,report,text=text)
 assert {q['uuid'] for q in proof['converted_pads']}=={'physical-pad','second-circle'}
 wiring=api._child(api._read(out),'wiring')
 assert sum(api._child(q,'polygon') is not None for q in wiring[1:])==4


def test_pin_position_mismatch_fails_instead_of_shrinking_another_pad():
 pads=[{**PADS[0],'position':[15000000,10000000]}];report=deepcopy(REPORT);report['violations'][0]['items'][1]['pos']['x']=15
 with pytest.raises(ValueError):transform(pads,report)
