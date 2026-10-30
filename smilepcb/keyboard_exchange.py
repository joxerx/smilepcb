"""Data-checked native NPTH compensation in the temporary router exchange."""
from decimal import Decimal
import re
import math

NPTH_EXTRA_UM=10
CIRCLE_SIDES=64
CIRCLE_ROUNDING_GUARD_UM=.02


def npth_exchange_clearance_nm(native_clearance_nm,hole_diameter_nm,*,router_clearance_nm=150000):
    """Generated circular-hole gap enclosing DSN compensation and rounding."""
    if any(type(v) is not int or v<=0 for v in (native_clearance_nm,hole_diameter_nm,router_clearance_nm)):
        raise ValueError("Expected positive integer native hole/exchange dimensions")
    physical=native_clearance_nm+NPTH_EXTRA_UM*1000
    if router_clearance_nm>=physical:raise ValueError("Unsupported NPTH exchange clearance policy")
    radius=hole_diameter_nm/2+physical-router_clearance_nm
    enclosure=math.ceil(radius*(1/math.cos(math.pi/CIRCLE_SIDES)-1)+CIRCLE_ROUNDING_GUARD_UM*1000)
    return physical+enclosure+1000


def adapt_npth_keepouts(text, *, holes_um, native_hole_clearance_um, router_clearance_um):
    holes={Decimal(str(v)) for v in holes_um}
    native=Decimal(str(native_hole_clearance_um)); gap=Decimal(str(router_clearance_um))
    physical=native+NPTH_EXTRA_UM
    if not holes or native<=0 or gap<=0 or gap>=physical:
        raise ValueError('Unsupported NPTH exchange clearance policy')
    count=0
    def convert(match):
        nonlocal count
        exported=Decimal(match[2]); hole=exported-2*native
        if min(abs(hole-actual) for actual in holes)>Decimal('.1'):
            raise ValueError('Exported circular keepout does not match a declared native NPTH hole')
        count+=1
        diameter=hole+2*(physical-gap)
        return match[1]+format(diameter.normalize(),'f')
    result=re.sub(r'(\(keepout "" \(circle (?:F|B)\.Cu )([0-9.]+)(?=[ )])',convert,text)
    return result,{'adjusted_circles':count,'physical_hole_clearance_um':float(physical),
                   'native_hole_clearance_um':float(native),'router_clearance_um':float(gap)}


def _via_padstack_ranges(text):
    """Find via definitions by references, independently of padstack names."""
    import json
    tokens=list(re.finditer(r'\(string_quote "\)|"(?:\\.|[^"\\])*"|[()]|[^()\s]+',text))
    def atom(token):
        value=token[0]
        return json.loads(value) if value.startswith('"') else value
    refs=set();stack=[];declarations=[]
    for i,token in enumerate(tokens):
        if token[0]=='(':
            head=tokens[i+1][0] if i+1<len(tokens) else ''
            name=atom(tokens[i+2]) if head=='padstack' and i+2<len(tokens) else None
            stack.append((token.start(),head,name,i))
        elif token[0]==')':
            if not stack:raise ValueError('Unbalanced DSN exchange')
            start,head,name,opened=stack.pop()
            if head=='padstack':declarations.append((name,start,token.end()))
            if head=='via':
                # Structure lists every selectable padstack. Wiring instances
                # name just one, followed by coordinates and attributes.
                ids=tokens[opened+2:i] if stack and stack[-1][1]=='structure' else tokens[opened+2:opened+3]
                for item in ids:
                    if item[0] in ('(',')'):raise ValueError('Unsupported via declaration')
                    refs.add(atom(item))
    if stack or refs-{name for name,a,z in declarations}:raise ValueError('Missing or unbalanced via padstack declaration')
    return [(a,z) for name,a,z in declarations if name in refs]


def polygonize_circles(text):
    """Avoid coarse circle-to-octagon obstacles in Freerouting's tile tree.

    Sixty-four tangent edges conservatively enclose the original circle.
    A 0.02 um radial guard also bounds rounding to the 0.01 um exchange grid.
    Via padstacks stay circular: the native SES importer requires circles.
    This changes the temporary exchange only, not native copper or clearances.
    """
    import math
    if '(unit um)' not in text:raise ValueError('Circle exchange requires micron units')
    count=0;excess=0;via_ranges=_via_padstack_ranges(text)
    number=r'-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)'
    pattern=r'\(string_quote "\)|"(?:\\.|[^"\\])*"|\(circle ([FB]\.Cu) ('+number+r')(?: ('+number+r') ('+number+r'))?\)'
    def convert(match):
        nonlocal count,excess
        if match[1] is None or any(a<=match.start()<z for a,z in via_ranges):return match[0]
        diameter=float(match[2]);x=float(match[3] or 0);y=float(match[4] or 0)
        if not diameter>0:raise ValueError('Invalid exchange circle diameter')
        radius=diameter/2;outer=radius/math.cos(math.pi/CIRCLE_SIDES)+CIRCLE_ROUNDING_GUARD_UM
        points=[]
        for i in range(CIRCLE_SIDES):
            angle=(2*i+1)*math.pi/CIRCLE_SIDES
            points.extend((f'{x+outer*math.cos(angle):.2f}',f'{y+outer*math.sin(angle):.2f}'))
        count+=1;excess=max(excess,outer-radius+.01)
        return '(polygon '+match[1]+' 0 '+' '.join(points)+')'
    result=re.sub(pattern,convert,text)
    return result,{'converted_circles':count,'sides':CIRCLE_SIDES,'max_radial_excess_um':excess,
                   'conservative_enclosure':True}


def guard_milled_edges(text, *, native_edge_um):
    """Give native cutouts a separate conservative router clearance class.

    KiCad exports inner Edge.Cuts loops as all-layer polygon keepouts, but
    omits the distinct copper-to-edge minimum. Applying the ordinary wire
    clearance to these polygons can route legal DSN copper across a native
    edge violation. Circular layer-specific NPTH compensation is separate.
    """
    import math
    import sexpdata as sx
    if '(unit um)' not in text or isinstance(native_edge_um,bool) or not isinstance(native_edge_um,(int,float)) or not math.isfinite(native_edge_um) or not 0<native_edge_um<=5000:
        raise ValueError('Expected a bounded native micron edge clearance')
    if 'SmilePCBEdge' in text:raise ValueError('Edge exchange guard already exists')
    start=text.index('\n  (structure');end=text.index('\n  (placement',start)
    structure=sx.loads(text[start:end]);count=0;boundary_count=0
    label=sx.Symbol('SmilePCBEdge')
    rule=next(node for node in structure[1:] if str(node[0])=='rule')
    clearance=math.ceil(native_edge_um+50)
    for kind in ('wire','via'):
        rule.append([sx.Symbol('clearance'),clearance,[sx.Symbol('type'),sx.Symbol(kind+'_SmilePCBEdge')]])
    for node in structure[1:]:
        if str(node[0])=='boundary':
            node.append([sx.Symbol('clearance_class'),label]);boundary_count+=1
        elif str(node[0])=='keepout' and len(node)>=3 and isinstance(node[2],list) and str(node[2][0])=='polygon' and str(node[2][1])=='signal':
            node.append([sx.Symbol('clearance_class'),label]);count+=1
    if not boundary_count:raise ValueError('Missing native outer boundary')
    return text[:start]+'\n  '+sx.dumps(structure)+text[end:],{
        'guarded_milled_regions':count,'guarded_boundaries':boundary_count,
        'native_edge_um':native_edge_um,'exchange_edge_clearance_um':clearance}
