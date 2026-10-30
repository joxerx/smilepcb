"""Real bounded CSP decisions with irrelevant groups ahead of a tight bank."""
import ast
from pathlib import Path
import time


def selector():
 path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/controller_ports.py'
 tree=ast.parse(path.read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_select')
 module=ast.Module(body=[node],type_ignores=[]);scope={'time':time}
 exec(compile(module,str(path),'exec'),scope)
 return scope['_select']


def test_later_physical_bank_influences_choice_without_extra_search_states():
 domains=[[(i,a) for a in range(2 if i==0 else 3 if i<9 else 4)] for i in range(11)]
 def compatible(a,b):
  if a[0]>b[0]:a,b=b,a
  if a[0]==0 and b[0] in (9,10) and a[1]==0:return b[1]==0
  if a[0]==9 and b[0]==10:return a[1]!=0 or b[1]!=0
  return True
 answers=[]
 for _ in range(2):
  chosen,stats=selector()(domains,compatible,time.monotonic()+5,11)
  assert chosen is not None,stats
  assert stats['states']==11
  assert all(compatible(domains[i][a],domains[j][b]) for i,a in chosen.items() for j,b in chosen.items() if i!=j)
  answers.append(chosen)
 assert answers[0]==answers[1]


def test_impossible_ports_and_limits_fail_without_partial_assignment():
 for domains,compatible,deadline,cap in [
  ([[0],[1]],lambda a,b:False,time.monotonic()+5,10),
  ([[0],[1]],lambda a,b:True,time.monotonic()-1,10),
  ([[0],[1]],lambda a,b:True,time.monotonic()+5,1),
 ]:
  chosen,stats=selector()(domains,compatible,deadline,cap)
  assert chosen is None and stats['states']<=cap
