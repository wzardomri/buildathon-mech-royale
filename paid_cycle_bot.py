"""Normal protocol entry applying the paid-cycle policy to exact scored Prism."""
import mechfight
from combat_bot import Bot as Prism
from paid_cycle import PaidCycle


class Bot(Prism):
    def __init__(self, hello):
        super().__init__(hello)
        self.paid_cycle = PaidCycle()

    def act(self, observation):
        native = super().act(observation)
        return self.paid_cycle.choose(observation, native, self) or native


if __name__ == "__main__":
    mechfight.run(Bot)
