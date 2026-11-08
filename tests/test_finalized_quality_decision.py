from smilepcb.keyboard_routing.finalized_research import quality_decision


def report(before, after, coverage_before=102967653, coverage_after=98238923):
    return dict(before_coverage={'columns': coverage_before}, after_coverage={'columns': coverage_after},
                before_objective={'columns': {'score_nm': before}},
                after_objective={'columns': {'score_nm': after}})


def test_electrically_feasible_ansi61_regression_cannot_claim_quality_admission():
    assert quality_decision(report(9387285, -38103428)) == 'feasibility-only-quality-rejected'


def test_score_gain_does_not_override_coverage_loss_or_saved_check_requirement():
    assert quality_decision(report(0, 1000000)) == 'feasibility-only-quality-rejected'
    assert quality_decision(report(0, 1000000, 100, 100)) == 'quality-admitted-awaiting-saved-ERC-DRC'
    assert quality_decision(None) == 'no-candidate'
