from engine.risk import RiskEngine, RiskAssessment


def test_default_level():
    engine = RiskEngine(
        position_caps={1: 0.3, 2: 0.5, 3: 0.7, 4: 0.9, 5: 1.0},
        stop_loss_pcts={1: 0.03, 2: 0.05, 3: 0.08, 4: 0.12, 5: 0.15},
    )
    result = engine.assess({})
    assert isinstance(result, RiskAssessment)
    assert 1 <= result.level <= 5
    assert result.position_cap > 0
    assert result.stop_loss_pct > 0
