"""Find a nearby route out of an observed laser's line of sight."""

import math


def forecast(me, move, boost, seconds):
    stats = me["stats"]
    speed = stats["boost_speed"] if boost else stats["walk_speed"]
    velocity = tuple(me["vel"][:2])
    desired = tuple(axis * speed for axis in move)
    change = math.dist(velocity, desired)
    if change == 0:
        return tuple(p + v * seconds for p, v in zip(me["pos"], velocity))
    acceleration = stats["ground_accel"]
    ramp = min(seconds, change / acceleration)
    return tuple(
        p + start * ramp + 0.5 * (finish - start) * acceleration / change * ramp * ramp + finish * (seconds - ramp)
        for p, start, finish in zip(me["pos"], velocity, desired)
    )


def is_aligned(enemy, source, point, radius, laser_range):
    dx, dy, dz = (target - muzzle for target, muzzle in zip(point, source))
    horizontal = math.hypot(dx, dy)
    if math.hypot(horizontal, dz) > laser_range + radius:
        return False
    yaw = math.atan2(dy, dx)
    pitch = math.atan2(dz, horizontal)
    tolerance = math.atan2(radius + 8.0, max(1.0, horizontal))
    return abs(math.remainder(yaw - enemy["aim"][0], math.tau)) <= tolerance and abs(pitch - enemy["aim"][1]) <= tolerance


class LaserDefense:
    def __init__(self, config):
        self.laser_range = config["weapons"]["laser"]["range"]
        self.charge_time = config["weapons"]["laser"]["charge"]
        self.short_range = config["weapons"]["lightning"]["range"]
        self.short_energy = config["weapons"]["lightning"]["energy"]
        self.muzzle_height = config["mech"]["muzzle_height"]
        self.size_per_cube = config["cubes"]["size_per_cube"]
        self.charging_since = {}

    def choose(self, obs, world, enemies, move, boost, goal):
        me, now = obs["self"], obs["t"]
        stats = me["stats"]
        charges = [enemy for enemy in enemies if enemy.get("charging")]
        self.charging_since = {enemy["id"]: self.charging_since.get(enemy["id"], now) for enemy in charges}
        if not charges:
            return move, boost, None, False

        own_height = stats["height"]
        position = tuple(me["pos"][:2])
        point = (*position, me["pos"][2] + own_height * 0.55)
        for enemy in charges[:3]:
            muzzle_height = self.muzzle_height * (1.0 + self.size_per_cube * enemy.get("cubes", 0))
            source = (*enemy["pos"][:2], enemy["pos"][2] + muzzle_height)
            if is_aligned(enemy, source, point, stats["radius"], self.laser_range) and not world.blocked_between(source, point):
                break
        else:
            return move, boost, None, False

        close = math.dist(source, point) <= self.short_range
        short_ready = any(
            weapon and weapon["kind"] == "lightning" and weapon.get("cooldown", 0.0) < 0.15
            for weapon in me["weapons"]
        )
        if close and short_ready and me["energy"] >= max(12.0, 3.0 * self.short_energy):
            return move, boost, enemy, False
        if not me["grounded"]:
            return move, boost, enemy, False

        since = self.charging_since[enemy["id"]]
        remaining = self.charge_time - (now - since)
        deadline = max(0.22, min(0.70, remaining + 0.08))
        zone_center, zone_radius = obs["zone"]["center"], obs["zone"]["radius"]

        def sheltered(candidate):
            ground = world.ground(*candidate)
            middle = (*candidate, ground + own_height * 0.55)
            high = (*candidate, ground + own_height * 0.83)
            return world.blocked_between(source, middle) and world.blocked_between(source, high)

        unchanged = forecast(me, move, boost, deadline)
        if sheltered(unchanged):
            return move, boost, enemy, False

        x, y = position
        heading = math.atan2(y - source[1], x - source[0])
        choices = []
        for angle in (0.0, 0.80, -0.80, 1.40, -1.40, 2.12, -2.12):
            next_move = math.cos(heading + angle), math.sin(heading + angle)
            use_boost = boost or me["energy"] >= 29.0
            projected = forecast(me, next_move, use_boost, deadline)
            if world.cell(projected) not in world.open:
                continue
            if me["in_zone"] and math.dist(projected, zone_center) > zone_radius - 1.0:
                continue
            if not sheltered(projected):
                continue
            ground = world.ground(*projected)
            if world.blocked_between(point, (*projected, ground + own_height * 0.55)):
                continue
            mid = forecast(me, next_move, use_boost, deadline * 0.70)
            score = 13.0 * sheltered(mid) - 0.4 * math.dist(projected, goal) - 2.0 * use_boost
            choices.append((score, next_move, use_boost))
        if not choices:
            return move, boost, enemy, False
        _, evasive_move, boost_needed = max(choices)
        return evasive_move, boost_needed, enemy, True
