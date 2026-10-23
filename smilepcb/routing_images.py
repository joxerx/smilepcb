"""Content-based native DSN images, independent of exporter alias allocation."""
from copy import deepcopy
import hashlib
import sexpdata as sx


def _outline(node):
    result=deepcopy(node)
    if len(result)!=2 or not isinstance(result[1],list):raise ValueError('Unsupported image outline')
    shape=result[1];kind=str(shape[0])
    if kind not in ('path','polygon'):return result
    coordinates=shape[3:]
    if len(coordinates)%2 or len(coordinates)<(6 if kind=='polygon' else 4):
        raise ValueError('Unsupported image outline coordinates')
    points=list(zip(coordinates[::2],coordinates[1::2]))
    if kind=='path':
        points=min(points,list(reversed(points)))
    else:
        if points[0]==points[-1]:points.pop()
        # Only non-copper outline polygons are traversed differently. Their
        # vertices remain exact; pin/padstack/keepout shapes are untouched.
        variants=[order[i:]+order[:i] for order in (points,list(reversed(points))) for i in range(len(points))]
        points=min(variants)
    shape[3:]=[coordinate for point in points for coordinate in point]
    return result


def normalize_images(library,placement):
    aliases={};images={};other=[]
    for image in library[1:]:
        if str(image[0])!='image':other.append(image);continue
        original=str(image[1])
        if original in aliases:raise ValueError('Duplicate native image identifier')
        children=[]
        for node in image[2:]:
            if not isinstance(node,list) or str(node[0]) not in ('outline','pin','keepout'):
                raise ValueError('Unsupported native image declaration')
            children.append(_outline(node) if str(node[0])=='outline' else deepcopy(node))
        children.sort(key=lambda node:(str(node[0]),sx.dumps(node)))
        payload=sx.dumps([sx.Symbol('image'),*children])
        name='SmilePCBImage_'+hashlib.sha256(payload.encode()).hexdigest()
        normalized=[sx.Symbol('image'),sx.Symbol(name),*children]
        if name in images and images[name]!=normalized:raise ValueError('Native image identity collision')
        aliases[original]=name;images[name]=normalized
    groups={};references=set()
    for component in placement[1:]:
        if str(component[0])!='component' or str(component[1]) not in aliases:
            raise ValueError('Placement references an unknown native image')
        name=aliases[str(component[1])];places=groups.setdefault(name,[])
        for place in component[2:]:
            if str(place[0])!='place' or str(place[1]) in references:
                raise ValueError('Duplicate or unsupported native image placement')
            references.add(str(place[1]));places.append(place)
    library[1:]=[*other,*images.values()]
    placement[1:]=[[sx.Symbol('component'),sx.Symbol(name),*sorted(places,key=lambda node:str(node[1]))]
                  for name,places in sorted(groups.items())]
    return {'aliases':aliases,'before_images':len(aliases),'after_images':len(images),
            'placed_components':len(references)}
