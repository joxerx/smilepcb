"""HOST index boundary; native coupled physics is a separate explicit gate."""
import ast,math,sys
from pathlib import Path
from types import SimpleNamespace
import pytest

class Box:
    def __init__(self,points):self.points=points
    def GetLeft(self):return min(x for x,y in self.points)
    def GetRight(self):return max(x for x,y in self.points)
    def GetTop(self):return min(y for x,y in self.points)
    def GetBottom(self):return max(y for x,y in self.points)
class Poly:
    def __init__(self):self.points=[]
    def NewOutline(self):return 0
    def Append(self,v):self.points.append((v.x,v.y))
    def BBox(self):return Box(self.points)
    def OutlineCount(self):return 1 if self.points else 0
    def HoleCount(self,i):return 0
    def COutline(self,i):return self
    def PointCount(self):return len(self.points)
    def CPoint(self,i):return SimpleNamespace(x=self.points[i][0],y=self.points[i][1])
    def IsSelfIntersecting(self):return len(self.points)>1 and self.points[0]==self.points[-1]
    def ArcCount(self):return 0
    def Simplify(self):
        if len(self.points)>1 and self.points[0]==self.points[-1]:self.points.pop()
class Circle:
    def __init__(self,r):self.radius=r
    def BBox(self):return Box([(-self.radius,-self.radius),(self.radius,self.radius)])
class Pad:
    def __init__(self,*,face=2,attribute=1,radius=1650000):
        self.face=face;self.attribute=attribute;self.radius=radius;self.native=Circle(radius);self.transforms=[]
        self.m_Uuid=SimpleNamespace(AsString=lambda:'physical-pad')
    def IsOnLayer(self,face):return face==self.face
    def GetEffectiveShape(self,face):return self.native
    def GetAttribute(self):return self.attribute
    def GetNetname(self):return 'pad-net'
    def GetOwnClearance(self,face):return 150000
    def GetDrillSize(self):return SimpleNamespace(x=0,y=0)
    def GetShape(self,*args):return 0
    def GetSize(self,*args):return SimpleNamespace(x=self.radius*2,y=self.radius*2)
    def Padstack(self):return SimpleNamespace(Mode=lambda:0)
    def TransformShapeToPolygon(self,poly,face,gap,error,side):
        self.transforms.append((face,gap,error,side))
        assert face==self.face and gap==0 and side==1
        # An independently constructed circumscribed64-gon is an outside polygon.
        for i in range(64):
            a=(2*i+1)*math.pi/64;r=self.radius/math.cos(math.pi/64)+2
            poly.points.append((round(r*math.cos(a)),round(r*math.sin(a))))

def indexed(profile,monkeypatch,*,face=2,attribute=1):
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/local_router.py'
    cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef)and n.name=='LocalRouter')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef)and n.name=='index')
    native=SimpleNamespace(F_Cu=0,B_Cu=2,PAD=Pad,PCB_VIA=type('Via',(),{}),PAD_ATTRIB_SMD=1,PAD_ATTRIB_NPTH=3,
        PAD_SHAPE_CIRCLE=0,PAD_SHAPE_RECT=1,PAD_SHAPE_OVAL=2,PAD_SHAPE_TRAPEZOID=3,PAD_SHAPE_ROUNDRECT=4,PAD_SHAPE_CHAMFERED_RECT=5,PAD_SHAPE_CUSTOM=6,
        PADSTACK=SimpleNamespace(MODE_NORMAL=0),SHAPE_POLY_SET=Poly,ERROR_OUTSIDE=1,
        VECTOR2I=lambda x,y:SimpleNamespace(x=x,y=y),FromMM=lambda value:round(value*1000000))
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',SimpleNamespace(p=native))
    env={'__name__':'smilepcb.keyboard_routing.local_router','__package__':'smilepcb.keyboard_routing','p':native,'own_clearance':lambda item,layer,floor:max(floor,item.GetOwnClearance(layer)),
         'uses_exchange_geometry':lambda generation:generation.name in('dsn-safe-v1','dsn-process-v2')}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),env)
    router=SimpleNamespace(buckets={0:{},2:{}},minimum_clearance=150000,net_clearances={},solder_lands={},holes={},
        hole_clearances={},native_hole_clearance=250000,generation=SimpleNamespace(name=profile),
        cells=lambda box,padding=0:[(0,0)],_pad_import_grid_nm=100,deadline=43)
    pad=Pad(face=face,attribute=attribute);env['index'](router,pad)
    return router,pad

def capsule_gap(shape,point,width):
    if isinstance(shape,Circle):return math.hypot(*point)-shape.radius-width/2
    # This witness lies outside the NE diagonal octagon edge, not near its corners.
    return (sum(point)-max(x+y for x,y in shape.points))/math.sqrt(2)-width/2

def test_v2_rejects_native_clear_foreign_capsule_after_import_pad_rebound(monkeypatch):
    router,pad=indexed('dsn-process-v2',monkeypatch)
    point=(991250,1645000);width=150000
    assert capsule_gap(pad.native,point,width)==pytest.approx(195573.23799432337)
    entry=router.buckets[2][0,0][0]
    assert capsule_gap(entry[1],point,width)<173000,'Imported PAD occupancy must reject a native-clear150um trace'
    assert len(router.buckets[2][0,0])==1 and entry[0]=='physical-pad'
    assert router.solder_lands[0,0][0][1]is pad.native
    assert router.deadline==43

@pytest.mark.parametrize('profile',['native','dsn-safe-v1'])
def test_earlier_profiles_retain_native_pad_occupancy(monkeypatch,profile):
    router,pad=indexed(profile,monkeypatch)
    assert router.buckets[2][0,0][0][1]is pad.native and pad.transforms==[]

@pytest.mark.parametrize('face',[0,2])
def test_npth_does_not_use_drill_item_pin_envelope(monkeypatch,face):
    router,pad=indexed('dsn-process-v2',monkeypatch,face=face,attribute=3)
    assert router.buckets[face][0,0][0][1]is pad.native and pad.transforms==[]
    assert router.buckets[2 if face==0 else 0]=={}


def test_native_repeated_closure_is_normalized_on_detached_polygon(monkeypatch):
    transform=Pad.TransformShapeToPolygon
    def repeated(pad,poly,*args):
        transform(pad,poly,*args);poly.points.append(poly.points[0])
    monkeypatch.setattr(Pad,'TransformShapeToPolygon',repeated)
    try:router,pad=indexed('dsn-process-v2',monkeypatch)
    except ValueError:pytest.fail('Native repeated closing vertex must be canonicalized, not rejected')
    assert capsule_gap(router.buckets[2][0,0][0][1],(991250,1645000),150000)<173000
    assert router.solder_lands[0,0][0][1]is pad.native


def test_curved_outward_polygon_is_refused_before_vertex_support_sampling(monkeypatch):
    monkeypatch.setattr(Poly,'ArcCount',lambda self:1)
    def unsafe_sampling(*args):pytest.fail('Curved contour was sampled as straight vertices')
    monkeypatch.setattr(Poly,'COutline',unsafe_sampling)
    with pytest.raises(ValueError,match='flat|arc|curv'):indexed('dsn-process-v2',monkeypatch)
