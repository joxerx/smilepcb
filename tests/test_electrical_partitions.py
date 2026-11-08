import unittest

from smilepcb.keyboard_routing.electrical_partitions import conductor_groups
from smilepcb.keyboard_routing.joint_repair_contract import require_components


def graph(groups):
    nodes = []
    indexed = {}
    for group_id, rows in enumerate(groups):
        members = []
        for uid, zone in rows:
            members.append(len(nodes))
            nodes.append({'item': type('Item', (), {'m_Uuid': type('UUID', (), {'AsString': lambda self, u=uid: u})()})(), 'zone': zone})
        indexed[group_id] = members
    return {'nodes': nodes, 'groups': indexed}


class ElectricalPartitionTests(unittest.TestCase):
    def test_distinct_zone_islands_with_same_zone_uuid_do_not_collapse_conductors(self):
        projected = conductor_groups(graph([[('zone-1', True), ('track-a', False)], [('zone-1', True), ('track-b', False)]]))
        self.assertEqual(projected, [frozenset({'track-a'}), frozenset({'track-b'})])

    def test_zone_only_islands_do_not_change_projection(self):
        one = conductor_groups(graph([[('track-a', False)]]))
        with_zone = conductor_groups(graph([[('track-a', False), ('zone-a', True)], [('zone-b', True)]]))
        self.assertEqual(with_zone, one)

    def test_zone_bridge_projects_as_one_expected_conductor_group(self):
        before = conductor_groups(graph([[('track-a', False), ('zone-old', True), ('track-b', False)]]))
        after = conductor_groups(graph([[('track-a', False), ('zone-refilled', True), ('track-b', False)]]))
        self.assertEqual(before, [frozenset({'track-a', 'track-b'})])
        require_components(before, after)

    def test_real_conductor_split_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'split or merged'):
            require_components([frozenset({'a', 'b'})], [frozenset({'a'}), frozenset({'b'})])

    def test_duplicate_conductor_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'multiple electrical groups'):
            conductor_groups(graph([[('track-a', False)], [('track-a', False)]]))


if __name__ == '__main__':
    unittest.main()
