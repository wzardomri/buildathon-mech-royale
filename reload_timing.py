"""Keep the last useful gun rounds available during a brief exposed window."""

import math


class ReloadTiming:
    MAX_DEFERRAL = 0.90
    MODES = {"fight", "hold", "rotate", "cover"}

    def __init__(self, config):
        self.config = config
        self.deferred_at = None
        self.previous_ammo = None
        self.permitted_since = None

    def has_immediate_shot(self, world, me, foe):
        if foe["kind"] != "mech" or math.dist(me["pos"], foe["pos"]) > 145.0:
            return False
        if not any(weapon in {"gun", "laser"} for weapon in foe.get("weapons", ("gun",))):
            return False
        aim = foe.get("aim")
        if aim is None:
            return False

        stats = self.config["mech"]
        size = 1.0 + self.config["cubes"]["size_per_cube"] * foe.get("cubes", 0)
        source = (*foe["pos"][:2], foe["pos"][2] + stats["muzzle_height"] * size)
        body = (*me["pos"][:2], me["pos"][2] + me["stats"]["height"] * 0.52)
        dx, dy, dz = (b - a for a, b in zip(source, body))
        yaw = abs(math.remainder(math.atan2(dy, dx) - aim[0], math.tau))
        pitch = abs(math.atan2(dz, math.hypot(dx, dy)) - aim[1])
        if max(yaw / stats["turn_rate"], pitch / stats["pitch_rate"]) > 0.17:
            return False
        return not world.blocked_between(source, body)

    def defer(self, obs, world, action, mode):
        me, now = obs["self"], obs["t"]
        weapons = [weapon for weapon in me["weapons"] if weapon is not None]
        if len(weapons) != 1 or weapons[0]["kind"] != "gun":
            self.deferred_at = self.previous_ammo = None
            self.permitted_since = None
            return False
        gun = weapons[0]
        ammo = gun.get("ammo", 0)
        loading = gun.get("reload", 0.0) > 0.0
        if loading or (self.previous_ammo is not None and ammo > self.previous_ammo):
            self.deferred_at = None
            self.permitted_since = None
        self.previous_ammo = ammo

        if (not action.get("reload") or loading or ammo < 2 or mode not in self.MODES
                or not me["in_zone"] or any(action.get("fire", ()))):
            self.permitted_since = None
            return False
        if not any(self.has_immediate_shot(world, me, foe) for foe in obs["mechs"]):
            if self.permitted_since is None:
                self.permitted_since = now
            elif now - self.permitted_since >= 0.35:
                self.deferred_at = None
            return False
        self.permitted_since = None
        if self.deferred_at is not None and now - self.deferred_at >= self.MAX_DEFERRAL:
            return False
        if self.deferred_at is None:
            self.deferred_at = now
        return True
