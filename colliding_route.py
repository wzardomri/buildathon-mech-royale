"""Use public-map swept edge geometry after a confirmed ground stall."""

import json
import math
from hashlib import sha256
from pathlib import Path


def bounds(a, b, radius):
    return (min(a[0], b[0]) - radius, max(a[0], b[0]) + radius,
            min(a[1], b[1]) - radius, max(a[1], b[1]) + radius)


def meets(box, prism):
    x1, x2, y1, y2 = box
    px1, px2, py1, py2 = prism.outline.bounds
    return x1 < px2 and x2 > px1 and y1 < py2 and y2 > py1


def obstacles(world, a, b, radius):
    x1, x2, y1, y2 = bounds(a, b, radius)
    return {
        prism for bucket in world.ray_bucket_keys(x1, x2, y1, y2)
        for prism in world.ray_buckets.get(bucket, ())
        if meets((x1, x2, y1, y2), prism)
    }


def sweep(world, me, a, b, nearby):
    if not nearby:
        return set()
    stats = me["stats"]
    radius = stats["radius"] + 0.04
    dx, dy = b[0] - a[0], b[1] - a[1]
    count = max(1, math.ceil(math.hypot(dx, dy) / 0.83))
    hits = set()
    for index in range(1, count + 1):
        point = a[0] + dx * index / count, a[1] + dy * index / count
        ground = world.ground(*point)
        for prism in nearby:
            if (prism not in hits and ground + stats["step_up"] < prism.top
                    and ground + stats["height"] > prism.base
                    and prism.outline.intersects_disc(point, radius)
                    and not any(hole.contains_disc(point, radius) for hole in prism.holes)):
                hits.add(prism)
    return hits


class CollidingRoute:
    def __init__(self, world):
        self.next_try = -math.inf
        self.active = False
        self.table = []
        self.confirmed = set()
        self.observed = {}
        try:
            data = json.loads(Path(__file__).with_name("collision_edges.json").read_bytes())
        except (OSError, ValueError):
            return
        if (data["signature"] == world.arena_signature and data["grid"] == world.GRID
                and data["depth"] == world.depth):
            self.table = [(r, h, step, {key: set(edges) for key, edges in positions.items()})
                          for r, h, step, positions in data["table"]]

    def graph_repair(self, world, stats, position, goal):
        by_prism = next((edges for radius, height, step, edges in self.table
                          if stats["radius"] <= radius + 0.001 and stats["height"] <= height + 0.001
                          and stats["step_up"] >= step), None)
        if by_prism is None:
            return None
        forbidden = set().union(*(by_prism.get(key, ()) for key in self.confirmed))
        if not forbidden:
            return None
        stride = world.depth

        def blocked(a, b):
            if a > b:
                a, b = b, a
            return (a[0] * stride + a[1]) * 65536 + b[0] * stride + b[1] in forbidden

        return world.route(position, goal, obstruction=blocked)

    def choose(self, obs, world, position, goal, existing):
        now, me, zone = obs["t"], obs["self"], obs["zone"]
        if (not self.table or now < self.next_try or not existing or not me["in_zone"] or not me["grounded"]
                or abs(me["pos"][2] - world.ground(*position)) > 0.82
                or math.dist(position, existing[0]) > 13.0):
            return None
        self.next_try = now + 1.10
        a, b = position, existing[0]
        blocking = sweep(world, me, a, b, obstacles(world, a, b, me["stats"]["radius"] + 0.04))
        if not blocking:
            return None
        for prism in blocking:
            if prism not in self.observed:
                fields = (prism.base, prism.top, prism.outline.edges,
                          tuple(hole.edges for hole in prism.holes))
                self.observed[prism] = sha256(repr(fields).encode("ascii")).hexdigest()[:24]
        if len(self.confirmed | {self.observed[prism] for prism in blocking}) > 7:
            self.confirmed.clear()
        self.confirmed.update(self.observed[prism] for prism in blocking)
        route = self.graph_repair(world, me["stats"], position, goal)
        if not route or math.dist(route[-1], goal) > 8.0:
            return None
        first = route[0]
        if (math.dist(position, first) > 13.0
                or sweep(world, me, position, first, obstacles(world, position, first, me["stats"]["radius"] + 0.04))):
            return None
        point, walk = position, 0.0
        radii = zone["radius"] - me["stats"]["radius"]
        for next_point in route:
            if math.dist(next_point, zone["center"]) > radii:
                return None
            walk += math.dist(point, next_point)
            point = next_point
        if now + 2.8 + 1.20 * walk / me["stats"]["walk_speed"] > zone["shrink_end"]:
            return None
        self.active = True
        return route
