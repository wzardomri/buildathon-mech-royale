"""Briefly close on a weakened opponent who is already leaving the fight."""

import math

from weapon_control import advance, target_body


class WeakPursuit:
    MAX_BURST = 1.45
    REST = 4.0
    MODES = {"fight", "hold", "rotate"}
    HEARD_THREATS = {"step", "boost", "jet", "gun", "laser", "laser_charge", "lightning", "mortar", "explosion"}

    def __init__(self, config):
        self.config = config
        self.last_hit = {}
        self.target = None
        self.until = -math.inf
        self.rest_until = -math.inf
        self.lightning_range = config["weapons"]["lightning"]["range"]
        for upgrade in config["upgrades"]:
            for effect in upgrade["effects"]:
                if effect["path"] == "weapons.lightning.range":
                    self.lightning_range += max(0.0, effect.get("add", 0.0)) * upgrade["max"]
                    self.lightning_range *= max(1.0, effect.get("mul", 1.0)) ** upgrade["max"]

    def stop(self, now):
        if self.target is not None:
            self.rest_until = now + self.REST
        self.target = None

    def equipped(self, me, action, distance, entering):
        triggers = action.get("fire", (False, False))
        if any(weapon and weapon["kind"] == "laser" and (weapon.get("charge", 0.0) > 0.0 or firing)
               for weapon, firing in zip(me["weapons"], triggers)):
            return False
        for weapon in me["weapons"]:
            if not weapon or weapon.get("cooldown", 0.0) > 0.38:
                continue
            kind, stats = weapon["kind"], weapon["stats"]
            if kind == "gun" and not weapon.get("reload", 0.0) and weapon.get("ammo", 0) >= (3 if entering else 2):
                return True
            if kind == "laser" and distance <= stats["range"]:
                return True
            if kind == "mortar" and 30.0 <= distance <= 0.85 * stats["speed"] ** 2 / stats["gravity"]:
                return True
            if kind == "lightning" and distance <= stats["range"] and me["energy"] > 30.0 + 4.0 * stats["energy"]:
                return True
        return False

    def forecast(self, me, move, boosted, seconds):
        stats = me["stats"]
        speed = stats["boost_speed"] if boosted else stats["walk_speed"]
        velocities = me["vel"][:2]
        wanted = tuple(axis * speed for axis in move)
        change = math.dist(velocities, wanted)
        if change <= 0.001:
            return advance(me["pos"][:2], velocities, seconds)
        ramp = min(seconds, change / stats["ground_accel"])
        fraction = ramp * stats["ground_accel"] / change
        return tuple(
            start + (velocity + end) * 0.5 * ramp + end * (seconds - ramp)
            for start, velocity, end in zip(me["pos"], velocities,
                                           (v + fraction * (w - v) for v, w in zip(velocities, wanted)))
        )

    def next_distance(self, zone, me, point):
        return max(0.0, math.dist(point, zone["next_center"]) - (zone["next_radius"] - me["stats"]["radius"] - 5.0))

    def ring_safe(self, obs, point, current_need):
        me, zone, now = obs["self"], obs["zone"], obs["t"]
        if math.dist(point, zone["center"]) > zone["radius"] - me["stats"]["radius"] - 5.0:
            return False
        need = self.next_distance(zone, me, point)
        if need <= 0.0:
            return zone["shrink_end"] - now > 3.0
        return (zone["next_radius"] >= 40.0 and need <= current_need + 3.0
                and zone["shrink_start"] - now > 9.5 + 2.0 * need / me["stats"]["walk_speed"])

    def route_clear(self, obs, world, points, target, danger):
        me = obs["self"]
        previous = tuple(me["pos"][:2])
        ground = me["pos"][2]
        height = me["stats"]["height"]
        buffer = me["stats"]["radius"] * 0.72
        needs = self.next_distance(obs["zone"], me, previous)
        for point in points:
            if world.cell(point) not in world.open or not self.ring_safe(obs, point, needs):
                return False
            if math.dist(point, target[:2]) <= danger + 3.5:
                return False
            new_ground = world.ground(*point)
            dx, dy = point[0] - previous[0], point[1] - previous[1]
            walkway = math.hypot(dx, dy)
            if abs(new_ground - ground) > me["stats"]["step_up"] + 0.12:
                return False
            if walkway > 0.12:
                cross_x, cross_y = -dy * buffer / walkway, dx * buffer / walkway
                for side, fraction in ((0, 0.83), (-1, 0.43), (1, 0.43)):
                    start = previous[0] + side * cross_x, previous[1] + side * cross_y, ground + height * fraction
                    end = point[0] + side * cross_x, point[1] + side * cross_y, new_ground + height * fraction
                    if world.blocked_between(start, end):
                        return False
            previous, ground = point, new_ground
        return True

    def choose(self, obs, world, foe, action, mode):
        now, me = obs["t"], obs["self"]
        for event in obs["events"]:
            if event["type"] == "hit" and 0 <= event["target"] < 16 and event.get("amount", 0.0) > 0.0:
                self.last_hit[event["target"]] = now
        entering = self.target is None
        if (foe is None or mode not in self.MODES or "take" in action or action.get("reload")
                or not me["grounded"] or not me["in_zone"] or obs["zone"]["next_radius"] < 24.0
                or (entering and now < self.rest_until)
                or (not entering and (now >= self.until or foe["id"] != self.target))):
            self.stop(now)
            return None

        if (me["hp"] < me["max_hp"] * (0.65 if entering else 0.57)
                or me["energy"] < me["max_energy"] * (0.58 if entering else 0.37)):
            self.stop(now)
            return None
        health, maximum = foe.get("hp"), foe.get("max_hp", self.config["mech"]["hp"])
        if (not self.config["vision"]["show_enemy_hp"] or not isinstance(health, (int, float))
                or isinstance(health, bool) or not math.isfinite(health) or not isinstance(maximum, (int, float))
                or isinstance(maximum, bool) or not math.isfinite(maximum) or not (0 < health < min(0.30 * maximum, 460.0))
                or now - self.last_hit.get(foe["id"], -math.inf) > 4.2):
            self.stop(now)
            return None

        position = tuple(me["pos"][:2])
        dx, dy = (b - a for a, b in zip(position, foe["pos"]))
        distance = math.hypot(dx, dy)
        danger = self.lightning_range + 5.0 if "lightning" in foe.get("weapons", ("lightning",)) else 34.0
        minimum = max(danger + (8.0 if entering else 3.5), 58.0 if entering else 34.0)
        if not minimum < distance <= 147.0 or not self.equipped(me, action, distance, entering):
            self.stop(now)
            return None
        velocity = foe.get("vel", (0.0, 0.0, 0.0))
        ux, uy = dx / distance, dy / distance
        if velocity[0] * ux + velocity[1] * uy < (3.4 if entering else 1.2):
            self.stop(now)
            return None
        for other in obs["mechs"]:
            if other["kind"] == "mech" and other["id"] != foe["id"]:
                if math.dist(position, other["pos"][:2]) < 133.0 or math.dist(foe["pos"], other["pos"]) < 93.0:
                    self.stop(now)
                    return None

        heading = math.atan2(dy, dx)
        flank = any(sound["kind"] in self.HEARD_THREATS and sound["dist"] == "near" and sound["age"] < 0.32
                    and abs(math.remainder(sound["bearing"] - heading, math.tau)) > 0.86 for sound in obs["sounds"])
        body = target_body(foe, self.config)[0]
        eye = (*position, me["pos"][2] + me["stats"]["eye_height"])
        muzzle = (*position, me["pos"][2] + me["stats"]["muzzle_height"])
        facing = abs(math.remainder(heading - me["aim"][0], math.tau))
        if flank or facing > 0.72 or world.blocked_between(eye, body) or world.blocked_between(muzzle, body):
            self.stop(now)
            return None

        future = advance(body, velocity, 0.65)
        baseline = self.forecast(me, action["move"], action.get("boost", False), 0.65)
        baseline_gap = math.dist(baseline, future[:2])
        side = 1 if ux * action["move"][1] - uy * action["move"][0] >= 0.0 else -1
        for offset in (0.24 * side, -0.24 * side, 0.46 * side, -0.46 * side, 0.0):
            candidate = math.cos(heading + offset), math.sin(heading + offset)
            points = [self.forecast(me, candidate, True, seconds) for seconds in (0.19, 0.34, 0.50, 0.65)]
            end = points[-1]
            future_gap = math.dist(end, future[:2])
            if baseline_gap - future_gap < 2.4 or future_gap > distance + 1.0:
                continue
            if not self.route_clear(obs, world, points, foe["pos"], danger):
                continue
            end_muzzle = (*end, world.ground(*end) + me["stats"]["muzzle_height"])
            if world.blocked_between(end_muzzle, future):
                continue
            if entering:
                self.target = foe["id"]
                self.until = now + self.MAX_BURST
            return candidate

        self.stop(now)
        return None
