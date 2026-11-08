"""Choose opposite matrix faces from actual pad access, not MCU orientation."""

def select_matrix_layers(terminals,*,faces,preferred_row_layer):
    faces=tuple(faces)
    if len(faces)!=2 or len(set(faces))!=2 or preferred_row_layer not in faces:
        raise ValueError('Two different faces and one previous preference required')
    roots={}
    for i,q in enumerate(terminals):
        layers=set(q['layers'])
        if q['family'] not in ('rows','columns') or not layers or not layers<=set(faces):
            raise ValueError('Physical matrix family and external pad access required')
        key=(q['family'],('root',q['root']) if 'root' in q else ('pad',i))
        roots.setdefault(key,set()).update(layers)
    rows=[(family,layers) for (family,_),layers in roots.items()]
    candidates=[]
    for row in faces:
        column=next(q for q in faces if q!=row)
        misses={family:sum(face not in layers for f,layers in rows if f==family)
            for family,face in [('rows',row),('columns',column)]}
        candidates.append(dict(row_layer=row,column_layer=column,pad_layer_misses=sum(misses.values()),by_family=misses))
    selected=min(candidates,key=lambda q:(q['pad_layer_misses'],q['row_layer']!=preferred_row_layer))
    return dict(selected,candidates=candidates,scope='non-controller matrix copper-root layer access heuristic')


def select_board_matrix_layers(board,cfg,families,*,faces,preferred_row_layer,deadline):
    """Score each real connected field terminal once, using all its faces."""
    parts={q['ref']:q for q in cfg['parts']};pad_ids={}
    for footprint in board.GetFootprints():
        if parts.get(footprint.GetReference(),{}).get('region')=='controller':continue
        for pad in footprint.Pads():
            if pad.GetNumber() and pad.GetNetname() in families:
                pad_ids[pad.m_Uuid.AsString()]=families[pad.GetNetname()]
    terminals=[]
    if pad_ids:
        from .native_graph import copper_graph
        graph=copper_graph(board,set(families),deadline,include_zones=False)
        for root,indices in graph['groups'].items():
            families_here={pad_ids[n['item'].m_Uuid.AsString()] for i in indices
                if (n:=graph['nodes'][i])['item'].m_Uuid.AsString() in pad_ids}
            if not families_here:continue
            if len(families_here)!=1:raise ValueError('Matrix root crosses physical families')
            layers=sorted({face for i in indices for face in graph['nodes'][i]['shapes'] if face in faces})
            terminals.append(dict(family=next(iter(families_here)),root=root,layers=layers))
    result=select_matrix_layers(terminals,faces=faces,preferred_row_layer=preferred_row_layer)
    result['field_root_count']=len(terminals)
    return result
