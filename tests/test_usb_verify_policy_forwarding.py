"""Host execution of verification call sites with the real USB length checker."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Track:
    def __init__(self, net, length):
        self.net, self.length = net, length

    def GetNetname(self): return self.net
    def GetLength(self): return self.length
    def GetLayer(self): return 2


class Via(Track):
    pass


def verification_call(entry, request):
    """Extract only the Native-free call, leaving its request defaulting intact."""
    tree = ast.parse((ROOT / 'smilepcb/keyboard_native.py').read_text())
    checker = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'verify_usb_pair')
    scope = {'p': NS(PCB_VIA=Via), 'math': math}
    exec(compile(ast.Module(body=[checker], type_ignores=[]), 'usb-checker', 'exec'), scope)
    if entry == 'verify':
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_run')
        branch = next(n for n in run.body if isinstance(n, ast.If) and ast.unparse(n.test) == "request['operation'] == 'verify'")
    else:
        branch = ast.parse((ROOT / 'smilepcb/stm32/check_access.py').read_text())
    call = next(n for n in ast.walk(branch) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'verify_usb_pair')
    scope.update(request=request, board=NS(
        GetTracks=lambda: [Track('USB_DP', 22_100_000), Track('USB_DM', 22_000_000)],
        GetFootprints=lambda: [NS(GetReference=lambda: 'U_MCU', GetLayer=lambda: 2)]))
    return eval(compile(ast.Expression(body=call), 'verification-entry', 'eval'), scope)


@pytest.mark.parametrize('entry', ['verify', 'check_access'])
@pytest.mark.parametrize('payload,accepted', [
    ({}, False),
    ({'usb_max_length_nm': None}, True),
    ({'usb_max_length_nm': 35_000_000}, True),
    ({'usb_max_length_nm': 21_000_000}, False),
])
def test_verification_entries_apply_caller_length_policy(entry, payload, accepted):
    if accepted:
        assert verification_call(entry, payload) == {'USB_DP': 22.1, 'USB_DM': 22.0}
    else:
        with pytest.raises(ValueError, match='length or skew'):
            verification_call(entry, payload)
