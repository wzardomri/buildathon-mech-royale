"""Use nearby map geometry to cut a second firing lane during a fight."""

from dataclasses import dataclass
import math

from route_bot import unit_toward
from weapon_control import target_body


@dataclass
class Plan:
    point: tuple
    primary: int
    screened: tuple
    expires: float
    boost: bool


class LaneIsolation:
    def __init__(self, config):
        self.config = config
        self.plan = None
        self.next_scan = 0.0

    def muzzle(self, foe, elapsed=0.0):
        x, y, z = (
            value + elapsed * speed for value, speed in zip(foe["pos"], foe["vel"])
        )
        size = 1.0 + self.config["cubes"]["size_per_cube"] * foe.get("cubes", 0)
        return x, y, z + self.config["mech"]["muzzle_height"] * size

    def zone_safe(self, obs, point):
        zone = obs["zone"]
        margin = obs["self"]["stats"]["radius"] + 4.0
        return (
            math.dist(point, zone["center"]) < zone["radius"] - margin
            and math.dist(point, zone["next_center"]) < zone["next_radius"] - margin
        )

    def clear_walk(self, world, me, point, ground):
        origin = me["pos"][:2]
        stats = me["stats"]
        length = math.dist(origin, point)
        ux, uy = unit_toward(origin, point)
        clearance = stats["radius"] * 0.78
        previous = me["pos"][2]
        steps = max(2, math.ceil(length / 2.0))
        slope = math.tan(math.radians(stats["max_slope_deg"]))
        for step in range(1, steps + 1):
            ratio = step / steps
            sample = (origin[0] + ratio * (point[0] - origin[0]),
                      origin[1] + ratio * (point[1] - origin[1]))
            if world.cell(sample) not in world.open:
                return False
            height = world.ground(*sample)
            if abs(height - previous) > min(stats["step_up"], slope * length / steps) + 0.05:
                return False
            previous = height
        for side, fraction in ((0, 0.84), (1, 0.45), (-1, 0.45)):
            dx, dy = -side * uy * clearance, side * ux * clearance
            height = fraction * stats["height"]
            start = origin[0] + dx, origin[1] + dy, me["pos"][2] + height
            end = point[0] + dx, point[1] + dy, ground + height
            if world.blocked_between(start, end):
                return False
        return True

    def shielded(self, world, me, point, ground, enemy):
        height = me["stats"]["height"]
        center = (*point, ground + height * 0.52)
        source = self.muzzle(enemy)
        future = self.muzzle(enemy, 0.45)
        if not world.blocked_between(source, center) or not world.blocked_between(future, center):
            return False
        if not world.blocked_between(source, (*point, ground + height * 0.83)):
            return False
        dx, dy = unit_toward(source, point)
        width = me["stats"]["radius"] * 0.76
        return all(
            world.blocked_between(source, (point[0] - sign * dy * width,
                                           point[1] + sign * dx * width, center[2]))
            for sign in (-1, 1)
        )

    def scan(self, obs, world, action, primary, others):
        me = obs["self"]
        origin = me["pos"][:2]
        own_muzzle = (*origin, me["pos"][2] + me["stats"]["muzzle_height"])
        hostile_nudge = []
        for foe in others:
            distance = math.dist(foe["pos"], me["pos"])
            if distance > (195.0 if "laser" in foe.get("weapons", ()) else 152.0):
                continue
            if not world.blocked_between(self.muzzle(foe), own_muzzle):
                hostile_nudge.append(foe)
        if not hostile_nudge:
            return None
        hostile_nudge.sort(key=lambda foe: math.dist(foe["pos"], me["pos"]))
        flanks = hostile_nudge[:3]
        if min(math.dist(foe["pos"], me["pos"]) for foe in [primary, *flanks]) < 23.0:
            return None

        body, _, _ = target_body(primary, self.config)
        toward = math.atan2(body[1] - origin[1], body[0] - origin[0])
        boost = me["energy"] > 52.0
        speed = me["stats"]["boost_speed" if boost else "walk_speed"]
        best = None
        for length in (8.0, 14.0, 20.0):
            if length / speed > 1.15:
                continue
            offsets = (1.57, -1.57, 2.35, -2.35, 0.82, -0.82, math.pi)
            for offset in offsets:
                ux, uy = math.cos(toward + offset), math.sin(toward + offset)
                point = origin[0] + length * ux, origin[1] + length * uy
                if world.cell(point) not in world.open or not self.zone_safe(obs, point):
                    continue
                ground = world.ground(*point)
                screened = tuple(foe["id"] for foe in flanks if self.shielded(world, me, point, ground, foe))
                if not screened or world.blocked_between((*point, ground + me["stats"]["muzzle_height"]), body):
                    continue
                if any(math.dist(point, foe["pos"][:2]) < 32.0 for foe in [primary, *flanks]):
                    continue
                align = ux * action["move"][0] + uy * action["move"][1]
                effort = length / speed - 0.16 * align
                score = len(screened), -effort
                if best is not None and score <= best[0]:
                    continue
                if self.clear_walk(world, me, point, ground):
                    expires = obs["t"] + min(1.95, length / speed + 0.76)
                    best = score, Plan(point, primary["id"], screened, expires, boost)
        return best[1] if best else None

    def choose(self, obs, world, action, primary):
        me, now = obs["self"], obs["t"]
        if (primary is None or not me["in_zone"] or not me["grounded"]
                or not self.zone_safe(obs, me["pos"][:2])
                or primary.get("hp", 1400.0) < 350.0 or "take" in action
                or any(weapon and weapon.get("charge", 0.0) > 0.0 for weapon in me["weapons"])):
            self.plan = None
            return None
        others = [foe for foe in obs["mechs"] if foe["kind"] == "mech" and foe["id"] != primary["id"]]
        if self.plan is None and now >= self.next_scan and others:
            self.next_scan = now + 0.65
            self.plan = self.scan(obs, world, action, primary, others)
        if self.plan is None:
            return None
        plan = self.plan
        if (now >= plan.expires or plan.primary != primary["id"]
                or not any(foe["id"] in plan.screened for foe in others)):
            self.plan = None
            return None
        position = me["pos"][:2]
        ground = world.ground(*plan.point)
        remaining = math.dist(position, plan.point)
        screened = [foe for foe in others if foe["id"] in plan.screened]
        if (not any(self.shielded(world, me, plan.point, ground, foe) for foe in screened)
                or remaining > 2.5 and not self.clear_walk(world, me, plan.point, ground)
                or any(math.dist(me["pos"], foe["pos"]) < 21.0 for foe in [primary, *screened])):
            self.plan = None
            return None
        ux, uy = unit_toward(position, plan.point)
        pace = min(1.0, remaining / 4.0)
        return (ux * pace, uy * pace), plan.boost and remaining >= 5.0 and me["energy"] > 33.0
