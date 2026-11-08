"""Apply chain-relative poses to the final native placement, before copper."""
import time

from smilepcb.led_orientation import chain_rotations, posed_offset


def native_data_offsets(project,*,p):
    """Read the materialized canonical recipe with the selected CAD runtime."""
    source=p.FootprintLoad(str(project/'KeyboardParts.pretty'),'MINI_E')
    if source is None:raise ValueError('Missing materialized MINI_E recipe')
    centre=source.GetPosition();pads={pad.GetNumber():pad for pad in source.Pads() if pad.GetNumber()}
    return tuple((pads[number].GetPosition().x-centre.x,pads[number].GetPosition().y-centre.y)
                 for number in ('3','2'))


def orient_final_led_chain(board,cfg,lookup,*,p,deadline,data_offsets):
    """Use actual pad geometry, not native backside angle conventions.

    This proposes only 0/180 rotations at fixed LED sites. The caller must
    rebuild its placement obstacles and admit the resulting native geometry.
    """
    order=cfg['layout']['rgb_order']
    if not order:return []
    parts={part['ref']:part for part in cfg['parts']}
    footprints={key:lookup['LED'+str(key)] for key in order}
    sides={board.GetLayerName(f.GetLayer()) for f in footprints.values()}
    if len(sides)!=1:raise ValueError('LED chain requires one physical copper face')
    side=sides.pop();offsets=data_offsets;positions={};current={}
    def position(obj):
        q=obj.GetPosition();return q.x,q.y
    for key,f in footprints.items():
        if time.monotonic()>=deadline:raise TimeoutError('Final LED placement budget exhausted')
        centre=positions[key]=position(f)
        pads={pad.GetNumber():pad for pad in f.Pads() if pad.GetNumber()}
        actual=tuple(tuple(position(pads[number])[i]-centre[i] for i in (0,1)) for number in ('3','2'))
        matches=[angle for angle in (0,180) if actual==tuple(posed_offset(q,side,angle) for q in offsets)]
        if len(matches)!=1:raise ValueError('Unexpected native LED data-pad geometry')
        current[key]=matches[0]
    driver=lookup['R_RGB'];lead=position(next(pad for pad in driver.Pads() if pad.GetNumber()=='2'))
    rotations=chain_rotations(order,positions,offsets,side=side,lead_nm=lead)
    if time.monotonic()>=deadline:raise TimeoutError('Final LED placement budget exhausted')
    for key,f in footprints.items():
        delta=rotations[key]-current[key]
        if delta:f.Rotate(f.GetPosition(),p.EDA_ANGLE(delta,p.DEGREES_T))
        parts[f.GetReference()]['rotation']=f.GetOrientationDegrees()
    return list(footprints.values())


def capture_led_mounts(lookup,order,*,p):
    """Bind the two supported native body poses without moving the real board.

    The source courtyard has a 1 nm off-centre coordinate. Its 180-degree
    counterpart is therefore distinct. Retain both exactly and use their
    conservative union only for mechanical clearance, never copper.
    """
    result={}
    for key in order:
        original=lookup['LED'+str(key)]
        alternate=p.Cast_to_FOOTPRINT(original.Duplicate(False))
        alternate.Rotate(alternate.GetPosition(),p.EDA_ANGLE(180,p.DEGREES_T))
        layers={}
        for layer in (p.F_Cu,p.B_Cu):
            poses=[p.SHAPE_POLY_SET(f.GetCourtyard(layer)) for f in (original,alternate)]
            envelope=p.SHAPE_POLY_SET(poses[0]);envelope.BooleanAdd(poses[1])
            layers[layer]={'poses':poses,'envelope':envelope}
        result[original.m_Uuid.AsString()]=layers
    return result


def same_courtyard(actual,before,*,p):
    """Native polygon equality, independent of vertex order/orientation."""
    added=p.SHAPE_POLY_SET(actual);added.BooleanSubtract(before)
    removed=p.SHAPE_POLY_SET(before);removed.BooleanSubtract(actual)
    return added.OutlineCount()==0 and removed.OutlineCount()==0
