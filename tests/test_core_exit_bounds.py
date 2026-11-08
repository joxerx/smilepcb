"""Generic positive-module search frame; no Native routing qualification."""
from copy import deepcopy
import importlib
import importlib.util
from pathlib import Path
import ast
import pytest


class Box:
    def __init__(self,bounds):self.bounds=bounds
    def GetLeft(self):return self.bounds[0]
    def GetTop(self):return self.bounds[1]
    def GetRight(self):return self.bounds[2]
    def GetBottom(self):return self.bounds[3]


class Footprint:
    def __init__(self,bounds):self.bounds=tuple(bounds)
    def GetBoundingBox(self,*args):
        assert args==(False,False),'reference/value text must not enlarge the physical module frame'
        return Box(self.bounds)


class Copper:
    def __init__(self,bounds):self.bounds=tuple(bounds)
    def GetBoundingBox(self,*args):assert args==();return Box(self.bounds)


def helper():
    assert importlib.util.find_spec('smilepcb.stm32.core_bounds') is not None,'positive whole-module bounds helper is missing'
    return importlib.import_module('smilepcb.stm32.core_bounds').core_exit_bounds_nm


def test_cap_and_internal_copper_outside_chip_enlarge_frame_without_mutation():
    core=[Footprint([0,0,10,10]),Footprint([12,-3,15,4]),Copper([-5,2,1,3])]
    before=deepcopy([item.bounds for item in core])
    assert helper()(core,footprint_type=Footprint)==(-5,-3,15,10)
    assert helper()(core,footprint_type=Footprint,fanout_bounds_nm=(-1,-1,11,11))==(-5,-3,15,11)
    assert [item.bounds for item in core]==before


def test_module_and_existing_fanout_frame_translate_equivariantly():
    delta=(-13,27);boxes=[[0,0,10,10],[12,-3,15,4],[-5,2,1,3]];fanout=(-1,-1,11,11)
    def shift(box):return tuple(value+delta[i%2] for i,value in enumerate(box))
    core=[Footprint(boxes[0]),Footprint(boxes[1]),Copper(boxes[2])]
    moved=[Footprint(shift(boxes[0])),Footprint(shift(boxes[1])),Copper(shift(boxes[2]))]
    original=helper()(core,footprint_type=Footprint,fanout_bounds_nm=fanout)
    assert helper()(moved,footprint_type=Footprint,fanout_bounds_nm=shift(fanout))==shift(original)
    with pytest.raises(ValueError):helper()([],footprint_type=Footprint)


def test_placer_passes_positive_module_frame_through_existing_exit_argument():
    tree=ast.parse((Path(__file__).resolve().parents[1]/'smilepcb/stm32/place.py').read_text())
    call=next(q for q in ast.walk(tree) if isinstance(q,ast.Call) and getattr(q.func,'id',None)=='prepare_ordered_controller')
    frame=next(q.value for q in call.keywords if q.arg=='exit_bounds_nm')
    assert isinstance(frame,ast.Name) and frame.id=='core_exit_frame_nm'
    union=next((q for q in ast.walk(tree) if isinstance(q,ast.Call) and getattr(q.func,'id',None)=='core_exit_bounds_nm'),None)
    assert union is not None and isinstance(union.args[0],ast.Name) and union.args[0].id=='core'
    assert any(q.arg=='fanout_bounds_nm' for q in union.keywords)
