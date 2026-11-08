"""Pure HOST bounds against emitted exchange geometry, not KiCad mocks."""
import importlib.util,math,re
import pytest


def api():
    name='smilepcb.keyboard_routing.exchange_pad_envelope'
    assert importlib.util.find_spec(name)is not None,'Foreign PAD enclosure math is missing'
    return __import__(name,fromlist=['enclosing_octagon'])


def inside(poly,point):
    return all((b[0]-a[0])*(point[1]-a[1])-(b[1]-a[1])*(point[0]-a[0])>=-1e-5
               for a,b in zip(poly,poly[1:]+poly[:1]))


def imported_octagon(points):
    # Independent clipping of the pinned tree's eight integer halfplanes.
    lo=min(min(x,y)for x,y in points)-1;hi=max(max(x,y)for x,y in points)+1
    poly=[(lo,lo),(hi,lo),(hi,hi),(lo,hi)]
    for nx,ny in((1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1),(0,-1),(1,-1)):
        bound=max(nx*x+ny*y for x,y in points);out=[]
        for a,b in zip(poly,poly[1:]+poly[:1]):
            da=nx*a[0]+ny*a[1]-bound;db=nx*b[0]+ny*b[1]-bound
            if da<=0:out.append(a)
            if(da<0<db)or(db<0<da):
                f=da/(da-db);out.append((a[0]+f*(b[0]-a[0]),a[1]+f*(b[1]-a[1])))
        poly=out
    return poly


@pytest.mark.parametrize('bounds,want',[
    ((-2000000,-2000000,287750000,100000000),100),
    ((-671088634,-1000000,1000000,1000000),100),
    ((-671088635,-1000000,1000000,1000000),1000),
    ((-700000000,-2000000,-1000000,-1000000),1000),
])
def test_import_scale_bound_includes_signed_board_extent_and_format_rounding(bounds,want):
    assert api().import_grid_nm(bounds)==want

@pytest.mark.parametrize('bounds',[(0,0,0,0),(0,0,True,10),(0,0,1.,10),(-7000000000,0,1,10)])
def test_invalid_or_zero_import_scale_fails_closed(bounds):
    with pytest.raises(ValueError):api().import_grid_nm(bounds)

@pytest.mark.parametrize('kind,size,corner,want',[
    ('circle',(3300000,3300000),0,2020),('circle',(1000000,1000000),0,633),
    ('rect',(500000,300000),0,30),('oval',(900000,300000),0,30),
    ('trapezoid',(500000,300000),0,30),('custom',(500000,300000),0,30),
    ('roundrect',(1000000,800000),250000,1377),('chamfered_rect',(1000000,800000),250000,1377),
])
def test_exporter_shape_specific_growth_bound(kind,size,corner,want):
    assert api().export_excess_nm(kind,size,corner)==want

@pytest.mark.parametrize('grid,want',[(100,454),(1000,4536)])
def test_all_five_coordinate_roundings_and_support_rounding_are_bounded(grid,want):
    assert api().quantization_excess_nm(grid)==want

@pytest.mark.parametrize('radius',[500000,1650000])
@pytest.mark.parametrize('angle',[-173,0,17,90])
def test_envelope_contains_actual64gon_rebounded_after_pose_rounding(radius,angle):
    from smilepcb.keyboard_exchange import polygonize_circles
    text='(pcb "math" (unit um) (structure) (library (padstack "pad" (shape (circle B.Cu '+str(radius*2/1000)+' 0 0)))) (wiring))'
    rendered,_=polygonize_circles(text);values=list(map(float,re.search(r'\(polygon B.Cu 0 ([^()]+)',rendered)[1].split()))
    local=[(x*1000,y*1000)for x,y in zip(values[::2],values[1::2])]
    theta=math.radians(angle);c,s=math.cos(theta),math.sin(theta)
    rotate=lambda q:(c*q[0]-s*q[1],s*q[0]+c*q[1])
    q=lambda value:math.floor(value/100+.5)*100
    offset=(-100025,399975);origin=(-1000025,-2000025)
    center=rotate(offset);center=(center[0]+origin[0],center[1]+origin[1])
    native=[(round(center[0]+(radius/math.cos(math.pi/64)+2)*math.cos((2*i+1)*math.pi/64)),
             round(center[1]+(radius/math.cos(math.pi/64)+2)*math.sin((2*i+1)*math.pi/64)))for i in range(64)]
    imported=[]
    for point in local:
        point=(q(point[0]),q(point[1]));point=rotate(point);point=(q(point[0]),q(point[1]))
        imported.append((point[0]+q(rotate((q(offset[0]),q(offset[1])))[0])+q(origin[0]),
                         point[1]+q(rotate((q(offset[0]),q(offset[1])))[1])+q(origin[1])))
    extra=api().export_excess_nm('circle',(radius*2,radius*2))+api().quantization_excess_nm(100)
    envelope=api().enclosing_octagon(native,extra)
    assert all(inside(envelope,p)for p in native)
    assert all(inside(envelope,p)for p in imported_octagon(imported))


def test_tiny_compound_outline_is_enclosed_without_circle_recasting():
    points=[(-3,-2),(-1,-2),(-1,-1),(-3,-1),(2,1),(4,1),(4,3),(2,3)]
    envelope=api().enclosing_octagon(points,0)
    assert all(inside(envelope,p)for p in points)
    assert all(type(v)is int for p in envelope for v in p)

@pytest.mark.parametrize('points,extra',[((),0),([(0,0),(1,1),(2,2)],0),([(0,0),(1,0),(0,1)],True),([(0,0),(1.,0),(0,1)],1),([(0,0),(1,0),(0,1)],-1)])
def test_invalid_empty_or_degenerate_enclosure_fails_closed(points,extra):
    with pytest.raises(ValueError):api().enclosing_octagon(points,extra)


def test_unknown_exporter_shape_cannot_silently_use_native_circle():
    with pytest.raises(ValueError):api().export_excess_nm('future-shape',(1000,1000))
