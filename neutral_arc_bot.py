"""Run Projectile Prism with its idle secondary available for neutral defense."""

import mechfight
from arc_policy import NeutralArc
from combat_bot import Bot as Prism


class Bot:
    def __init__(self, hello):
        self.prism = Prism(hello)
        self.policy = NeutralArc()

    def __getattr__(self, name):
        return getattr(self.prism, name)

    def act(self, observation):
        native = self.prism.act(observation)
        return self.policy.choose(observation, native, self.prism) or native


if __name__ == "__main__":
    mechfight.run(Bot)
