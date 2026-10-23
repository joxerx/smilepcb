"""Strict, bounded KLE JSON geometry plus an explicit electrical sidecar.

The supported subset follows KLE's relative x/y cursor semantics. It does not
interpret legends as firmware, execute raw-data JavaScript, or fetch resources.
"""
from decimal import Decimal, DecimalException, Context, localcontext
import hashlib
import json
import re

MAX_BYTES=65536
COSMETIC={'c','t','p','a','f','f2','fa','n'}
META={'name','author','notes','backcolor','radii'}


def document(text):
    if not isinstance(text,str) or len(text.encode('utf-8'))>MAX_BYTES:
        raise ValueError('KLE and matrix JSON must each fit within 64 KiB')
    def pairs(entries):
        result={}
        for key,value in entries:
            if key in result:raise ValueError('Duplicate JSON property: '+key)
            result[key]=value
        return result
    def invalid(value):raise ValueError('Non-finite JSON number: '+value)
    def decimal_token(value):
        if len(value)>64:raise ValueError('Numeric token is too long')
        d=Decimal(value)
        if not d.is_finite() or d.copy_abs()>100 or d.as_tuple().exponent < -6:
            raise ValueError('Numbers require at most six fractional decimal places and magnitude 100')
        return d
    try:return json.loads(text,parse_float=decimal_token,parse_constant=invalid,object_pairs_hook=pairs)
    except (json.JSONDecodeError,RecursionError,DecimalException) as error:raise ValueError('Expected complete strict JSON') from error


def number(value):
    if type(value) not in (int,Decimal) or not Decimal(value).is_finite() or Decimal(value).copy_abs()>100:
        raise ValueError('Coordinates must be finite numbers within 100u')
    return Decimal(value)


def source_files(source):
    if not isinstance(source,dict) or set(source)!={'kle_json','matrix_json'}:
        raise ValueError('Expected explicit KLE and matrix JSON sources')
    return {'layout.kle.json':source['kle_json'],'layout.matrix.json':source['matrix_json']}


def parse(kle_json,matrix_json):
    with localcontext(Context(prec=32)):
        try:return _parse(kle_json,matrix_json)
        except DecimalException as error:raise ValueError('Unsupported numeric precision') from error


def _parse(kle_json,matrix_json):
    rows=document(kle_json);mapping=document(matrix_json)
    if not isinstance(rows,list) or not rows or len(rows)>32:
        raise ValueError('Expected a bounded KLE row array')
    if not isinstance(mapping,dict) or set(mapping)!={'rows','cols','matrix','keycodes'}:
        raise ValueError('Matrix sidecar requires rows, cols, matrix and keycodes')
    nr,nc=mapping['rows'],mapping['cols']
    if type(nr) is not int or type(nc) is not int or not 1<=nr<=4 or not 1<=nc<=5:
        raise ValueError('Matrix exceeds the selected controller profile')
    count=nr*nc;cells=mapping['matrix'];codes=mapping['keycodes']
    if (not isinstance(cells,list) or len(cells)!=count or
            any(not isinstance(c,list) or len(c)!=2 or any(type(v) is not int for v in c) for c in cells) or
            {tuple(c) for c in cells}!={(r,c) for r in range(nr) for c in range(nc)}):
        raise ValueError('Matrix cells must be complete, unique and in range')
    if (not isinstance(codes,list) or len(codes)!=count or
            any(not isinstance(c,str) or not re.fullmatch(r'KC_[A-Z0-9_]{1,32}',c) for c in codes)):
        raise ValueError('Provide one safe QMK KC_ identifier for each key')
    cursor_y=Decimal(0);geometry=[]
    for ri,row in enumerate(rows):
        if isinstance(row,dict):
            if ri!=0 or set(row)-META or any(not isinstance(v,str) or len(v)>4096 for v in row.values()):
                raise ValueError('Unsupported KLE metadata')
            continue
        if not isinstance(row,list) or not row or not isinstance(row[-1],str):
            raise ValueError('Every KLE row must end with a key, not a property')
        cursor_x=Decimal(0)
        for token in row:
            if isinstance(token,str):
                number(cursor_x);number(cursor_y)
                if len(token)>256:raise ValueError('Key legend is too long')
                geometry.append((cursor_x,cursor_y,token))
                if len(geometry)>20:raise ValueError('At most 20 keys are supported')
                cursor_x+=1
                continue
            if not isinstance(token,dict) or set(token)-({'x','y','w','h'}|COSMETIC):
                raise ValueError('Unsupported KLE property: rotations, compound shapes and hardware overrides are not supported')
            for name,value in token.items():
                if name in ('x','y','w','h'):
                    value=number(value)
                    if name in ('w','h') and value!=1:raise ValueError('Only rectangular 1u MX keys are supported')
                    if name=='x':cursor_x+=value
                    if name=='y':cursor_y+=value
                elif name in ('c','t','p'):
                    if not isinstance(value,str) or len(value)>256:raise ValueError('Invalid cosmetic string')
                elif name=='n':
                    if type(value) is not bool:raise ValueError('Invalid nub flag')
                elif name=='fa':
                    if not isinstance(value,list) or len(value)>12:raise ValueError('Invalid legend sizes')
                    for v in value:number(v)
                else:number(value)
            number(cursor_x);number(cursor_y)
        cursor_y+=1
    if len(geometry)!=count:raise ValueError('KLE key count does not match the explicit matrix')
    origin=(min(v[0] for v in geometry),min(v[1] for v in geometry));keys=[]
    for i,((x,y,label),(r,c),code) in enumerate(zip(geometry,cells,codes)):
        x-=origin[0];y-=origin[1]
        if x>6 or y>4:raise ValueError('Layout exceeds the bounded mechanical envelope')
        nm=[v*19050000 for v in (x,y)]
        if any(v!=v.to_integral_value() for v in nm):raise ValueError('Key coordinate is finer than the native nanometre grid')
        keys.append({'row':r,'col':c,'x':float(nm[0]/1000000),'y':float(nm[1]/1000000),
                     'x_u':float(x),'y_u':float(y),'label':label,'keycode':code})
    for i,(x,y,_) in enumerate(geometry):
        if any(abs(x-ox)<1 and abs(y-oy)<1 for ox,oy,_ in geometry[:i]):
            raise ValueError('Overlapping 1u key envelopes')
    physical_rows=[]
    for r in range(nr):
        members=[(i,k) for i,k in enumerate(keys,1) if k['row']==r]
        ys={k['y_u'] for _,k in members}
        if len(ys)!=1:raise ValueError('Each electrical row must be one horizontal physical row')
        physical_rows.append((ys.pop(),members))
    if any(physical_rows[i][0]>=physical_rows[i+1][0] for i in range(nr-1)):
        raise ValueError('Explicit matrix rows must follow physical top-to-bottom order')
    chain=[]
    for r,(_,members) in enumerate(physical_rows):
        chain.extend(i for i,k in sorted(members,key=lambda pair:pair[1]['x_u'],reverse=bool(r%2)))
    return {'cols':nc,'rows':nr,'keys':keys,'rgb_order':chain,'origin_u':[float(v) for v in origin],
            'input_sha256':{'layout.kle.json':hashlib.sha256(kle_json.encode()).hexdigest(),
                            'layout.matrix.json':hashlib.sha256(matrix_json.encode()).hexdigest()}}
