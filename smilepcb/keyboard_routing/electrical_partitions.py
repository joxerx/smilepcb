"""Project Native electrical graph groups onto stable conductor identities."""


def conductor_groups(graph):
    """Return non-zone UUID partitions, omitting zone-only islands.

    The same native zone UUID can represent multiple disconnected filled
    islands, so zone identities are deliberately excluded from this projection.
    """
    nodes = graph['nodes']
    projected = []
    seen = set()
    for indices in graph['groups'].values():
        members = set()
        for index in indices:
            node = nodes[index]
            if node.get('zone', False):
                continue
            uid = node['item'].m_Uuid.AsString()
            if uid in seen:
                raise ValueError('Conductor identity appears in multiple electrical groups')
            seen.add(uid)
            members.add(uid)
        if members:
            projected.append(frozenset(members))
    return projected
