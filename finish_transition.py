"""Collect a nearby confirmed reward and reset before the next engagement."""

import math

from route_bot import unit_toward


class FinishTransition:
    def __init__(self, config):
        self.config = config
        self.positions = {}
        self.corpse = None
        self.killed_at = -math.inf

    def update(self, obs):
        now = obs["t"]
        for foe in obs["mechs"]:
            if foe["kind"] == "mech":
                self.positions[foe["id"]] = (now, foe["pos"])
        for event in obs["events"]:
            if event["type"] == "kill":
                last_seen, point = self.positions.get(event["target"], (-math.inf, None))
                if now - last_seen < 1.0:
                    self.corpse = point
                    self.killed_at = now

    def safe_walk(self, world, me, goal):
        position = me["pos"][:2]
        x, y = unit_toward(position, goal)
        stats = me["stats"]
        clearance = 0.78 * stats["radius"]
        next_ground = world.ground(*goal)
        for step in range(1, 5):
            sample = tuple(a + (b - a) * step / 4 for a, b in zip(position, goal))
            if world.cell(sample) not in world.open:
                return False
        if abs(next_ground - me["pos"][2]) > 2.2:
            return False
        for side, height in ((0, 0.82), (1, 0.40), (-1, 0.40)):
            offset = -y * side * clearance, x * side * clearance
            high = stats["height"] * height
            start = position[0] + offset[0], position[1] + offset[1], me["pos"][2] + high
            end = goal[0] + offset[0], goal[1] + offset[1], next_ground + high
            if world.blocked_between(start, end):
                return False
        return True

    def shelter(self, world, me, foes, point):
        own_body = (*point, world.ground(*point) + me["stats"]["height"] * 0.65)
        for foe in foes:
            distance = math.dist(foe["pos"][:2], point)
            if distance > 148.0:
                continue
            base = self.config["mech"]
            scale = 1.0 + self.config["cubes"]["size_per_cube"] * foe.get("cubes", 0)
            muzzle = (*foe["pos"][:2], foe["pos"][2] + base["muzzle_height"] * scale)
            if distance < 42.0 or not world.blocked_between(muzzle, own_body):
                return False
        return True

    def choose(self, obs, world, action, mode):
        self.update(obs)
        me, zone, now = obs["self"], obs["zone"], obs["t"]
        elapsed = now - self.killed_at
        if (self.corpse is None or elapsed >= 2.25 or not me["in_zone"] or not me["grounded"]
                or mode not in {"hold", "fight", "rotate", "farm", "cube", "weapon"}
                or "take" in action or action.get("jump") or any(action.get("fire", ()))
                or me["hp"] < me["max_hp"] * 0.45
                or obs["projectiles"] or any(event["type"] == "damaged" for event in obs["events"])
                or any(sound["kind"] in {"gun", "lightning", "laser", "mortar"} and sound["dist"] == "near"
                       for sound in obs["sounds"])
                or any(weapon and weapon.get("charge", 0.0) > 0.0 for weapon in me["weapons"])):
            return None, False

        position = me["pos"][:2]
        if abs(me["pos"][2] - world.ground(*position)) > 0.85:
            return None, False
        foes = [foe for foe in obs["mechs"] if foe["kind"] == "mech" and foe.get("hp", 1) > 0]
        if not self.shelter(world, me, foes, position):
            return None, False
        gun = me["weapons"][0]
        reload = (elapsed <= 1.2 and gun is not None and gun["kind"] == "gun"
                  and 0 < gun.get("ammo", 0) < 0.6 * gun["stats"]["mag"] and not gun.get("reload", 0.0))
        if me["cubes"] >= self.config["cubes"]["stat_cap"]:
            return None, reload
        margin = me["stats"]["radius"] + 4.0
        pickup = self.config["cubes"]["pickup_radius"]
        reach = min(11.0, (2.25 - elapsed) * me["stats"]["walk_speed"] + pickup)
        options = []
        for item in obs["cubes"]:
            point = tuple(item["pos"][:2])
            distance = math.dist(position, point)
            if (not pickup - 0.15 < distance <= reach or math.dist(self.corpse, item["pos"]) >= 11.0
                    or math.dist(point, zone["center"]) >= zone["radius"] - margin
                    or math.dist(point, zone["next_center"]) >= zone["next_radius"] - margin
                    or abs(item["pos"][2] - world.ground(*point)) > 2.5):
                continue
            options.append((distance, point))
        for distance, point in sorted(options)[:3]:
            direction = unit_toward(position, point)
            alignment = sum(a * b for a, b in zip(direction, action["move"]))
            if distance > 7.0 and alignment < -0.12:
                continue
            if self.safe_walk(world, me, point) and self.shelter(world, me, foes, point):
                pace = min(1.0, distance / 5.0)
                return tuple(axis * pace for axis in direction), reload
        return None, reload
