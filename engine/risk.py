from dataclasses import dataclass


@dataclass
class RiskAssessment:
    level: int
    score: float
    position_cap: float
    stop_loss_pct: float
    reasoning: str


class RiskEngine:

    WEIGHTS = {
        "trend": 0.35,
        "volatility": 0.25,
        "macro": 0.20,
        "sentiment": 0.20,
    }

    LEVEL_THRESHOLDS = [
        (0.0, 1), (0.3, 2), (0.5, 3), (0.7, 4), (0.85, 5),
    ]

    def __init__(self, position_caps: dict, stop_loss_pcts: dict):
        self.position_caps = position_caps
        self.stop_loss_pcts = stop_loss_pcts

    def assess(self, indicators: dict) -> RiskAssessment:
        # TODO: wire real indicators (MA direction, ATR, breadth, VIX, etc.)
        score = 0.5
        level = self._score_to_level(score)
        return RiskAssessment(
            level=level,
            score=score,
            position_cap=self.position_caps.get(level, 0.7),
            stop_loss_pct=self.stop_loss_pcts.get(level, 0.08),
            reasoning="Placeholder -- indicators not yet wired.",
        )

    def _score_to_level(self, score: float) -> int:
        for threshold, level in reversed(self.LEVEL_THRESHOLDS):
            if score >= threshold:
                return level
        return 1
