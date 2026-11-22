"""Run Projectile Prism with the guarded ranged opening."""

import mechfight
from combat_bot import Bot
from ordnance_policy import build as reserve_ordnance
from route_guard import build as guard_ranged_route


if __name__ == "__main__":
    mechfight.run(guard_ranged_route(reserve_ordnance(Bot)))
