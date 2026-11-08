"""Focused Native regression probe, usable outside the measured checkout."""
import importlib.util,json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.local_router import LocalRouter
root=Path(sys.argv[1]);output=Path(sys.argv[2]);helper=root/'source_pad_star.py'
if not helper.exists():
    print('Missing source_pad_star.owned_pad_stubs',file=sys.stderr)
    raise SystemExit(1)
spec=importlib.util.spec_from_file_location('source_pad_star',helper);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
owned_pad_stubs=module.owned_pad_stubs
M=1000000

def fixture():
    b=p.BOARD();b.SetCopperLayerCount(2);rules=b.GetDesignSettings();rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000
    for net in ('signal','foreign'):b.Add(p.NETINFO_ITEM(b,net))
    corners=[(0,0),(20*M,0),(20*M,15*M),(0,15*M)]
    for a,z in zip(corners,corners[1:]+corners[:1]):
        edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetLayer(p.Edge_Cuts);edge.SetStart(p.VECTOR2I(*a));edge.SetEnd(p.VECTOR2I(*z));edge.SetWidth(50000);b.Add(edge)
    f=p.FOOTPRINT(b);f.SetReference('SOURCE');b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(p.VECTOR2I(5*M,5*M));d.SetSize(p.VECTOR2I(1475000,300000));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_ROUNDRECT);d.SetRoundRectRadiusRatio(.25);layers=p.LSET();layers.AddLayer(p.B_Cu);d.SetLayerSet(layers);d.SetNet(b.FindNet('signal'));f.Add(d)
    return b,d

def track(b,a,z,net='signal',face=p.B_Cu,locked=False):
    item=p.PCB_TRACK(b);item.SetStart(p.VECTOR2I(*a));item.SetEnd(p.VECTOR2I(*z));item.SetWidth(150000);item.SetLayer(face);item.SetNet(b.FindNet(net));item.SetLocked(locked);b.Add(item);return item

def uid(item):return item.m_Uuid.AsString()

def inspect(b,d,chain=(),protected=()):
    oracle=LocalRouter(b,compact=True,ignored_copper=chain,track_width_nm=150000,deadline=time.monotonic()+5)
    before=snapshot(b);result=owned_pad_stubs(b,d,chain,protected,oracle)
    assert snapshot(b)==before,'Ownership discovery mutated Native board'
    return result

result={}
b,d=fixture();spur=track(b,(5*M,5*M),(5700000,5*M));assert 700000+75000>1475000/2
assert inspect(b,d)=={uid(spur)};result['beyond-strict-land-safe']=True

b,d=fixture();spur=track(b,(5*M,5*M),(5700000,5*M));branch=track(b,(5825000,5*M),(7*M,5*M))
assert p.SHAPE.Collide(spur.GetEffectiveShape(p.B_Cu),branch.GetEffectiveShape(p.B_Cu),0)
assert not p.SHAPE.Collide(branch.GetEffectiveShape(p.B_Cu),d.GetEffectiveShape(p.B_Cu),0)
assert inspect(b,d)==set();result['retained-external-branch-blocks']=True

b,d=fixture();spur=track(b,(5*M,5*M),(5700000,5*M));branch=track(b,(5825000,5*M),(7*M,5*M))
assert inspect(b,d,chain={uid(branch)})=={uid(spur)};result['replaced-chain-contact-allowed']=True

b,d=fixture();spur=track(b,(5*M,5*M),(5700000,5*M));retained=track(b,(5*M,5*M),(5*M,7*M))
assert inspect(b,d)=={uid(spur)};result['retained-branch-direct-pad-contact']=True

b,d=fixture();plain=track(b,(5*M,5*M),(5700000,5*M));locked=track(b,(5*M,5*M),(4300000,5*M),locked=True);protected=track(b,(5*M,5*M),(5*M,5100000));foreign=track(b,(5*M,5*M),(5*M,4900000),net='foreign');opposite=track(b,(5*M,5*M),(5100000,5*M),face=p.F_Cu)
v=p.PCB_VIA(b);v.SetPosition(p.VECTOR2I(5*M,5*M));v.SetNet(b.FindNet('signal'));v.SetWidth(500000);v.SetDrill(200000);v.SetViaType(p.VIATYPE_THROUGH);v.SetLayerPair(p.F_Cu,p.B_Cu);b.Add(v)
assert inspect(b,d,protected={uid(protected)})=={uid(plain)};result['locked-protected-via-foreign-face-retained']=True

b,d=fixture();long=track(b,(5*M,5*M),(7*M,5*M));assert inspect(b,d)==set();result['bounded-local-candidates-only']=True

b,d=fixture();safe=track(b,(5*M,5*M),(5700000,5*M));blocked=track(b,(5*M,5*M),(5*M,5200000));external=track(b,(5*M,5325000),(5*M,7*M))
assert inspect(b,d)=={uid(safe)};result['rejected-candidate-keeps-independent-pad-contact']=True

# A rotated real land uses Native shapes, never board-axis pad-size assumptions.
b,d=fixture();d.SetOrientationDegrees(90);spur=track(b,(5*M,5*M),(5*M,5700000));assert inspect(b,d)=={uid(spur)};result['rotated-land']=True

output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
