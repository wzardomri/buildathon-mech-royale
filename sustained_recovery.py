"""Let regeneration finish when the mech already has reliable shelter."""

from dataclasses import dataclass
import math

from weapon_control import target_body


@dataclass
class Hold:
    anchor: tuple
    started: float
    expires: float
    last_gain: float


class SustainedRecovery:
    ENTER_HP = 0.53
    EXIT_HP = 0.77
    MAX_HOLD = 18.0
    MODES = {"fight", "hold", "cover"}
    CLOSE_SOUNDS = {"step", "boost", "jet", "gun", "laser", "laser_charge", "lightning", "mortar", "explosion"}

    def __init__(self, config):
        self.config = config
        self.hold = None
        self.last_hp = None
        self.last_activity = -math.inf
        self.failed_activity = -math.inf
        self.lockout_until = -math.inf
        self.known = {}

    def source(self, foe, elapsed=0.0):
        size = 1.0 + self.config["cubes"]["size_per_cube"] * foe.get("cubes", 0)
        x, y, z = (a + b * elapsed for a, b in zip(foe["pos"], foe.get("vel", (0.0, 0.0, 0.0))))
        return x, y, z + self.config["mech"]["muzzle_height"] * size

    def shielded(self, world, me, point, ground, source):
        height = me["stats"]["height"]
        for fraction in (0.49, 0.84, 0.24):
            if not world.blocked_between(source, (*point, ground + height * fraction)):
                return False
        dx, dy = point[0] - source[0], point[1] - source[1]
        width = me["stats"]["radius"] * 0.91 / max(1.0, math.hypot(dx, dy))
        return all(
            world.blocked_between(source, (point[0] - side * dy * width, point[1] + side * dx * width, ground + height * 0.49))
            for side in (-1, 1)
        )

    def rings_safe(self, obs, point, entering):
        me, zone = obs["self"], obs["zone"]
        radius = me["stats"]["radius"]
        return (me["in_zone"] and zone["next_radius"] >= 24.0
                and zone["shrink_end"] - obs["t"] > (5.5 if entering else 1.0)
                and math.dist(point, zone["center"]) < zone["radius"] - radius - 5.0
                and math.dist(point, zone["next_center"]) < zone["next_radius"] - radius - min(8.0, 0.13 * zone["next_radius"]))

    def fence(self, obs, world, foes):
        me = obs["self"]
        position = tuple(me["pos"][:2])
        vx, vy = me["vel"][:2]
        glide = math.hypot(vx, vy) / (2.0 * me["stats"]["ground_accel"])
        stop = position[0] + vx * glide, position[1] + vy * glide
        points = [(position, me["pos"][2])]
        if math.dist(position, stop) > 0.2:
            if world.cell(stop) not in world.open or not self.rings_safe(obs, stop, self.hold is None):
                return False
            ground = world.ground(*stop)
            if abs(ground - me["pos"][2]) > me["stats"]["step_up"]:
                return False
            height = me["stats"]["height"] * 0.67
            if world.blocked_between((*position, me["pos"][2] + height), (*stop, ground + height)):
                return False
            points.append((stop, ground))
        for foe in foes:
            actual = self.source(foe)
            forecast = self.source(foe, 0.35)
            sources = (actual, forecast) if math.dist(actual, forecast) >= 1.1 else (actual,)
            if any(not self.shielded(world, me, point, ground, source)
                   for source in sources for point, ground in points):
                return False
        return True

    def flank_sound(self, obs, foes):
        x, y = obs["self"]["pos"][:2]
        expected = [math.atan2(foe["pos"][1] - y, foe["pos"][0] - x) for foe in foes]
        for sound in obs["sounds"]:
            if sound["dist"] != "near" or sound["age"] > 0.38 or sound["kind"] not in self.CLOSE_SOUNDS:
                continue
            if sound["kind"] in {"mortar", "explosion"}:
                return True
            if all(abs(math.remainder(sound["bearing"] - bearing, math.tau)) > 0.70 for bearing in expected):
                return True
        return False

    def viable_option(self, obs, world, action, live, resource_check):
        me = obs["self"]
        muzzle = (*me["pos"][:2], me["pos"][2] + me["stats"]["muzzle_height"])
        arcing = any(weapon and weapon["kind"] == "mortar" and firing
                     for weapon, firing in zip(me["weapons"], action.get("fire", ())))
        for foe in live:
            if 0.0 < foe.get("hp", 1400) < 335.0 and math.dist(me["pos"], foe["pos"]) <= 122.0:
                body, _, _ = target_body(foe, self.config)
                if arcing or not world.blocked_between(muzzle, body):
                    return True
        if not (obs["drops"] or obs["cubes"] or obs["pickups"]):
            return False
        option = resource_check(obs, tuple(me["pos"][:2]), live)
        if option is None:
            return False
        point, _ = option
        if math.dist(me["pos"][:2], point) > 24.0 or world.nearest_open(point, search=1) is None:
            return False
        ground = world.ground(*point)
        return self.rings_safe(obs, point, False) and all(
            self.shielded(world, me, point, ground, self.source(foe)) for foe in live
        )

    def abandon(self, now):
        if self.hold is not None:
            self.lockout_until = now + 1.0
            self.hold = None
        self.known.clear()

    def choose(self, obs, world, action, mode, last_damage, last_hit, resource_check):
        me, now = obs["self"], obs["t"]
        hp = me["hp"]
        lost_hp = self.last_hp is not None and hp < self.last_hp - 0.40
        gained_hp = self.last_hp is not None and hp > self.last_hp + 0.40
        damage = lost_hp or any(event["type"] == "damaged" for event in obs["events"])
        if damage or any(event["type"] == "hit" for event in obs["events"]):
            self.last_activity = now
        self.last_hp = hp
        self.last_activity = max(self.last_activity, last_damage, last_hit)

        position = tuple(me["pos"][:2])
        entering = self.hold is None
        charge = any(weapon and weapon.get("charge", 0.0) > 0.0 for weapon in me["weapons"])
        if (mode not in self.MODES or not me["grounded"] or not self.rings_safe(obs, position, entering)
                or now < self.lockout_until or damage or charge or "take" in action
                or hp >= me["max_hp"] * (self.ENTER_HP if entering else self.EXIT_HP)):
            self.abandon(now)
            return None

        regen = me["stats"]["regen_per_s"]
        delay = me["stats"]["regen_delay"]
        live = [
            foe for foe in obs["mechs"] if foe["kind"] == "mech" and foe.get("weapons") != []
            and math.dist(foe["pos"], me["pos"]) < 190.0
        ]
        if (regen <= 0.0 or (entering and (not live or now - self.last_activity > 8.0
                                         or self.last_activity <= self.failed_activity))
                or any(math.dist(foe["pos"], me["pos"]) < 21.0 for foe in live)):
            self.abandon(now)
            return None
        for foe in live:
            self.known[foe["id"]] = now, foe
        if live:
            foes = [foe for last, foe in self.known.values() if now - last <= 1.4]
        else:
            # Keep all recently tested directions as opponents fade from observation.
            latest = max((last for last, _ in self.known.values()), default=-math.inf)
            foes = [foe for last, foe in self.known.values() if latest - last <= 1.4]
        if (not 1 <= len(foes) <= 3 or self.flank_sound(obs, foes)
                or any(p["kind"] == "mortar" and math.dist(p["pos"], me["pos"]) < 38.0 for p in obs["projectiles"])
                or not self.fence(obs, world, foes) or self.viable_option(obs, world, action, live, resource_check)):
            self.abandon(now)
            return None

        if entering:
            wait = max(0.0, self.last_activity + delay - now)
            healing = (me["max_hp"] * self.EXIT_HP - hp) / regen
            self.hold = Hold(position, now, now + min(self.MAX_HOLD, max(5.5, wait + healing + 0.85)), -math.inf)
        plan = self.hold
        if gained_hp:
            plan.last_gain = now
        no_progress = now - max(self.last_activity + delay, plan.started, plan.last_gain) > 1.45
        if now >= plan.expires or no_progress or math.dist(position, plan.anchor) > 4.0:
            if no_progress:
                self.failed_activity = self.last_activity
            self.abandon(now)
            return None

        return min(foes, key=lambda foe: math.dist(foe["pos"], me["pos"]))
