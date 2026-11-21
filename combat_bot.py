"""Defend aggressively in open lanes while retaining the route policy."""

import math

import mechfight as mf
from route_bot import Bot as RouteBot
from weapon_control import target_body, weapon_plan


def unit(x, y):
    size = max(0.0001, math.hypot(x, y))
    return x / size, y / size


class Bot(RouteBot):
    name = "offensive-upgrades-cadence"

    def __init__(self, hello):
        super().__init__(hello)
        self.rules = hello["config"]
        self.slot = hello["slot"]

    def fight_target(self, obs, me, position):
        muzzle = (position[0], position[1], me["pos"][2] + me["stats"]["muzzle_height"])
        picks = []
        laser = any(w and w["kind"] == "laser" for w in me["weapons"])
        for foe in obs["mechs"]:
            if foe["kind"] != "mech":
                continue
            body, _, _ = target_body(foe, self.rules)
            distance = math.dist(muzzle, body)
            if distance > (190 if laser else 150) or self.world.blocked_between(muzzle, body):
                continue
            health = foe.get("hp", 1300)
            pursuit = distance * 0.55 + health * 0.02
            if health < 400 and distance < 120:
                pursuit -= 18
            if foe["id"] == self.combat_target:
                pursuit -= 14
            if "lightning" in foe.get("weapons", ()) and distance < 42:
                pursuit -= 14
            picks.append((pursuit, foe["id"], foe))
        return min(picks, default=(0, 0, None))[2]

    def strafe(self, obs, foe, position, preferred):
        me = obs["self"]
        dx, dy = foe["pos"][0] - position[0], foe["pos"][1] - position[1]
        distance = math.hypot(dx, dy)
        ux, uy = unit(dx, dy)
        # Alternate early enough that the apparent velocity is often stale by
        # the time a midrange round arrives. Use deterministic varied lengths.
        timing = int((obs["t"] + self.slot * 0.37) * (1.8 if distance > 65 else 1.2))
        first = 1 if (timing + timing // 3) & 1 else -1
        if distance < 18 or "lightning" in foe.get("weapons", ()) and distance < 42:
            radial = -0.65
        elif distance > 69:
            radial = 0.54 if me["hp"] > me["max_hp"] * 0.28 else 0.16
        else:
            radial = 0.05
        zone = obs["zone"]
        radius = zone["radius"] - min(7.0, zone["radius"] * 0.1)
        height = me["stats"]["height"] * 0.55
        torso = (*position, me["pos"][2] + height)
        for sign in (first, -first):
            x, y = unit(radial * ux - sign * uy, radial * uy + sign * ux)
            near = position[0] + 9 * x, position[1] + 9 * y
            if self.world.cell(near) in self.world.open and math.dist(near, zone["center"]) <= radius:
                if not self.world.blocked_between(torso, (*near, self.world.ground(*near) + height)):
                    return [x, y]
        return preferred

    def act(self, obs):
        action = super().act(obs)
        me = obs["self"]
        position = tuple(me["pos"][:2])
        if self.active_mode not in {"fight", "hold", "rotate", "cover"}:
            return action
        foe = self.fight_target(obs, me, position)
        if foe is None or obs["t"] < self.recovery_until:
            return action
        if self.active_mode == "cover":
            nearby = sum(enemy["kind"] == "mech" and math.dist(enemy["pos"], me["pos"]) < 100
                         for enemy in obs["mechs"])
            # The route policy can label simply moving away as cover. Keep
            # real shelter and outnumbered retreats, but contest an open duel.
            if nearby > 1 or self.cover_goal is None:
                return action
            x, y = self.cover_goal
            body_height = me["stats"]["height"] * 0.55
            source = (*foe["pos"][:2], foe["pos"][2] + self.rules["mech"]["muzzle_height"])
            if self.world.blocked_between(source, (x, y, self.world.ground(x, y) + body_height)):
                return action
        distance = math.dist(position, foe["pos"][:2])
        low = me["hp"] < 0.25 * me["max_hp"]
        can_fight = me["in_zone"] and (not low or distance < 26 or foe.get("hp", 1300) < 300)
        zone = obs["zone"]
        required = max(0.0, math.dist(position, zone["next_center"]) - zone["next_radius"])
        slack = zone["shrink_end"] - obs["t"]
        if can_fight and (required < 3.0 or slack > 12.0 + 2.0 * required / me["stats"]["walk_speed"]):
            action["move"] = self.strafe(obs, foe, position, action["move"])
            action["boost"] = me["energy"] > 47 and (distance < 42 or distance > 130)
            self.previous_move = tuple(action["move"])
            self.sprinting = action["boost"]
        aim, triggers = weapon_plan(me, foe, self.world, self.rules, retreat=not can_fight)
        action["aim"] = aim
        action["fire"] = triggers if can_fight or distance < 29 else [False, False]
        if any(action["fire"]):
            action["reload"] = False
        return action


if __name__ == "__main__":
    mf.run(Bot)
