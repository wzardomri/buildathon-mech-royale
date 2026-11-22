"""Delay the first lightning pickup until it can support a real close fight."""

import math


def build(prism):
    class OrdnanceFirst(prism):
        def __init__(self, hello):
            super().__init__(hello)
            self.reservation_end = None
            self.withheld = {}
            self.first_withheld = None

        def close_fight(self, obs):
            me = obs["self"]
            origin = (*me["pos"][:2], me["pos"][2] + me["stats"]["muzzle_height"])
            reach = self.rules["weapons"]["lightning"]["range"] + 8.
            base_height = self.rules["mech"]["height"]
            growth = self.rules["cubes"]["size_per_cube"]
            for foe in obs["mechs"]:
                if foe["kind"] != "mech":
                    continue
                x, y, z = foe["pos"]
                body = x, y, z + base_height * (1. + foe.get("cubes", 0) * growth) * .52
                if math.dist(origin, body) <= reach and not self.world.blocked_between(origin, body):
                    return True
            return False

        def reserve(self, obs):
            me, now = obs["self"], obs["t"]
            if me["weapons"][1]:
                return "secondary equipped"
            if any(event["type"] == "kill" for event in obs["events"]):
                return "first credit"
            if me["hp"] <= .60 * me["max_hp"]:
                return "wounded"
            if not me["in_zone"]:
                return "outside ring"
            if now >= 55.:
                return "time elapsed"
            if self.close_fight(obs):
                return "close fight"
            return None

        def act(self, obs):
            if self.reservation_end is None:
                reason = self.reserve(obs)
                if reason:
                    self.reservation_end = {"t": obs["t"], "reason": reason}
                else:
                    arcs = [item for item in obs["pickups"] if item["kind"] == "lightning"]
                    if arcs:
                        recent = [dict(id=item["id"], pos=item["pos"]) for item in arcs]
                        self.withheld[obs["tick"]] = recent
                        if self.first_withheld is None:
                            self.first_withheld = dict(t=obs["t"], hp=obs["self"]["hp"], items=recent)
                        return super().act(dict(obs, pickups=[item for item in obs["pickups"]
                                                              if item["kind"] != "lightning"]))
            return super().act(obs)

    return OrdnanceFirst
