"""Face a plausible unseen attacker between engagements."""

from collections import deque
import math


def yaw_delta(a, b):
    return math.remainder(a - b, math.tau)


class SoundAcquisition:
    def __init__(self, config):
        vision = config["vision"]
        sound = config["sound"]
        self.half_fov = math.radians(vision["fov_deg"] / 2)
        self.agreement = min(0.38, max(0.19, math.radians(sound["bearing_noise_deg"]) * 2.3))
        inner = max(vision["near_range"] + 2.0, sound["near"] * 0.75)
        near = sound["near"]
        mid = min(sound["mid"], vision["range"])
        self.probe_ranges = {
            "near": (inner, (inner + near) * 0.5),
            "mid": tuple(near + (mid - near) * fraction for fraction in (0.18, 0.5, 0.82)),
        }
        self.heard = deque(maxlen=12)
        self.cue = None
        self.heading = 0.0
        self.look_until = -math.inf
        self.rest_until = -math.inf

    def reset(self):
        self.heard.clear()
        self.cue = None
        self.look_until = -math.inf

    def open_view(self, me, world, bearing, bucket):
        x, y, z = me["pos"]
        eye = x, y, z + me["stats"]["eye_height"]
        cx, cy = math.cos(bearing), math.sin(bearing)
        for distance in self.probe_ranges[bucket]:
            px, py = x + distance * cx, y + distance * cy
            if world.cell((px, py)) in world.open and not world.blocked_between(
                eye, (px, py, world.ground(px, py) + me["stats"]["height"] * 0.55)
            ):
                return True
        return False

    def incoming_gun(self, obs, bearing):
        me = obs["self"]
        x, y, z = me["pos"]
        mx, my, mz = me["vel"]
        stats = me["stats"]
        for projectile in obs["projectiles"]:
            if projectile["kind"] != "gun":
                continue
            px, py, pz = projectile["pos"]
            vx, vy, vz = projectile["vel"]
            if abs(yaw_delta(math.atan2(-vy, -vx), bearing)) > 0.40:
                continue
            dx, dy = px - x, py - y
            relative_x, relative_y = vx - mx, vy - my
            speed_squared = relative_x * relative_x + relative_y * relative_y
            if speed_squared == 0.0:
                continue
            time = -(dx * relative_x + dy * relative_y) / speed_squared
            if (0.004 < time < 0.36 and -0.9 <= pz - z + (vz - mz) * time <= stats["height"] + 0.9
                    and math.hypot(dx + relative_x * time, dy + relative_y * time) <= stats["radius"] + 1.2):
                return True
        return False

    def choose(self, obs, action, mode, last_damage_at, world):
        now, me, zone = obs["t"], obs["self"], obs["zone"]
        if (mode not in ("hold", "rotate") or "take" in action or action.get("jump")
                or not me["grounded"] or not me["in_zone"] or me["hp"] < me["max_hp"] * 0.32
                or any(foe["kind"] == "mech" for foe in obs["mechs"])
                or now - last_damage_at < 2.0):
            self.reset()
            return None
        pos = me["pos"][:2]
        travel = max(0.0, math.dist(pos, zone["next_center"]) - zone["next_radius"])
        if zone["next_radius"] < 44.0 or (travel > 4.0 and
                zone["shrink_end"] - now < 10.0 + 2.0 * travel / me["stats"]["walk_speed"]):
            self.reset()
            return None

        while self.heard and now - self.heard[0][0] > 0.76:
            self.heard.popleft()
        current = sorted(
            (now - sound["age"], sound["bearing"], sound["dist"])
            for sound in obs["sounds"] if sound["kind"] == "gun" and sound["dist"] in self.probe_ranges
            and sound["age"] <= 0.18
        )[-6:]
        paired = []
        for heard_at, bearing, bucket in current:
            for last_at, last_bearing, last_bucket in reversed(self.heard):
                difference = yaw_delta(last_bearing, bearing)
                if (last_bucket == bucket and 0.14 <= heard_at - last_at <= 0.66
                        and abs(difference) <= self.agreement):
                    estimate = math.remainder(bearing + 0.5 * difference, math.tau)
                    paired.append((estimate, bucket))
                    break
        self.heard.extend(current)

        if now < self.look_until:
            if any(abs(yaw_delta(bearing, self.heading)) > 0.68 for bearing, _ in paired):
                self.reset()
                return None
            return self.heading
        if paired:
            bearing, bucket = paired[-1]
            if any(abs(yaw_delta(other, bearing)) > 0.68 for other, _ in paired):
                self.cue = None
                return None
            self.cue = now, bearing, bucket
        if self.cue is None or now < self.rest_until:
            return None
        cue_at, bearing, bucket = self.cue
        if (now - cue_at > 0.24
                or abs(yaw_delta(bearing, me["aim"][0])) < self.half_fov + 0.07
                or abs(yaw_delta(bearing, action["aim"][0])) < 0.47
                or not self.incoming_gun(obs, bearing)
                or not self.open_view(me, world, bearing, bucket)):
            return None
        self.heading = bearing
        self.look_until = now + 0.66
        self.rest_until = now + 1.45
        return self.heading
