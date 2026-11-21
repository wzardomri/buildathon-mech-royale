"""Route to a visible ground weapon while there is time to disengage."""

from dataclasses import dataclass
import math


@dataclass
class Detour:
    pickup: int
    point: tuple
    expires: float


class OpeningLoot:
    LIMITS = {"laser": (150.0, 96.0), "lightning": (112.0, 66.0), "mortar": (73.0, 42.0)}
    COMBAT_SOUNDS = {"gun", "laser", "lightning", "mortar"}
    PATH_PROBE_LIMIT = 192

    def __init__(self, pickup_radius, short_limits):
        self.pickup_radius = pickup_radius
        self.short_limits = short_limits
        self.detour = None
        self.check_at = 0.0

    def path_distance(self, world, here, point, center, radius, tolerance):
        path = world.route(here, point, probe_limit=self.PATH_PROBE_LIMIT)
        if (not path or math.dist(path[-1], point) > tolerance
                or any(math.dist(node, center) > radius for node in path)):
            return math.inf
        return math.dist(here, path[0]) + sum(math.dist(a, b) for a, b in zip(path, path[1:])) + math.dist(path[-1], point)

    def choose(self, obs, world, inside_goal, enemy_distance, last_damage):
        me, zone, now = obs["self"], obs["zone"], obs["t"]
        if (now >= 43.0 or me["weapons"][1] is not None or not me["in_zone"]
                or me["hp"] < 0.84 * me["max_hp"] or enemy_distance < 153.0
                or now - last_damage < 3.0 or obs["projectiles"]
                or any(s["dist"] == "near" and s["kind"] in self.COMBAT_SOUNDS for s in obs["sounds"])):
            self.detour = None
            return None

        position = me["pos"][:2]
        ready_until = min(43.0, zone["shrink_start"] - 9.0)
        if self.detour is not None:
            detour = self.detour
            if (now < min(detour.expires, ready_until)
                    and (math.dist(position, detour.point) > self.pickup_radius + 1.0
                         or any(p["id"] == detour.pickup for p in obs["pickups"]))):
                return detour.point
            self.detour = None
            self.check_at = now + 2.0

        if not me["grounded"] or now >= ready_until or now < self.check_at or not obs["pickups"]:
            return None
        self.check_at = now + 0.55
        rotation = math.dist(position, inside_goal)
        speed = me["stats"]["walk_speed"]
        safe_radius = zone["next_radius"] - min(18.0, zone["next_radius"] / 4.0)
        center = zone["center"]
        radius = zone["radius"] - 4.0
        prospects = []
        for pickup in obs["pickups"]:
            kind = pickup["kind"]
            if kind not in self.LIMITS:
                continue
            point = tuple(pickup["pos"][:2])
            distance = math.dist(position, point)
            max_distance, extra_distance = self.LIMITS[kind]
            return_distance = max(0.0, math.dist(point, zone["next_center"]) - safe_radius)
            if (not self.short_limits[kind] < distance <= max_distance
                    or math.dist(point, zone["center"]) > zone["radius"] - 9.0
                    or distance + return_distance - rotation > extra_distance
                    or 1.35 * distance + 1.6 * return_distance > (ready_until - now) * speed
                    or abs(pickup["pos"][2] - world.ground(*point)) > 2.8):
                continue
            merit = (distance + 0.6 * return_distance) / extra_distance
            prospects.append((merit, pickup["id"], point, return_distance, extra_distance))

        for _, pickup, point, return_distance, extra_distance in sorted(prospects)[:3]:
            route = self.path_distance(world, position, point, center, radius, self.pickup_radius + 2.0)
            if route + 1.1 * return_distance - rotation > extra_distance:
                continue
            if return_distance > 3.5:
                cx, cy = zone["next_center"]
                dx, dy = point[0] - cx, point[1] - cy
                radial = math.hypot(dx, dy)
                goal = cx + safe_radius * dx / radial, cy + safe_radius * dy / radial
                return_distance = self.path_distance(world, point, goal, center, radius, 10.0)
            eta = 1.35 * route / speed
            if (route + 1.1 * return_distance - rotation > extra_distance
                    or now + eta + 1.6 * return_distance / speed > ready_until):
                continue
            self.detour = Detour(pickup, point, now + eta + 3.0)
            return point
        return None
