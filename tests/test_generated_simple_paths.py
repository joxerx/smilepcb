"""DSN creation uses octile simple legs rather than free-angle traces."""
import ast
from pathlib import Path


def proposals():
 path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/local_router.py';tree=ast.parse(path.read_text())
 node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='paths');scope={}
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),scope)
 return scope['paths']


def test_exchange_profile_simple_legs_keep_endpoints_with_only_octile_edges():
 a,z=(103,207),(3000106,2000209)
 rows=list(proposals()(a,z,octile_only=True))
 assert rows and all(row[0]==a and row[-1]==z for row in rows)
 assert all(not dx or not dy or abs(dx)==abs(dy) for row in rows for q,r in zip(row,row[1:]) for dx,dy in [(r[0]-q[0],r[1]-q[1])])


def test_legacy_native_proposals_keep_the_original_free_angle_candidate():
 assert next(proposals()((0,0),(3,2)))==[(0,0),(3,2)]
