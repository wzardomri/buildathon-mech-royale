"""Alternate loaded-rifle pressure with lateral movement during empty reload."""
import math

from projectile_evasion import clears_route, projected_motion
from weapon_control import weapon_plan


class CombatCadence:
    def __init__(self, slot):
        self.slot = int(slot)
        self.target_id = None
        self.side = 1.0
        self.phase = None
        self.counts = {"fire_phase": 0, "reload_phase": 0, "mode_veto": 0,
                       "dodge_veto": 0, "laser_veto": 0, "no_target": 0,
                       "retreat_veto": 0, "finish_veto": 0, "route_veto": 0,
                       "safe": 0, "unchanged": 0, "changed": 0, "shot_veto": 0}
        self.first = []

    @staticmethod
    def _unit(x, y):
        length = math.hypot(x, y)
        return (x / length, y / length) if length > 1e-8 else (0.0, 0.0)

    def _direction(self, bot, me, foe, phase):
        dx, dy = foe["pos"][0] - me["pos"][0], foe["pos"][1] - me["pos"][1]
        distance = math.hypot(dx, dy)
        toward = self._unit(dx, dy)
        if phase == "fire":
            radial = 0.82 if distance > 75.0 else 0.18 if distance > 32.0 else -0.55
        else:
            radial = 0.10 if distance > 75.0 else -0.10 if distance > 32.0 else -0.65
        tangential = math.sqrt(max(0.0, 1.0 - radial * radial))
        tangent = (-toward[1] * self.side, toward[0] * self.side)
        return self._unit(radial * toward[0] + tangential * tangent[0],
                          radial * toward[1] + tangential * tangent[1])

    def _preserves_ready_gun(self, bot, me, foe, action, native, candidate):
        if not (action.get("fire") or (False,))[0]:
            return True
        gun = me["weapons"][0]
        if not gun or gun["kind"] != "gun" or not gun["ammo"] or gun["reload"]:
            return True
        yaw, pitch = me["aim"]
        goal = action["aim"]
        turn = me["stats"]["turn_rate"]
        pitch_rate = me["stats"]["pitch_rate"]
        shortest = math.remainder(goal[0] - yaw, math.tau)
        for index in (1, 2):
            elapsed = index * 0.1
            dyaw = math.copysign(min(turn * elapsed, abs(shortest)), shortest) if shortest else 0.0
            dpitch = math.copysign(min(pitch_rate * elapsed, abs(goal[1] - pitch)),
                                   goal[1] - pitch) if goal[1] != pitch else 0.0
            future_foe = dict(foe, pos=tuple(p + elapsed * v for p, v in zip(foe["pos"], foe["vel"])))
            aim = (yaw + dyaw, pitch + dpitch)
            before_pos = (*native[index], me["pos"][2])
            after_pos = (*candidate[index], me["pos"][2])
            before = dict(me, aim=aim, pos=before_pos)
            after = dict(me, aim=aim, pos=after_pos)
            _, before_fire = weapon_plan(before, future_foe, bot.world, bot.rules)
            _, after_fire = weapon_plan(after, future_foe, bot.world, bot.rules)
            if before_fire[0] and not after_fire[0]:
                self.counts["shot_veto"] += 1
                return False
        return True

    def apply(self, bot, obs, action):
        me = obs["self"]
        gun = me["weapons"][0]
        phase = None
        if (gun and gun["kind"] == "gun" and action.get("fire") and action["fire"][0]
                and gun["ammo"] > 0 and gun["reload"] <= 0.0):
            phase = "fire"
        elif (gun and gun["kind"] == "gun" and gun["ammo"] <= 0
              and gun["reload"] >= 0.8):
            phase = "reload"

        if phase is None:
            self.phase = None
            return
        self.counts[phase + "_phase"] += 1

        if (bot.active_mode != "fight" or not me["grounded"] or not me["in_zone"]
                or action.get("jump") or action.get("take") is not None):
            self.counts["mode_veto"] += 1
            self.phase = None
            return
        if bot.dodge_plan is not None:
            self.counts["dodge_veto"] += 1
            self.phase = None
            return
        if any(w and w["kind"] == "laser" and
               (w.get("charge", 0.0) > 0.0 or i < len(action.get("fire", ())) and action["fire"][i])
               for i, w in enumerate(me["weapons"])):
            self.counts["laser_veto"] += 1
            self.phase = None
            return

        foe = bot.fight_target(obs, me, me["pos"][:2])
        if foe is None:
            self.counts["no_target"] += 1
            self.phase = None
            return
        distance = math.dist(me["pos"], foe["pos"])
        low = me["hp"] < 0.25 * me["max_hp"]
        can_fight = me["in_zone"] and (not low or distance < 26.0 or foe.get("hp", 1300.0) < 300.0)
        if not can_fight:
            self.counts["retreat_veto"] += 1
            self.phase = None
            return
        if foe.get("hp", 1300.0) < 190.0 and distance < 145.0:
            self.counts["finish_veto"] += 1
            self.phase = None
            return

        if self.target_id != foe["id"]:
            self.target_id = foe["id"]
            self.side = 1.0 if (self.slot + int(foe["id"])) % 2 == 0 else -1.0
            self.phase = None
        if phase == "reload" and self.phase != "reload":
            self.side = -self.side
        self.phase = phase

        direction = self._direction(bot, me, foe, phase)
        native = projected_motion(me, action["move"], action["boost"])
        candidate = projected_motion(me, direction, action["boost"])
        if not clears_route(bot.world, obs, native, candidate):
            self.counts["route_veto"] += 1
            return
        if not self._preserves_ready_gun(bot, me, foe, action, native, candidate):
            return
        self.counts["safe"] += 1
        if math.dist(direction, action["move"]) < 0.08:
            self.counts["unchanged"] += 1
            return
        if len(self.first) < 24:
            self.first.append(dict(t=round(obs["t"], 2), phase=phase, target=foe["id"],
                                   native=list(action["move"]), changed=list(direction),
                                   ammo=gun["ammo"], reload=round(gun["reload"], 2),
                                   hp=round(me["hp"], 1), distance=round(distance, 1)))
        action["move"] = list(direction)
        bot.previous_move = tuple(direction)
        self.counts["changed"] += 1
