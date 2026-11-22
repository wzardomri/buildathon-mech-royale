"""Retain native route authority when it approaches a recent ranged pickup."""

import math


def nearby_vector(position, target, limit):
    x, y = (b - a for a, b in zip(position, target))
    gap = math.hypot(x, y)
    if not 0. < gap <= limit:
        return None
    return x, y, gap


def in_resource_rings(item, zone):
    point = item["pos"][:2]
    return (math.dist(point, zone["center"]) <= zone["radius"] - 3.
            and math.dist(point, zone["next_center"]) <= zone["next_radius"] - 4.)


class RangedHistory:
    def __init__(self):
        self.items = {}

    def connecting_arc(self, obs):
        now, position, zone = obs["t"], obs["self"]["pos"][:2], obs["zone"]
        pickups = obs["pickups"]
        visible = {item["id"] for item in pickups}
        for item in pickups:
            if item["kind"] in ("laser", "mortar") and nearby_vector(position, item["pos"], 75.):
                self.items[item["id"]] = now, dict(item)
        self.items = {ident: entry for ident, entry in self.items.items() if now - entry[0] <= 2.5}
        if not self.items:
            return None
        for arc in pickups:
            if arc["kind"] != "lightning" or not in_resource_rings(arc, zone):
                continue
            short = nearby_vector(position, arc["pos"], 40.)
            if short is None:
                continue
            for ident, (seen, marked) in self.items.items():
                if ident in visible or not in_resource_rings(marked, zone):
                    continue
                long = nearby_vector(position, marked["pos"], 70.)
                if long is None or long[2] <= short[2]:
                    continue
                alignment = (short[0] * long[0] + short[1] * long[1]) / (short[2] * long[2])
                if alignment >= math.sqrt(3.) / 2.:
                    return dict(t=now, arc=arc["id"], previous_ranged=ident, seen=seen,
                                arc_distance=short[2], ranged_distance=long[2], cosine=alignment)
        return None


def build(ordnance):
    class RangedRouteGuard(ordnance):
        def __init__(self, hello):
            super().__init__(hello)
            self.ranged_history = RangedHistory()
            self.ranged_route = None

        def reserve(self, obs):
            reason = super().reserve(obs)
            if reason is not None:
                return reason
            self.ranged_route = self.ranged_history.connecting_arc(obs)
            return "route toward recent ranged gear" if self.ranged_route else None

    return RangedRouteGuard
