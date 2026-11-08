"""Matrix faces follow physical pad access, independently of the MCU face."""
from smilepcb.keyboard_routing.matrix_layers import select_matrix_layers


def test_back_only_rows_use_back_even_with_front_mcu():
 rows=[{'family':'rows','layers':[2]} for _ in range(5)]+[{'family':'columns','layers':[0,2]} for _ in range(8)]
 result=select_matrix_layers(rows,faces=(0,2),preferred_row_layer=0)
 assert (result['row_layer'],result['column_layer'])==(2,0)
 assert result['pad_layer_misses']==0 and result['candidates'][0]['pad_layer_misses']==5


def test_mirroring_the_physical_pads_mirrors_the_policy():
 rows=[{'family':'rows','layers':[0]}]*5+[{'family':'columns','layers':[0,2]}]*8
 result=select_matrix_layers(rows,faces=(0,2),preferred_row_layer=2)
 assert (result['row_layer'],result['column_layer'])==(0,2)


def test_through_hole_tie_preserves_the_previous_preference():
 rows=[{'family':'rows','layers':[0,2]},{'family':'columns','layers':[0,2]}]
 for layer in (0,2):
  result=select_matrix_layers(rows,faces=(0,2),preferred_row_layer=layer)
  assert result['row_layer']==layer
  assert result==select_matrix_layers(list(reversed(rows)),faces=(0,2),preferred_row_layer=layer)


def test_count_is_pad_access_misses_not_a_claim_of_physical_via_minimum():
 rows=[{'family':'rows','layers':[2]}]*3+[{'family':'columns','layers':[2]}]*5
 result=select_matrix_layers(rows,faces=(0,2),preferred_row_layer=2)
 assert result['row_layer']==0 and result['pad_layer_misses']==3
 assert result['scope']=='non-controller matrix copper-root layer access heuristic'


def test_connected_hotswap_lands_count_as_one_two_face_terminal():
 rows=[{'family':'rows','root':'diode','layers':[2]},
       {'family':'columns','root':'socket','layers':[0,2]},
       {'family':'columns','root':'socket','layers':[2]},
       {'family':'columns','root':'socket','layers':[2]}]
 result=select_matrix_layers(rows,faces=(0,2),preferred_row_layer=0)
 assert (result['row_layer'],result['column_layer'])==(2,0)
 assert result['pad_layer_misses']==0


def test_same_net_disconnected_lands_are_not_merged():
 rows=[{'family':'rows','root':'diode','layers':[2]},
       {'family':'columns','root':'socket-hole','layers':[0,2]},
       {'family':'columns','root':'loose-land-a','layers':[2]},
       {'family':'columns','root':'loose-land-b','layers':[2]}]
 result=select_matrix_layers(rows,faces=(0,2),preferred_row_layer=2)
 assert result['row_layer']==0 and result['pad_layer_misses']==1


def test_board_adapter_unions_real_root_faces_and_excludes_controller_only_roots(monkeypatch):
 import sys
 from types import SimpleNamespace as S
 from smilepcb.keyboard_routing.matrix_layers import select_board_matrix_layers
 pad=lambda uid,net:S(m_Uuid=S(AsString=lambda:uid),GetNetname=lambda:net,GetNumber=lambda:'1')
 diode=pad('diode','r');land=pad('land','c');hole=pad('hole','c');source=pad('source','r')
 fp=lambda ref,pads:S(GetReference=lambda:ref,Pads=lambda:pads)
 board=S(GetFootprints=lambda:[fp('key-diode',[diode]),fp('socket',[land,hole]),fp('chip',[source])])
 nodes=[{'item':diode,'shapes':{2:object()}},{'item':land,'shapes':{2:object()}},
        {'item':hole,'shapes':{0:object(),2:object()}},{'item':source,'shapes':{0:object()}}]
 graph={'nodes':nodes,'groups':{0:[0],1:[1,2],3:[3]}}
 def copper(actual,nets,deadline,**kw):
  assert actual is board and nets=={'r','c'} and deadline==19 and kw=={'include_zones':False}
  return graph
 monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.native_graph',S(copper_graph=copper))
 result=select_board_matrix_layers(board,{'parts':[{'ref':'chip','region':'controller'}]},
   {'r':'rows','c':'columns'},faces=(0,2),preferred_row_layer=0,deadline=19)
 assert result['field_root_count']==2 and result['pad_layer_misses']==0
 assert (result['row_layer'],result['column_layer'])==(2,0)
