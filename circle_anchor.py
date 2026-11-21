"""Find a ground route when the normal late-circle goal snaps outside safety."""

import heapq
import math


class CircleAnchor:
    def __init__(self):
        self.next_check = -math.inf
        self.anchor = None
        self.urgent = None

    def choose(self, obs, world, projected, center, radius):
        me, zone, now = obs["self"], obs["zone"], obs["t"]
        position = me["pos"][:2]
        urgent = not me["in_zone"]
        if urgent != self.urgent:
            self.anchor = None
            self.next_check = -math.inf
            self.urgent = urgent
        if not (12.0 <= radius <= 80.0 and me["grounded"]) or math.dist(position, projected) < 3.5:
            self.anchor = None
            return projected
        safe_radius = radius - max(2.5, me["stats"]["radius"])
        cell = world.nearest_open(projected, search=8)
        if world.edges.get(cell) and math.dist(world.points[cell], center) <= safe_radius:
            self.anchor = None
            return projected
        if math.dist(position, center) <= safe_radius:
            self.anchor = position
            return position
        if self.anchor is not None and math.dist(self.anchor, center) > safe_radius:
            self.anchor = None
        if now < self.next_check:
            return self.anchor or projected

        self.next_check = now + 1.05
        current_center, current_radius = zone["center"], zone["radius"]
        require_inside_current = me["in_zone"]
        lo_x, lo_y = world.cell((center[0] - safe_radius - world.GRID, center[1] - safe_radius - world.GRID))
        hi_x, hi_y = world.cell((center[0] + safe_radius + world.GRID, center[1] + safe_radius + world.GRID))
        choices = []
        for x in range(lo_x, hi_x + 1):
            for y in range(lo_y, hi_y + 1):
                cell = (x, y)
                if not world.edges.get(cell):
                    continue
                point = world.points[cell]
                ring_gap = math.dist(point, center)
                if ring_gap <= safe_radius:
                    choices.append((math.dist(position, point) + 0.25 * ring_gap, point))

        alternatives = []
        for _, point in heapq.nsmallest(4, choices):
            route = world.route(position, point, probe_limit=240)
            if not route or require_inside_current and any(
                math.dist(node, current_center) > current_radius + 1.5 for node in route
            ):
                continue
            walk = math.dist(position, route[0]) + sum(math.dist(a, b) for a, b in zip(route, route[1:]))
            alternatives.append((walk + 0.25 * math.dist(point, center), point))
        self.anchor = min(alternatives)[1] if alternatives else None
        return self.anchor or projected
