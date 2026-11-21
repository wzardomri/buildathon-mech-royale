"""Use short, acceleration-aware sidesteps for likely shots in an arena duel."""

import math


STEP = 0.1
STEPS = 6


def projected_motion(me, move, boost):
    x, y = me["pos"][:2]
    vx, vy = me["vel"][:2]
    stats = me["stats"]
    speed = stats["boost_speed"] if boost else stats["walk_speed"]
    target_x, target_y = move[0] * speed, move[1] * speed
    positions = [(x, y)]
    for _ in range(STEPS):
        dx, dy = target_x - vx, target_y - vy
        change = math.hypot(dx, dy)
        fraction = min(1.0, STEP * stats["ground_accel"] / change) if change else 1.0
        next_x, next_y = vx + fraction * dx, vy + fraction * dy
        x += STEP * 0.5 * (vx + next_x)
        y += STEP * 0.5 * (vy + next_y)
        vx, vy = next_x, next_y
        positions.append((x, y))
    return positions


def closest_approach(projectile, motion, bottom, top):
    x, y, z = projectile["pos"]
    vx, vy, vz = projectile["vel"]
    best_distance = math.inf
    best_time = None
    for index, (start, end) in enumerate(zip(motion, motion[1:])):
        elapsed = index * STEP
        height = z + vz * elapsed
        if vz:
            dz = vz * STEP
            entered, exited = sorted(((bottom - height) / dz, (top - height) / dz))
            low, high = max(0.0, entered), min(1.0, exited)
            if low > high:
                continue
        elif not bottom <= height <= top:
            continue
        else:
            low, high = 0.0, 1.0
        ax, ay = x + vx * elapsed - start[0], y + vy * elapsed - start[1]
        dx, dy = vx * STEP + start[0] - end[0], vy * STEP + start[1] - end[1]
        length_squared = dx * dx + dy * dy
        optimum = -(ax * dx + ay * dy) / length_squared if length_squared else low
        ratio = max(low, min(high, optimum))
        distance = math.hypot(ax + dx * ratio, ay + dy * ratio)
        if distance < best_distance:
            best_distance, best_time = distance, elapsed + ratio * STEP
    return best_distance, best_time


def clears_route(world, obs, baseline, candidate):
    me, zone = obs["self"], obs["zone"]
    height = me["stats"]["height"] * 0.5
    middle, end = candidate[STEPS // 2], candidate[-1]
    if world.cell(middle) not in world.open or world.cell(end) not in world.open:
        return False
    current_margin = me["stats"]["radius"] + 2.0
    if math.dist(end, zone["center"]) > zone["radius"] - current_margin:
        return False
    existing_outside = max(0.0, math.dist(baseline[-1], zone["next_center"]) - zone["next_radius"])
    if math.dist(end, zone["next_center"]) > zone["next_radius"] + existing_outside + 1.2:
        return False
    torso = (*me["pos"][:2], me["pos"][2] + height)
    half = (*middle, world.ground(*middle) + height)
    finish = (*end, world.ground(*end) + height)
    return not (world.blocked_between(torso, half) or world.blocked_between(half, finish))


def evasive_move(obs, world, move, boost, allow_burst=False):
    me = obs["self"]
    if not me["grounded"] or math.hypot(*move) < 0.55:
        return None
    projectiles = [
        projectile for projectile in obs["projectiles"]
        if projectile["kind"] == "gun"
        and math.dist(projectile["pos"], me["pos"]) <= (STEP * STEPS) * math.hypot(*projectile["vel"]) + 16.0
    ]
    if not 1 <= len(projectiles) <= 8:
        return None

    baseline = projected_motion(me, move, boost)
    bottom = me["pos"][2]
    top = bottom + me["stats"]["height"]
    radius = me["stats"]["radius"]
    for projectile in projectiles:
        distance, seconds = closest_approach(projectile, baseline, bottom + 0.7, top - 0.7)
        if seconds is not None and distance <= 0.82 * radius and 0.17 <= seconds <= 0.55:
            impact = tuple(p + v * seconds for p, v in zip(projectile["pos"], projectile["vel"]))
            if not world.blocked_between(projectile["pos"], impact):
                break
    else:
        return None

    x, y = move
    options = [(boost, (0.52, -0.52, 0.85, -0.85, 1.18, -1.18))]
    reserve = 0.53 * me["max_energy"] + STEP * STEPS * me["stats"]["boost_drain"]
    if allow_burst and not boost and me["energy"] >= reserve:
        options.append((True, (0.0, 0.52, -0.52, 0.85, -0.85)))
    for burst, angles in options:
        for angle in angles:
            cosine, sine = math.cos(angle), math.sin(angle)
            candidate = x * cosine - y * sine, x * sine + y * cosine
            motion = projected_motion(me, candidate, burst)
            if any(closest_approach(shot, motion, bottom - 0.55, top + 0.55)[0] < radius + 0.65 for shot in projectiles):
                continue
            if clears_route(world, obs, baseline, motion):
                return candidate, burst, seconds, projectile["id"]
    return None
