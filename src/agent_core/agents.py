"""Single-mission gate agents. Pure deterministic logic, no I/O, no LLM calls.

Binary disqualifiers, no scoring: a failed gate returns COMPLIANCE_EXIT
immediately and never reaches the LLM layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.templates import COMPLIANCE_EXIT, get_template

INCOME_MULTIPLIER = 2.5


@dataclass
class LeadContext:
    rent: float
    stage: int = 1
    occupants: int = 1
    has_pets: bool = False
    has_kids: bool = False
    commute_minutes: int | None = None
    move_in_days: int | None = None
    monthly_income: float | None = None


@dataclass
class GateResult:
    passed: bool
    reply: str
    gate: str
    deterministic: bool = True


class OccupancyEvaluator:
    """Gate 1: single adult, no pets, no kids."""

    gate = "gate_1_occupancy"

    def evaluate(self, ctx: LeadContext) -> GateResult:
        if ctx.occupants > 1 or ctx.has_pets or ctx.has_kids:
            return GateResult(passed=False, reply=COMPLIANCE_EXIT, gate=self.gate)
        return GateResult(
            passed=True,
            reply=get_template(self.gate, "availability"),
            gate=self.gate,
        )


class ShowingCoordinator:
    """Gate 2: location/commute fit and move-in timing, then showing slot."""

    gate = "gate_2_location"

    def evaluate(self, ctx: LeadContext) -> GateResult:
        if ctx.commute_minutes is None or ctx.move_in_days is None:
            return GateResult(
                passed=True,
                reply=get_template(self.gate, "commute_probe")
                + " "
                + get_template(self.gate, "move_in_probe"),
                gate=self.gate,
            )
        return GateResult(
            passed=True,
            reply=get_template("showing", "slot_offer"),
            gate="showing",
        )


class IncomeVerifier:
    """Gate 3: hard income floor at INCOME_MULTIPLIER x rent."""

    gate = "gate_3_income"

    def evaluate(self, ctx: LeadContext) -> GateResult:
        if ctx.monthly_income is None:
            return GateResult(
                passed=True,
                reply=get_template(self.gate, "income_floor"),
                gate=self.gate,
            )
        if ctx.monthly_income < INCOME_MULTIPLIER * ctx.rent:
            return GateResult(passed=False, reply=COMPLIANCE_EXIT, gate=self.gate)
        return GateResult(
            passed=True,
            reply=get_template(self.gate, "verification_ask"),
            gate=self.gate,
        )
