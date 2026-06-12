"""Response template registry for the lead qualification funnel.

The production corpus references 333 standardized response templates ("SLPs")
generated from ~9,000 historical tenant messages. The corpus does not contain
the templates themselves, so this registry holds the distinct gate templates
the funnel requires, keyed by gate and scenario. TEMPLATE_MATRIX_TARGET
documents the production target size.
"""

from __future__ import annotations

TEMPLATE_MATRIX_TARGET = 333

COMPLIANCE_EXIT = (
    "Thanks for reaching out! This room is set up for single occupancy with "
    "no pets, so it won't be the right fit. Best of luck with your search!"
)

RESPONSE_TEMPLATES: dict[str, dict[str, str]] = {
    "gate_1_occupancy": {
        "availability": (
            "Yes, the room is still available! It's a private room for one "
            "adult, no pets. Would that work for you?"
        ),
        "single_occupancy": (
            "Just to confirm — the room is for one adult only. Is it just "
            "you moving in?"
        ),
        "no_pets": "Quick check: the house is pet-free. Do you have any pets?",
    },
    "gate_2_location": {
        "commute_probe": (
            "This house is located one or two minutes from {address}. Is that "
            "close to your work?"
        ),
        "move_in_probe": "When do you want to come see it?",
    },
    "gate_3_income": {
        "income_floor": (
            "To qualify, monthly income needs to be at least 2.5x the rent. "
            "Does that work on your end?"
        ),
        "verification_ask": (
            "Great — I'll send the application to your phone. It includes "
            "income verification and a background check."
        ),
    },
    "showing": {
        "slot_offer": (
            "Showings run daily between 5 and 7 pm. Which day works for you? "
            "I'll lock in a time."
        ),
    },
}


def get_template(gate: str, scenario: str) -> str:
    return RESPONSE_TEMPLATES[gate][scenario]
