"""Host ownership contracts for an unapplied draft; no native execution."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

MODULE=Path(__file__).parents[1]/'smilepcb/keyboard_routing/owned_feeder_repair.py'

def load():
 spec=importlib.util.spec_from_file_location('owned_feeder_repair_draft',MODULE);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module

def shape(net='a',**changes):
 value={'class':'PCB_TRACK','start':[0,0],'end':[1000000,0],'width':150000,'layer':0,'locked':False,'net':net};value.update(changes);return value

def frame():return {'tracks':{'fixed':shape('fixed')},'footprints':{'footprint':{'pads':[{'uuid':'pad','net':'a'}]}}}

class JournalOwnership(unittest.TestCase):
 def contract(self,name):
  fn=getattr(load(),name,None);self.assertTrue(callable(fn),'Required ownership behavior absent: '+name);return fn
 def test_delta_certifies_exact_append_only_shapes_and_returns_detached_inventory(self):
  fn=self.contract('feeder_delta');before=frame();after=deepcopy(before);after['tracks'].update({'new-track':shape(),'new-via':shape(**{'class':'PCB_VIA','drill':200000,'top':0,'bottom':2})})
  old=deepcopy((before,after));delta=fn(before,after,['new-track','new-via'],'a',{'fixed'})
  self.assertEqual(delta,{k:v for k,v in after['tracks'].items() if k!='fixed'});self.assertEqual((before,after),old)
  after['tracks']['new-track']['width']=999;self.assertEqual(delta['new-track']['width'],150000)
 def test_delta_rejects_retained_mutation_foreign_or_unjournaled_copper(self):
  fn=self.contract('feeder_delta')
  for mode in ('delete-old','change-old','change-footprint','foreign','locked','non-copper','duplicate-index','missing-index','extra-index','protected-new'):
   with self.subTest(mode=mode):
    before=frame();after=deepcopy(before);after['tracks']['new']=shape();indexed=['new'];protected={'fixed'}
    if mode=='delete-old':del after['tracks']['fixed']
    elif mode=='change-old':after['tracks']['fixed']['width']=200000
    elif mode=='change-footprint':after['footprints']['footprint']['pads'][0]['net']='other'
    elif mode=='foreign':after['tracks']['new']['net']='b'
    elif mode=='locked':after['tracks']['new']['locked']=True
    elif mode=='non-copper':after['tracks']['new']['class']='PAD'
    elif mode=='duplicate-index':indexed=['new','new']
    elif mode=='missing-index':indexed=[]
    elif mode=='extra-index':indexed=['new','absent']
    elif mode=='protected-new':protected.add('new')
    saved=deepcopy((before,after,indexed,protected))
    with self.assertRaises((ValueError,AssertionError)):fn(before,after,indexed,'a',protected)
    self.assertEqual((before,after,indexed,protected),saved)
 def journal(self):
  owners={'a-track':shape(),'a-via':shape(**{'class':'PCB_VIA','drill':200000}),'b-track':shape('b')}
  return SimpleNamespace(bank_nets=frozenset({'target','a','b'}),frame=frame(),ports={},physical='sealed',context=(),owners=owners,witnesses={'target':{'a-track':{'net':'a','shape':deepcopy(owners['a-track'])}}})
 def test_one_exact_native_witness_licenses_entire_live_owner_only(self):
  fn=self.contract('owner_cohort');j=self.journal();saved=deepcopy(j.__dict__);nets,ids=fn(j,'target',{'a','b'})
  self.assertEqual(nets,['a']);self.assertEqual(ids,['a-track','a-via']);self.assertEqual(j.__dict__,saved)
 def test_scope_rejects_stale_protected_open_or_same_net_witnesses(self):
  fn=self.contract('owner_cohort')
  for mode in ('changed-shape','wrong-net','unknown','protected','open','same-net'):
   with self.subTest(mode=mode):
    j=self.journal();closed={'a','b'}
    if mode=='changed-shape':j.witnesses['target']['a-track']['shape']['width']=200000
    elif mode=='wrong-net':j.witnesses['target']['a-track']['net']='b'
    elif mode=='unknown':j.witnesses['target']={'unknown':{'net':'a','shape':shape()}}
    elif mode=='protected':j.frame['tracks']['a-track']=deepcopy(j.owners['a-track'])
    elif mode=='open':closed={'b'}
    elif mode=='same-net':j.witnesses['target']={'candidate':{'net':'target','shape':shape('target')}};j.owners['candidate']=shape('target')
    saved=deepcopy(j.__dict__)
    try:nets,ids=fn(j,'target',closed)
    except (AssertionError,ValueError):pass
    else:self.assertEqual(nets,[]);self.assertEqual(ids,[])
    self.assertEqual(j.__dict__,saved)
 def test_whole_owner_cannot_silently_omit_invalid_tail(self):
  fn=self.contract('owner_cohort')
  for mode in ('locked','protected','foreign-bank'):
   with self.subTest(mode=mode):
    j=self.journal()
    if mode=='locked':j.owners['a-via']['locked']=True
    elif mode=='protected':j.frame['tracks']['a-via']=deepcopy(j.owners['a-via'])
    else:j.bank_nets=frozenset({'target','b'})
    try:nets,ids=fn(j,'target',{'a','b'})
    except (ValueError,AssertionError):continue
    self.assertEqual(nets,[]);self.assertEqual(ids,[])

if __name__=='__main__':unittest.main(verbosity=2)
