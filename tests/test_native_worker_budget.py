from types import SimpleNamespace
import pytest
from smilepcb import lab_tools

def test_explicit_matrix_worker_can_use_bounded_longer_budget(tmp_path,monkeypatch):
    observed=[]
    def run(*args,**kwargs):
        observed.append(kwargs['timeout']);return SimpleNamespace(returncode=0,stdout='{}',stderr='')
    monkeypatch.setattr(lab_tools.subprocess,'run',run)
    lab_tools.worker('native',{},tmp_path/'default.log')
    lab_tools.worker('native',{},tmp_path/'matrix.log',timeout_s=240)
    lab_tools.worker('native',{},tmp_path/'extended-placement.log',timeout_s=1220)
    assert observed==[90,240,1220]

@pytest.mark.parametrize('budget',[0,-1,1221,float('inf'),float('nan'),True,'240'])
def test_worker_never_accepts_unbounded_or_invalid_timeout(tmp_path,budget):
    with pytest.raises(ValueError):lab_tools.worker('native',{},tmp_path/'invalid.log',timeout_s=budget)
