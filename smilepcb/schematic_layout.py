"""Presentation-only symbol envelopes and shelf packing, independent of PCB geometry."""
from copy import deepcopy
import math

from . import schematic as s

STUB = 5.08
GAP = 7.62


def pin_exit(pin):
    at=s.one(pin,'at');angle=float(at[3]);r=math.radians(angle)
    return (round(at[1]-STUB*math.cos(r),6),round(-at[2]+STUB*math.sin(r),6),(angle+180)%360)


def annotation_bounds(part, embedded):
    points=[]
    for unit in s.children(embedded,'symbol'):
        for graphic in unit[2:]:
            for kind in ('start','end','center','at'):
                node=s.one(graphic,kind)
                if node is not None:points.append((float(node[1]),-float(node[2])))
            pts=s.one(graphic,'pts')
            if pts:points.extend((float(p[1]),-float(p[2])) for p in s.children(pts,'xy'))
            if s.tag(graphic)=='circle':
                c=s.one(graphic,'center');radius=s.one(graphic,'radius')[1]
                points.extend([(c[1]-radius,-c[2]-radius),(c[1]+radius,-c[2]+radius)])
        for pin in s.children(unit,'pin'):
            number=str(s.one(pin,'number')[1])
            if number not in part['nets'] or number in part.get('no_connect',[]):continue
            x,y,angle=pin_exit(pin)
            length=2.54+.8*len(part['nets'][number])
            dx,dy=round(math.cos(math.radians(angle))),-round(math.sin(math.radians(angle)))
            points.extend([(x-1.27,y-1.27),(x+1.27,y+1.27),
                           (x+dx*length-1.27,y+dy*length-1.27),
                           (x+dx*length+1.27,y+dy*length+1.27)])
    if not points:points=[(-2.54,-2.54),(2.54,2.54)]
    return [min(p[0] for p in points),min(p[1] for p in points),
            max(p[0] for p in points),max(p[1] for p in points)]


def property_offsets(part, embedded):
    top=annotation_bounds(part,embedded)[1]
    return {'Reference':s.snap(top-7.62),'Value':s.snap(top-3.81),'Footprint':0}


def part_envelope(part, embedded):
    bounds=annotation_bounds(part,embedded)
    for name,value in [('Reference',part['ref']),('Value',part['value'])]:
        half=.4*len(value)+1.27;y=property_offsets(part,embedded)[name]
        bounds=[min(bounds[0],-half),min(bounds[1],y-1.27),max(bounds[2],half),max(bounds[3],y+1.27)]
    return bounds


def arrange_parts(parts, symbols, *, width_mm=564):
    """Pack actual annotated symbol envelopes; never mutate electrical/PCB fields."""
    result=[];left=20;top=20;row_height=0
    for source in parts:
        p=deepcopy(source)
        embedded=symbols.get(p['symbol']) or s.symbol(p['symbol'])
        bounds=part_envelope(p,embedded)
        width=bounds[2]-bounds[0];height=bounds[3]-bounds[1]
        if width>width_mm:raise ValueError('Annotated symbol exceeds schematic sheet width')
        if left>20 and left+width>20+width_mm:
            left=20;top+=row_height+GAP;row_height=0
        x=s.snap(left-bounds[0]+1.27);y=s.snap(top-bounds[1]+1.27)
        p['sch']=[x,y];result.append(p)
        left=x+bounds[2]+GAP;row_height=max(row_height,y+bounds[3]-top)
    return result


def arrange_sections(sections, symbols, *, width_mm):
    result=[];headings=[];top=20
    for title,parts in sections:
        if not parts:continue
        headings.append((title,20,s.snap(top)))
        placed=arrange_parts(parts,symbols,width_mm=width_mm)
        shift=s.snap(top+10.16-20)
        for part in placed:part['sch'][1]=s.snap(part['sch'][1]+shift)
        result.extend(placed)
        bottom=max(part['sch'][1]+part_envelope(part,symbols.get(part['symbol']) or s.symbol(part['symbol']))[3] for part in placed)
        top=bottom+15.24
    return result,headings
