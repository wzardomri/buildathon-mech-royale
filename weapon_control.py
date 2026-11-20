"""Weapon-specific trigger and lead decisions for the Mech Royale bot."""

import math


def displacement(origin, target):
    return tuple(b - a for a, b in zip(origin, target))


def dot(first, second):
    return sum(a * b for a, b in zip(first, second))


def advance(origin, velocity, seconds):
    return tuple(p + v * seconds for p, v in zip(origin, velocity))


def torso_angles(direction, stats):
    x, y, z = direction
    yaw = math.atan2(y, x)
    pitch = math.atan2(z, math.hypot(x, y))
    if not stats["min_pitch"] <= pitch <= stats["max_pitch"]:
        return None
    return yaw, pitch


def aim_close(actual, desired, tolerance_yaw, tolerance_pitch):
    return (desired is not None
            and abs(math.remainder(desired[0] - actual[0], math.tau)) <= tolerance_yaw
            and abs(desired[1] - actual[1]) <= tolerance_pitch)


def lead_time(offset, velocity, speed, barrel_offset):
    # Solve |offset + velocity*t| = speed*t + barrel_offset for the
    # first nonnegative intercept. The speed of a gun round exceeds mech speed.
    a = speed * speed - dot(velocity, velocity)
    b = speed * barrel_offset - dot(offset, velocity)
    c = dot(offset, offset) - barrel_offset * barrel_offset
    if c <= 0:
        return 0.0
    if a <= 0 or b * b + a * c < 0:
        return None
    return c / (math.sqrt(b * b + a * c) + b)


def ballistic(offset, velocity, speed, gravity):
    def equation(t):
        x, y, z = advance(offset, velocity, t)
        z += 0.5 * gravity * t * t
        return x * x + y * y + z * z - speed * speed * t * t

    low = 0.0
    for index in range(1, 51):
        high = index * 0.10
        if equation(high) <= 0:
            for _ in range(10):
                mid = (low + high) * 0.5
                if equation(mid) > 0:
                    low = mid
                else:
                    high = mid
            x, y, z = advance(offset, velocity, high)
            return high, (x / high, y / high, z / high + 0.5 * gravity * high)
        low = high
    return None


def target_body(target, config):
    kind = target.get("kind", "crate")
    if kind == "mech":
        scale = 1 + config["cubes"]["size_per_cube"] * target.get("cubes", 0)
        radius, height = config["mech"]["radius"] * scale, config["mech"]["height"] * scale
    else:
        entity = config["npc"] if kind == "npc" else config["crates"]
        radius, height = entity["radius"], entity["height"]
    x, y, z = target["pos"]
    return (x, y, z + height * 0.52), radius, height


def aim_tolerances(point, origin, body_radius, body_height):
    separation = max(1.0, math.dist(point, origin))
    yaw = max(0.014, min(0.14, math.atan2(body_radius, separation) + 0.009))
    pitch = max(0.017, min(0.16, math.atan2(body_height * 0.38, separation) + 0.01))
    return yaw, pitch


def weapon_plan(me, target, world, config, retreat=False):
    """Return an aim and a fire state for both held slots."""
    body, radius, height = target_body(target, config)
    target_vel = target.get("vel", (0.0, 0.0, 0.0))
    self_stats, actual = me["stats"], me["aim"]
    muzzle = me["pos"][0], me["pos"][1], me["pos"][2] + self_stats["muzzle_height"]
    offset = displacement(muzzle, body)
    distance = math.dist(muzzle, body)
    barrel = self_stats["radius"] + 0.5
    tolerances = aim_tolerances(body, muzzle, radius, height)
    clear = not world.blocked_between(muzzle, body)
    plans = []
    for slot, weapon in enumerate(me["weapons"]):
        if not weapon:
            continue
        kind, stats = weapon["kind"], weapon["stats"]
        ready = weapon.get("cooldown", 0.0) < 0.10
        if kind == "gun":
            time = lead_time(offset, target_vel, stats["speed"], barrel)
            if time is None or time * stats["speed"] > stats["range"]:
                continue
            position = advance(body, target_vel, time)
            aim = torso_angles(displacement(muzzle, position), self_stats)
            if aim is not None and not world.blocked_between(muzzle, position):
                usable = ready and weapon.get("ammo", 1) > 0 and not weapon.get("reload", 0)
                plans.append((2 if usable else 0, slot, aim, usable, tolerances))
        elif kind == "laser" and (ready or weapon.get("charge", 0.0) > 0):
            aim = torso_angles(advance(offset, target_vel, 0.04), self_stats)
            charge = weapon.get("charge", 0.0)
            if charge > 0:
                reached = charge + 0.04 >= stats["charge"]
                aligned = aim_close(actual, aim, *tolerances)
                # Keep the trigger held until the beam has a clear shot.
                release = clear and aligned and (reached or retreat) and distance <= stats["range"]
                fire = (not release and not retreat and charge < stats["charge"] + stats["hold_s"] - 0.22)
                plans.append((5, slot, aim or actual, fire, tolerances))
            elif not retreat and aim is not None and clear and distance <= stats["range"] * 0.97:
                start = aim_close(actual, aim, max(0.26, tolerances[0]), max(0.19, tolerances[1]))
                if distance > config["weapons"]["lightning"]["range"] or "lightning" not in target.get("weapons", ()):
                    plans.append((4, slot, aim, start, tolerances))
        elif kind == "lightning" and ready and clear:
            aim = torso_angles(offset, self_stats)
            tolerance = math.radians(stats["cone_deg"]) * 0.82, 1.0
            if aim is not None and distance <= stats["range"] * 0.98 and me["energy"] >= max(12, stats["energy"] * 3):
                # The arc selects by yaw. Retain the tighter gun aim if both can hit.
                plans.append((3, slot, aim, aim_close(actual, aim, *tolerance), tolerance))
        elif kind == "mortar" and ready:
            min_range = max(17.0, stats["splash_radius"] * 2)
            if distance < min_range or distance > stats["speed"] ** 2 / stats["gravity"] + 25:
                continue
            launch = me["pos"][0], me["pos"][1], me["pos"][2] + self_stats["height"] + 0.5
            feet = body[0], body[1], target["pos"][2] + min(1.0, height * 0.35)
            shot = ballistic(displacement(launch, feet), target_vel, stats["speed"], stats["gravity"])
            if shot is None:
                continue
            time, velocity = shot
            aim = torso_angles(velocity, self_stats)
            if aim is None:
                continue
            previous = launch
            for point in range(1, 9):
                t = time * point / 8
                x, y, z = advance(launch, velocity, t)
                current = x, y, z - 0.5 * stats["gravity"] * t * t
                if world.blocked_between(previous, current):
                    break
                previous = current
            else:
                mortar_tolerance = min(tolerances[0] * 1.25, 0.10), min(tolerances[1], 0.055)
                plans.append((3, slot, aim, aim_close(actual, aim, *mortar_tolerance), mortar_tolerance))
    if not plans:
        aim = torso_angles(offset, self_stats)
        return aim or actual, [False, False]
    _, main_slot, aim, chosen_fire, main_tolerance = max(plans, key=lambda plan: plan[0])
    if me["weapons"][main_slot]["kind"] == "gun":
        chosen_fire = chosen_fire and aim_close(actual, aim, *main_tolerance)
    triggers = [False, False]
    triggers[main_slot] = chosen_fire
    for _, slot, secondary, can_fire, tolerance in plans:
        if slot == main_slot:
            continue
        if me["weapons"][slot]["kind"] == "gun":
            can_fire = can_fire and aim_close(actual, secondary, *tolerance)
        triggers[slot] = can_fire and aim_close(aim, secondary, *tolerance)
    return aim, triggers
