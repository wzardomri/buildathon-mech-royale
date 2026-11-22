"""Keep Prism's remaining rifle rounds during a verified, continuing paid engagement."""
from collections import deque

from weapon_control import aim_close, weapon_plan


class PaidCycle:
    def __init__(self):
        self.payments = deque()
        self.target = None
        self.last_ammo = None

    def choose(self, obs, native, state):
        now = obs["t"]
        me = obs["self"]
        gun, secondary = me["weapons"]
        visible = {actor["id"]: actor for actor in obs["mechs"] if actor["kind"] == "mech"}

        for event in obs["events"]:
            if event["type"] == "hit" and event.get("weapon") == "gun":
                self.payments.append((now, event["target"]))
        while self.payments and now - self.payments[0][0] > 1.35:
            self.payments.popleft()

        if not gun or gun["kind"] != "gun" or secondary:
            self.target = None
            self.last_ammo = None
            return None

        ammo = gun["ammo"]
        if self.last_ammo is not None and ammo > self.last_ammo:
            self.target = None
        self.last_ammo = ammo

        if (gun["reload"] or ammo <= 0 or not me["in_zone"]
                or me["hp"] < 0.25 * me["max_hp"]
                or state.active_mode not in {"fight", "hold", "rotate", "cover"}
                or native.get("jump") or now < state.recovery_until
                or any(native.get(key) is not None for key in ("take", "drop", "pick"))):
            self.target = None
            return None

        if len(self.payments) < 2 or now - self.payments[-1][0] > 0.75:
            self.target = None
            return None
        ident = self.payments[-1][1]
        foe = visible.get(ident)
        if foe is None or foe.get("hp") is None or any(target != ident for _, target in self.payments):
            self.target = None
            return None
        if self.target != ident:
            self.target = None
        if not native.get("reload") or any(native.get("fire", ())):
            return None

        # Start a new commitment only against a healthy current target. A near-finish
        # can already be covered by a round in flight when the reload request is issued.
        if self.target is None and foe["hp"] <= ammo * gun["stats"]["damage"]:
            return None

        # Ask the scored planner whether its real turret could pay this exact target
        # with a loaded, ready rifle. Leave the actual aim, motion and triggers intact.
        recovered = dict(gun, cooldown=0., reload=0.)
        ready = dict(me, weapons=[recovered, None])
        desired, fire = weapon_plan(ready, foe, state.world, state.rules)
        if not fire[0] or not aim_close(native["aim"], desired, .02, .02):
            self.target = None
            return None

        self.target = ident
        return dict(native, reload=False)
