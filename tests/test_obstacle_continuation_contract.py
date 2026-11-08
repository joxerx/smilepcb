"""Portable keyword-contract test of the actual isolated _connect definition.

Native collision helpers are permissive stubs here; separate Native tests own
physical qualification. This test checks the predicate's authority before emit.
"""
import ast,collections,os,sys,time,types,unittest
from pathlib import Path
from unittest.mock import patch


def connect_definition():
    default=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/obstacle_bundles.py'
    source=Path(os.environ.get('SMILEPCB_TEST_CONNECT_SOURCE',str(default)))
    definitions=[node for node in ast.parse(source.read_text()).body if isinstance(node,ast.FunctionDef) and node.name in ('_connect','_physical_connection')]
    yes=lambda *args,**kwargs:True
    style=types.SimpleNamespace(accepts_path=yes)
    route=dict(legs=[(0,[(0,0),(2,0)])],vias=[])
    namespace=dict(__package__='smilepcb.keyboard_routing',time=time,math=__import__('math'),
        safe_route=yes,_Style=lambda bounds:style,_join_clear=yes,_pad_contacts_clear=yes,
        retained_contacts_clear=yes,_own_contacts_clear=yes,_terminal_rays=lambda *args:set(),vec=lambda q:q,
        p=types.SimpleNamespace(F_Cu=0,B_Cu=2,SHAPE_SEGMENT=lambda *args:object()),
        ranked_departures=lambda *args:[[(0,0),(2,0)]],bridge_carrier=lambda *args,**kwargs:route,
        TerminalStyle=lambda *args:style,QualityProfile=lambda *args:None)
    exec(compile(ast.Module(body=definitions,type_ignores=[]),str(source),'exec'),namespace)
    return namespace['_connect']


class World:
    def __init__(self):
        self.track_width=1;self.via_width=2;self.board=None;self.ignored_ids=set();self.operations=[];self.connection_failures=collections.Counter()
    def checkpoint(self):return len(self.operations)
    def restore(self,mark):del self.operations[mark:]
    def track_items(self):return {}
    def clear(self,*args):return True
    def add_path(self,*args):self.operations.append(args)


class ContinuationContract(unittest.TestCase):
    def run_case(self,predicate='omitted'):
        connect=connect_definition();world=World();grid=types.ModuleType('smilepcb.keyboard_routing.grid_search');grid.search=lambda *args:False
        kw={} if predicate=='omitted' else dict(admit_continuation=predicate)
        with patch.dict(sys.modules,{'smilepcb.keyboard_routing.grid_search':grid}):
            okay=connect(world,(0,0),(2,0),0,0,'net',(-5,-5,5,5),time.monotonic()+1,**kw)
        return okay,world
    def test_default_contract_retains_success(self):
        okay,world=self.run_case();self.assertTrue(okay);self.assertEqual(len(world.operations),1)
    def test_false_predicate_rejects_before_any_emission(self):
        calls=[];okay,world=self.run_case(lambda route:calls.append(route) or False)
        self.assertFalse(okay);self.assertTrue(calls);self.assertFalse(world.operations)
        self.assertGreater(world.connection_failures['continuation-self-contact'],0)
    def test_true_predicate_is_called_and_allows_emission(self):
        calls=[];okay,world=self.run_case(lambda route:calls.append(route) or True)
        self.assertTrue(okay);self.assertTrue(calls);self.assertEqual(len(world.operations),1)


if __name__=='__main__':unittest.main()


class SharedPhysicalContract(unittest.TestCase):
    def test_every_existing_guard_still_has_authority_for_joint_admission(self):
        for guard in ('safe_route','_join_clear','_pad_contacts_clear','retained_contacts_clear','_own_contacts_clear'):
            namespace=connect_definition().__globals__;namespace[guard]=lambda *a,**kw:False
            world=World();evidence={}
            accepted=namespace['_physical_connection'](world,dict(legs=[(0,[(0,0),(2,0)])],vias=[]),
                (0,0),(2,0),'net',(-5,-5,5,5),diagnostics=evidence)
            self.assertFalse(accepted,guard);self.assertTrue(evidence.get('reason'));self.assertFalse(world.operations)

    def test_collision_witnesses_are_bounded_and_actual_via_size_is_used(self):
        namespace=connect_definition().__globals__;world=World();world.clear=lambda *a:False
        world.collisions=lambda *a,**kw:iter(range(100));evidence={}
        accepted=namespace['_physical_connection'](world,dict(legs=[(0,[(0,0),(2,0)])],vias=[]),
            (0,0),(2,0),'net',(-5,-5,5,5),diagnostics=evidence)
        self.assertFalse(accepted);self.assertEqual(evidence['collisions'],list(range(8)))
        world.clear=lambda *a:True;world.via_clear=lambda *a:False;world.drill=1
        namespace['p'].SHAPE_CIRCLE=lambda point,radius:('circle',radius)
        evidence={}
        accepted=namespace['_physical_connection'](world,dict(legs=[(0,[(0,0),(2,0)])],vias=[(1,0)]),
            (0,0),(2,0),'net',(-5,-5,5,5),diagnostics=evidence)
        self.assertFalse(accepted);self.assertEqual(evidence['via_width_nm'],world.via_width)
        self.assertEqual(evidence['drill_nm'],world.drill)
