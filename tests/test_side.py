from enum import Enum
class Side(str, Enum):
    OVERBOUGHT = "overbought"
    OVERSOLD = "oversold"

    @property
    def display(self) -> str:
        return {
            Side.OVERBOUGHT: "超买🔴",
            Side.OVERSOLD: "超卖🟢",
        }[self]

print(Side.OVERBOUGHT.display)