"""Host transaction controls with explicit nonphysical boundary substitutes."""
from copy import deepcopy
from contextlib import ExitStack
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

MODULE = Path(__file__).parents[1] / 'smilepcb/keyboard_routing/owned_feeder_repair.py'

def load():
    spec = importlib.util.spec_from_file_location('owned_transaction_draft', MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

class HeldSourcePolicy(unittest.TestCase):
    def fixture(self):
        module = load()
        source = {'class':'PCB_TRACK','net':'a','locked':False,'start':[0,0],'end':[1,0],'width':1,'layer':0}
        frame = {'tracks':{'source':source,'field':deepcopy(source)},'footprints':{}}
        ports = {('pad',):{'net':'a','pad_ids':['pad'],'added_ids':['source']}}
        inventory = SimpleNamespace(ports=ports, resolved=set(), repair_reserved={('pad',)})
        stack = ExitStack()
        stack.enter_context(patch.object(module, '_inventory', lambda: inventory))
        stack.enter_context(patch.object(module, '_snapshot', lambda board: deepcopy(frame)))
        stack.enter_context(patch.object(module, '_physical', lambda board, excluded=(): 'native-physical'))
        stack.enter_context(patch.object(module, '_context', lambda board: ('native-context',)))
        self.addCleanup(stack.close)
        journal = module.begin_feeders(object(), {'a'})
        return module, frame, inventory, journal

    def test_external_held_guard_change_invalidates_journal_before_repair(self):
        module, frame, inventory, journal = self.fixture()
        module._validate(object(), journal)
        inventory.repair_reserved.clear()
        with self.assertRaises(ValueError):
            module._validate(object(), journal)
        inventory.repair_reserved = {('pad',)}
        module._validate(object(), journal)

    def test_external_held_guard_change_cannot_publish_append_authority(self):
        module, frame, inventory, journal = self.fixture()
        saved = deepcopy(journal.__dict__)
        inventory.repair_reserved.clear()
        with self.assertRaises(ValueError):
            module.record_feeder_route(object(), journal, deepcopy(frame), [], 'a')
        self.assertEqual(journal.__dict__, saved)


    def test_same_net_nonport_field_cannot_be_reclassified_as_generated_owner(self):
        module, frame, inventory, journal = self.fixture()
        module._validate(object(), journal)
        journal.owners['field'] = journal.frame['tracks'].pop('field')
        with self.assertRaises(ValueError):
            module._validate(object(), journal)


class NativeStateSeams:
    """Board/copy stand-ins; no clearance or physical connectivity claim."""
    class Track:
        def __init__(self, uid, data, raw=0):
            self.uid, self.data, self.raw = uid, deepcopy(data), raw
        @property
        def m_Uuid(self): return SimpleNamespace(AsString=lambda: self.uid)
        def Duplicate(self): return type(self)(self.uid, self.data, self.raw)
        def SetUuid(self, uid): self.uid = uid
        def GetLocalClearance(self): return self.raw
        def IsOnLayer(self, face): return face == 0 or self.data['class'] == 'PCB_VIA'
        def GetEffectiveShape(self, face): return SimpleNamespace(Format=lambda verbose: repr((self.data, self.raw, face)))
        def GetEffectiveHoleShape(self): return SimpleNamespace(Format=lambda verbose: repr((self.data.get('drill'), self.raw)))
        def Padstack(self): return object()  # Opaque proxies cannot certify value equality.
    class Via(Track): pass
    class Board:
        def __init__(self, items): self.items = {item.uid:item for item in items}; self.deleted = []
        def GetTracks(self): return list(self.items.values())
        def Delete(self, item): self.deleted.append(item.uid); del self.items[item.uid]
        def Add(self, item): self.items[item.uid] = item
        def BuildConnectivity(self): pass

    def __init__(self, test, mode):
        import json
        self.module = load(); self.mode = mode; self.clock = [0.0]; self.proofs = 0
        shape = {'class':'PCB_TRACK','net':'target','locked':False,'start':[0,0],'end':[1,0],'width':1,'layer':0}
        via = dict(shape, **{'class':'PCB_VIA','net':'fixed','drill':1})
        self.board = self.Board([self.Track('source',shape), self.Via('fixed-via',via)])
        ports = {(net,):{'net':net,'pad_ids':[net],'added_ids':['source'] if net=='target' else []}
                 for net in ('target','owner','open')}
        self.inventory = SimpleNamespace(ports=ports,resolved={('owner',)},repair_reserved={('target',)})
        self.snapshot = lambda board: {'tracks':{uid:deepcopy(item.data) for uid,item in board.items.items()},'footprints':{}}
        self.physical = lambda board,excluded=(): json.dumps({uid:[item.data,item.raw] for uid,item in board.items.items() if uid not in excluded},sort_keys=True)
        self.context = lambda board: ('context',)
        stack = ExitStack(); test.addCleanup(stack.close)
        for name, value in {'_snapshot':self.snapshot,'_physical':self.physical,'_context':self.context,
                            '_inventory':lambda:self.inventory}.items():
            stack.enter_context(patch.object(self.module,name,value))
        native = SimpleNamespace(p=SimpleNamespace(PCB_VIA=self.Via,F_Cu=0,B_Cu=2,KIID=lambda uid:uid))
        stack.enter_context(patch.dict(sys.modules,{'smilepcb.native_worker':native}))
        stack.enter_context(patch.object(self.module.time,'monotonic',lambda:self.clock[0]))
        self.journal = self.module.begin_feeders(self.board, {'target','owner','open'})
        for net in ('owner','open'):
            before=self.snapshot(self.board);item=self.Track(net+'-old',dict(shape,net=net));self.board.Add(item)
            self.module.record_feeder_route(self.board,self.journal,before,[item.uid],net)
        self.journal.witnesses={'target':{'owner-old':{'net':'owner','shape':deepcopy(self.journal.owners['owner-old'])}}}
        self.initial=(self.snapshot(self.board),self.physical(self.board),deepcopy(self.journal.__dict__),deepcopy(self.inventory.__dict__))
        def checkpoint(board,journal):
            return {'frame':self.snapshot(board),'physical':self.physical(board),'context':self.context(board),
                    'copies':{uid:item.Duplicate() for uid,item in board.items.items()},'journal':deepcopy(journal.__dict__),
                    'launches':(deepcopy(self.inventory.ports),set(self.inventory.resolved),set(self.inventory.repair_reserved))}
        self.checkpoint=checkpoint
        def assess(board,journal,deadline):
            self.module._guard(deadline)
            if 'owner-old' in board.items: missing={'target':1,'open':1}
            elif self.mode in ('proof-fail','expired-proof','success'): missing={'open':1}
            else: missing={'target':1,'owner':1,'open':1}
            return {'missing':missing,'partitions':{},'attached':set(board.items),'frame':self.snapshot(board),'graph':{},'coverage':set(journal.bank_nets)}
        def route(board,journal,order,deadline):
            if self.mode=='unexpected':
                board.Add(self.Track('partial',dict(shape,net='target')))
                journal.owners['partial']=deepcopy(board.items['partial'].data)
                self.inventory.ports={};self.inventory.resolved=set();self.inventory.repair_reserved=set()
                raise RuntimeError('injected native route failure')
            if self.mode in ('proof-fail','expired-proof','success'):
                board.Add(self.Track('replacement',dict(shape,net='owner')))
                journal.owners['replacement']=deepcopy(board.items['replacement'].data)
                journal.current_physical=self.physical(board)
            return [{'net':'owner','connections':0,'added':{},'witnesses':{
                'open-old':{'net':'open','shape':deepcopy(self.journal.owners['open-old'])}},'query_omissions':0}]
        def prove(board,journal,state,deadline):
            self.proofs+=1
            if self.mode=='proof-fail': raise ValueError('injected physical proof rejection')
            if self.mode=='expired-proof': self.clock[0]=deadline
        for name,value in {'_checkpoint':checkpoint,'_assess':assess,'_route_candidate':route,'_prove_feeders':prove}.items():
            stack.enter_context(patch.object(self.module,name,value))
    def run(self): return self.module.repair_owned_feeders(self.board,self.journal,'target',deadline=100.0)
    def state(self): return (self.snapshot(self.board),self.physical(self.board),self.journal.__dict__,self.inventory.__dict__)

class RepairControlFlow(unittest.TestCase):
    def test_open_baseline_owner_witness_rejects_widening_without_aborting_transaction(self):
        fixture=NativeStateSeams(self,'scope-open')
        result=fixture.run()
        self.assertFalse(result.improved);self.assertEqual(fixture.state(),fixture.initial)
        self.assertEqual(fixture.proofs,0)
    def test_unexpected_after_mutation_restores_native_journal_and_all_launch_fields(self):
        fixture=NativeStateSeams(self,'unexpected')
        with self.assertRaisesRegex(RuntimeError,'injected native route failure'): fixture.run()
        self.assertEqual(fixture.state(),fixture.initial)
    def test_expired_or_failed_physical_proof_cannot_commit_native_progress(self):
        for mode in ('proof-fail','expired-proof'):
            with self.subTest(mode=mode):
                fixture=NativeStateSeams(self,mode)
                result=fixture.run()
                self.assertFalse(result.improved);self.assertGreater(fixture.proofs,0)
                self.assertEqual(fixture.state(),fixture.initial)
    def test_rollback_does_not_compare_opaque_padstack_proxy_identity(self):
        fixture=NativeStateSeams(self,'scope-open');saved=fixture.checkpoint(fixture.board,fixture.journal)
        original=fixture.board.items['fixed-via']
        fixture.module._restore(fixture.board,fixture.journal,saved)
        self.assertIs(fixture.board.items['fixed-via'],original)
    def test_raw_native_property_change_is_replaced_from_exact_copy(self):
        fixture=NativeStateSeams(self,'scope-open');saved=fixture.checkpoint(fixture.board,fixture.journal)
        fixture.board.items['fixed-via'].raw=17
        fixture.module._restore(fixture.board,fixture.journal,saved)
        self.assertEqual(fixture.state(),fixture.initial)

if __name__ == '__main__':
    unittest.main(verbosity=2)
