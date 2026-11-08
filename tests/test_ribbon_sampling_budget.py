import inspect
import importlib.util
import os
from pathlib import Path
import unittest


SOURCE=Path(os.environ.get('RIBBON_CANDIDATE_SOURCE',Path(__file__).parents[1]/'smilepcb'/'keyboard_routing'/'ribbon_port_candidates.py'))
spec=importlib.util.spec_from_file_location('ribbon_port_candidates_budget',SOURCE)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
port_pairs=module.port_pairs
departure_possible=module.departure_possible
SUPPORTS_SAMPLING='sampling' in inspect.signature(port_pairs).parameters


class CallbackBudgetExceeded(Exception):
    pass


class RibbonSamplingBudgetTest(unittest.TestCase):
    bounds=(0,0,48_000_000,36_000_000)
    args=dict(source=(0,0),targets=[(10_000_000,18_000_000)],heading=(1,0),
              bounds=bounds,step_nm=1,lead_nm=1_000_000,
              minimum_run_nm=2_000_000)

    def call(self, *, free, departure_free=None, sampling=None, targets=None):
        kwargs=dict(self.args,free=free)
        if departure_free is not None:kwargs['departure_free']=departure_free
        if targets is not None:kwargs['targets']=targets
        if SUPPORTS_SAMPLING:kwargs['sampling']=sampling
        return port_pairs(**kwargs)

    def test_empty_world_work_is_bounded_and_instrumented(self):
        for bounds in (self.bounds,(-24_000_000,-18_000_000,24_000_000,18_000_000)):
            with self.subTest(bounds=bounds):
                calls=[0]
                sampling={}
                def free(_):
                    calls[0]+=1
                    if calls[0]>3000:raise CallbackBudgetExceeded
                    return False
                kwargs=dict(self.args,bounds=bounds)
                try:
                    if SUPPORTS_SAMPLING:kwargs['sampling']=sampling
                    pairs=port_pairs(**kwargs,free=free)
                except CallbackBudgetExceeded:
                    self.fail('endpoint sampling exceeded the 3000-call budget')
                self.assertEqual(pairs,[])
                self.assertLessEqual(calls[0],3000)
                self.assertEqual(sampling.get('primary_pairs'),0)
                self.assertLessEqual(sampling.get('fallback_rings',10**9),64)
                self.assertTrue(sampling.get('sparse_fallback'))
                self.assertLessEqual(sampling.get('endpoint_queries',10**9),3000)

    def test_false_departure_filter_has_bounded_endpoint_work(self):
        endpoint_calls=[0]
        departure_calls=[0]
        sampling={}
        def free(_):
            endpoint_calls[0]+=1
            if endpoint_calls[0]>2300:raise CallbackBudgetExceeded
            return True
        def departure(_):
            departure_calls[0]+=1
            if departure_calls[0]>2300:raise CallbackBudgetExceeded
            return False
        try:
            pairs=self.call(free=free,departure_free=departure,sampling=sampling)
        except CallbackBudgetExceeded:
            self.fail('departure-filtered sampling exceeded the 2300-call budget')
        self.assertEqual(pairs,[])
        self.assertLessEqual(endpoint_calls[0],2300)
        self.assertLessEqual(departure_calls[0],2300)
        self.assertLessEqual(sampling.get('fallback_rings',10**9),64)
        self.assertLessEqual(sampling.get('endpoint_queries',10**9),2300)

    def test_deferred_departure_result_keeps_candidate_for_full_search(self):
        sampling={}
        departure_calls=[0]
        def deferred(_):
            departure_calls[0]+=1
            if departure_calls[0]>2300:raise CallbackBudgetExceeded
            return None
        try:
            pairs=self.call(free=lambda _:True,departure_free=deferred,sampling=sampling)
        except CallbackBudgetExceeded:
            self.fail('deferred departure should not force an unbounded seed scan')
        self.assertTrue(pairs)
        self.assertGreater(sampling.get('departure_queries',0),0)

    def test_sparse_fallback_reaches_a_valid_extreme_ring_on_one_nm_grid(self):
        distant_start=(1_000_000,36_000_000)
        calls=[0]
        sampling={}
        def free(q):
            calls[0]+=1
            if calls[0]>3000:raise CallbackBudgetExceeded
            return q==distant_start or (q[1]==36_000_000 and q[0]>=2_000_000)
        try:
            pairs=self.call(free=free,sampling=sampling,targets=[(10_000_000,36_000_000)])
        except CallbackBudgetExceeded:
            self.fail('sparse fallback did not reach the extreme within the callback budget')
        self.assertTrue(pairs)
        self.assertIn(distant_start,{start for start,_ in pairs})
        self.assertLessEqual(calls[0],3000)
        self.assertLessEqual(sampling.get('fallback_rings',10**9),64)
        self.assertTrue(sampling.get('sparse_fallback'))

    def test_over_budget_forced_departure_is_deferred_without_callbacks(self):
        calls=[]
        def record(kind):
            def callback(*args):
                calls.append((kind,args))
                if len(calls)>64:raise CallbackBudgetExceeded
                return kind=='edge'
            return callback
        try:
            result=departure_possible((0,0),(1,0),('F','B'),step=1,
                                      minimum_run=1_000_000,bounds=(0,0,1_000_000,1_000_000),
                                      edge_clear=record('edge'),portal=record('portal'))
        except CallbackBudgetExceeded:
            self.fail('over-budget prefix should defer before calling geometry predicates')
        self.assertIsNone(result)
        self.assertEqual(calls,[])


if __name__=='__main__':
    unittest.main()
