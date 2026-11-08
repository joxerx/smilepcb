"""USB measurement uses actual segment lengths, independent of enumeration order."""
import ast
from functools import reduce
import math
from operator import add
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

# Actual GetLength values from usb-checkpoint-length-diagnostic.json; no board input.
DM=[3005203.820042827,86974.13408594535,1577474.6186541324,1106251.,1002550.,875000.,99711.95564223982,749813.,530330.0858899107,2000000.]
DP=[593653.,1045246.658163517,1106251.,1500000.,1045246.658163517,1014422.459358033,1798318.208554315,86974.13408594535,1344390.,1500000.]


class Track:
    def __init__(self,net,length,layer=2):self.net,self.length,self.layer=net,length,layer
    def GetNetname(self):return self.net
    def GetLength(self):return self.length
    def GetLayer(self):return self.layer


class Via(Track):pass


def verifier():
    path=Path(__file__).parents[1]/'smilepcb/keyboard_native.py'
    node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='verify_usb_pair')
    # Native Python uses legacy sequential sum. Emulate it on newer host Python,
    # whose built-in sum already compensates floats and could mask this regression.
    ns={'p':NS(PCB_VIA=Via),'math':math,'sum':lambda xs:reduce(add,xs,0)}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
    return ns['verify_usb_pair']


def board(tracks):
    return NS(GetTracks=lambda:tracks,GetFootprints=lambda:[NS(GetReference=lambda:'U_MCU',GetLayer=lambda:2)])


def pair():return [Track('USB_DM',x) for x in DM]+[Track('USB_DP',x) for x in DP]


def test_actual_native_lengths_are_exact_when_track_enumeration_reverses():
    verify=verifier();tracks=pair()
    assert reduce(add,DM,0)!=reduce(add,reversed(DM),0)  # The witnessed one-ULP case.
    expected={'USB_DM':11.033308614315056,'USB_DP':11.034502118325326}
    assert verify(board(tracks))==verify(board(list(reversed(tracks))))==expected


@pytest.mark.parametrize('tracks',[
    [Track('USB_DM',20000000),Track('USB_DP',20000000)],  # Actual length limit.
    [Track('USB_DM',10000000),Track('USB_DP',10500000)],  # Exact skew boundary.
    [Track('USB_DM',0),Track('USB_DP',100000)],
    [Track('USB_DM',10000000,layer=0),Track('USB_DP',10000000)],
    [Via('USB_DM',10000000),Track('USB_DP',10000000)],
])
def test_real_length_skew_face_and_via_violations_still_reject(tracks):
    with pytest.raises(ValueError):verifier()(board(tracks))


def test_explicit_finite_long_limit_preserves_strict_length_and_skew_guards():
    verify=verifier();tracks=[Track('USB_DM',22_000_000),Track('USB_DP',22_100_000)]
    with pytest.raises(ValueError):verify(board(tracks))
    assert verify(board(tracks),max_length_nm=30_000_000)=={'USB_DP':22.1,'USB_DM':22.}
    with pytest.raises(ValueError):verify(board(tracks),max_length_nm=22_100_000)
    with pytest.raises(ValueError):verify(board([Track('USB_DM',22_000_000),Track('USB_DP',22_500_000)]),max_length_nm=30_000_000)


def test_explicit_none_removes_only_absolute_cap_for_finite_actual_lengths():
    verify=verifier();tracks=[Track('USB_DM',60_000_000),Track('USB_DP',60_100_000)]
    with pytest.raises(ValueError):verify(board(tracks))
    assert verify(board(tracks),max_length_nm=None)=={'USB_DP':60.1,'USB_DM':60.}


@pytest.mark.parametrize('tracks',[
    [Track('USB_DM',0),Track('USB_DP',100000)],
    [Track('USB_DM',float('inf')),Track('USB_DP',float('inf'))],
    [Track('USB_DM',float('nan')),Track('USB_DP',60_000_000)],
    [Track('USB_DM',60_000_000),Track('USB_DP',60_500_000)],
    [Track('USB_DM',60_000_000,layer=0),Track('USB_DP',60_000_000)],
    [Via('USB_DM',60_000_000),Track('USB_DP',60_000_000)],
])
def test_unbounded_geometry_policy_keeps_positive_finite_skew_face_and_via_guards(tracks):
    with pytest.raises(ValueError):verifier()(board(tracks),max_length_nm=None)
