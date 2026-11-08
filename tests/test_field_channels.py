"""Native obstacle events must expose legal channels between coarse samples."""

def test_a_narrow_channel_between_grid_samples_is_offered():
 from smilepcb.keyboard_routing.field_geometry import row_channels
 ys=row_channels(7000000,500000,19500000,[(500000,8098000),(8152000,19500000)])
 assert 8125000 in ys
 assert ys[0]==7000000
 assert len(set(ys))==len(ys) and all(500000<=y<=19500000 for y in ys)

def test_column_templates_use_whole_stagger_and_exact_terminals():
 from smilepcb.keyboard_routing.field_geometry import column_paths
 paths=column_paths((0,0),(5000000,10000000))
 assert [(0,0),(5000000,5000000),(5000000,10000000)] in paths
 assert [(0,0),(0,5000000),(5000000,10000000)] in paths
 assert not column_paths((0,0),(10000000,5000000))

def test_median_derived_row_bounds_produce_native_integer_coordinates():
 from smilepcb.keyboard_routing.field_geometry import row_channels
 ys=row_channels(7000000,-2500000.0,16500000.0,[])
 assert 7000000 in ys and all(type(y) is int for y in ys)

def test_row_domain_includes_keys_when_diodes_are_offset():
 from smilepcb.keyboard_routing.field_geometry import row_domain
 assert row_domain([7000000,7000000],[16000000,16000000],19000000)==(-2500000,25500000)
