"""Host-only empty-input control; no native geometry is simulated or certified."""
import ast
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/power.py'

class EmptyOnce:
    def __init__(self):self.iterations=0
    def __iter__(self):
        self.iterations+=1
        assert self.iterations==1,'Input iterable consumed more than once'
        return iter(())

class EmptyInputControl(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        def forbidden(*args,**kwargs):
            self.calls.append('native graph/index')
            raise AssertionError('Empty input must not build native graph or spatial index')
        module=ModuleType('smilepcb.keyboard_routing.native_graph');module.copper_graph=forbidden
        self.stub=patch.dict(sys.modules,{'smilepcb.keyboard_routing.native_graph':module});self.stub.start();self.addCleanup(self.stub.stop)
        tree=ast.parse(SOURCE.read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_seed_plane_access')
        ns={'__name__':'smilepcb.keyboard_routing.power','__package__':'smilepcb.keyboard_routing','time':SimpleNamespace(monotonic=lambda:100.0),'LocalRouter':forbidden}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),ns);self.helper=ns[node.name]
    def call(self,pads,**options):
        kwargs=dict(context={},deadline=None,track_width_nm=None,max_distance_nm=2000000,via_to_smd_clearance_nm=0);kwargs.update(options)
        result=self.helper(object(),pads,**kwargs);self.assertEqual(self.calls,[]);return result
    def test_empty_list_skips_all_native_work(self):self.assertEqual(self.call([]),[])
    def test_empty_iterator_consumed_once(self):
        pads=EmptyOnce();self.assertEqual(self.call(pads),[]);self.assertEqual(pads.iterations,1)
    def test_empty_generator_consumed_once(self):
        visited=[]
        def empty():
            visited.append('consumed')
            yield from ()
        self.assertEqual(self.call(empty()),[]);self.assertEqual(visited,['consumed'])
    def test_expired_deadline_returns_without_consuming(self):
        pads=EmptyOnce();self.assertEqual(self.call(pads,deadline=100.0),[]);self.assertEqual(pads.iterations,0)
    def test_invalid_arguments_raise_even_for_empty_expired_input(self):
        for options in ({'max_distance_nm':True},{'max_distance_nm':799999},{'max_distance_nm':10000001},
                        {'via_to_smd_clearance_nm':False},{'via_to_smd_clearance_nm':-1},{'via_to_smd_clearance_nm':2000001}):
            with self.subTest(options=options):
                pads=EmptyOnce()
                with self.assertRaises(ValueError):self.call(pads,deadline=0,**options)
                self.assertEqual(pads.iterations,0);self.assertEqual(self.calls,[])
    def test_validation_boundaries_accept_empty_input(self):
        for limit in (800000,10000000):
            for clearance in (0,2000000):
                self.assertEqual(self.call([],max_distance_nm=limit,via_to_smd_clearance_nm=clearance),[])

if __name__=='__main__':unittest.main(verbosity=2)
