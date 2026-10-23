"""Validate the supported two-layer SES via inventory before native promotion."""
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import re


def _session_tree(text):
    """Read SES lists with stdlib only, including bare bracketed padstack IDs.

    The native KiCad interpreter has no application site-packages. Keep atom
    values as strings so resolution conversion does not lose decimal digits.
    """
    tokens=re.finditer(r'\s+|"(?:\\.|[^"\\])*"|[()]|[^()\s"]+',text)
    root=[];stack=[root];end=0
    for token in tokens:
        if token.start()!=end:raise ValueError('Invalid SES token')
        end=token.end();value=token[0]
        if value.isspace():continue
        if value=='(':
            child=[];stack[-1].append(child);stack.append(child)
        elif value==')':
            if len(stack)==1:raise ValueError('Unbalanced SES')
            stack.pop()
        else:stack[-1].append(json.loads(value) if value.startswith('"') else value)
    if end!=len(text) or len(stack)!=1 or len(root)!=1 or not isinstance(root[0],list):
        raise ValueError('Expected one balanced SES')
    if not root[0] or root[0][0]!='session':raise ValueError('Expected SES session')
    return root[0]


def session_via_contacts(text):
    """Return a multiset of (net, x_nm, y_nm), rejecting unsupported padstacks."""
    tree=_session_tree(text)
    def child(node,head):
        found=[v for v in node if isinstance(v,list) and v and str(v[0])==head]
        if len(found)!=1:raise ValueError('Expected one SES '+head)
        return found[0]
    routes=child(tree,'routes');resolution=child(routes,'resolution')
    units={'um':1000,'mm':1000000,'mil':25400,'inch':25400000}
    if len(resolution)!=3 or str(resolution[1]) not in units:raise ValueError('Unsupported SES resolution')
    try:scale=Decimal(units[str(resolution[1])])/Decimal(str(resolution[2]))
    except (InvalidOperation,ZeroDivisionError):raise ValueError('Invalid SES resolution') from None
    if not scale.is_finite() or scale<=0:raise ValueError('Invalid SES scale')
    library=child(routes,'library_out');stacks={}
    for node in library[1:]:
        if not isinstance(node,list) or len(node)<2 or str(node[0])!='padstack':raise ValueError('Unsupported SES library item')
        name=str(node[1])
        if name in stacks:raise ValueError('Duplicate SES padstack')
        stacks[name]=node
    def nm(value):
        try:scaled=Decimal(str(value))*scale
        except InvalidOperation:raise ValueError('Invalid SES coordinate') from None
        if not scaled.is_finite() or abs(scaled)>2_000_000_000:raise ValueError('SES coordinate exceeds supported native range')
        return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
    result=Counter()
    for net in child(routes,'network_out')[1:]:
        if not isinstance(net,list) or len(net)<2 or str(net[0])!='net':raise ValueError('Unsupported SES network item')
        for via in net[2:]:
            if not isinstance(via,list) or not via or str(via[0])!='via':continue
            if len(via)<4 or str(via[1]) not in stacks:raise ValueError('Unknown SES via padstack')
            shapes=[v[1] for v in stacks[str(via[1])][2:] if isinstance(v,list) and v and str(v[0])=='shape' and len(v)==2]
            if len(shapes) not in (1,2) or any(not isinstance(v,list) or len(v) not in (3,5) or str(v[0])!='circle' or str(v[1]) not in ('F.Cu','B.Cu','signal') for v in shapes):
                raise ValueError('Native import requires circular through-via padstacks')
            diameters=[nm(v[2]) for v in shapes]
            if any(d<=0 for d in diameters) or len(set(diameters))!=1:
                raise ValueError('Native through-via requires one positive uniform diameter')
            if len(shapes)==2 and {str(v[1]) for v in shapes}!={'F.Cu','B.Cu'}:
                raise ValueError('Native through-via requires distinct outer layers')
            if any(len(v)==5 and (nm(v[3])!=0 or nm(v[4])!=0) for v in shapes):
                raise ValueError('Native through-via requires centered circles')
            coordinates=[]
            for value in via[2:]:
                if isinstance(value,list):break
                coordinates.append(value)
            if not coordinates or len(coordinates)%2:raise ValueError('Malformed SES via positions')
            for x,y in zip(coordinates[::2],coordinates[1::2]):result[str(net[1]),nm(x),-nm(y)]+=1
    return result


def require_via_inventory(expected,actual):
    """Do not trust native success when unsupported session items were skipped."""
    if expected!=Counter(actual):raise ValueError('Native session import lost or changed the expected via inventory')
