"""Deterministic scoring rules for Facebook lead candidates."""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any, NamedTuple

from .models import LeadCandidate, load, save

HOT_THRESHOLD = 8
WARM_THRESHOLD = 4


class Rule(NamedTuple):
    pattern: re.Pattern[str]
    weight: int
    tag: str
    label: str


RULES: tuple[Rule, ...] = (
    Rule(re.compile(r"\bprivate room\b", re.I), 4, "room_supply", "private room"),
    Rule(re.compile(r"\broom for rent\b", re.I), 4, "room_supply", "room for rent"),
    Rule(re.compile(r"\bfurnished\b", re.I), 2, "room_supply", "furnished"),
    Rule(re.compile(r"\butilities included\b", re.I), 2, "room_supply", "utilities included"),
    Rule(re.compile(r"\bmonth[- ]to[- ]month\b", re.I), 2, "room_supply", "month to month"),
    Rule(re.compile(r"\blandlord\b", re.I), 3, "owner", "landlord"),
    Rule(re.compile(r"\bi own\b", re.I), 3, "owner", "I own"),
    Rule(re.compile(r"\bmy property\b", re.I), 3, "owner", "my property"),
    Rule(re.compile(r"\bmy tenant\b", re.I), 2, "owner", "my tenant"),
    Rule(re.compile(r"\bvacant\b", re.I), 3, "distressed", "vacant"),
    Rule(re.compile(r"\bmust rent asap\b", re.I), 3, "distressed", "must rent asap"),
    Rule(re.compile(r"\btired of managing\b", re.I), 3, "distressed", "tired of managing"),
    Rule(re.compile(r"\bmotivated\b", re.I), 2, "distressed", "motivated"),
    Rule(re.compile(r"\brental arbitrage\b", re.I), 3, "partnership", "rental arbitrage"),
    Rule(re.compile(r"\bcorporate lease\b", re.I), 3, "partnership", "corporate lease"),
    Rule(re.compile(r"\bsublease allowed\b", re.I), 3, "partnership", "sublease allowed"),
    Rule(re.compile(r"\bproperty manager wanted\b", re.I), 3, "partnership", "property manager wanted"),
    Rule(re.compile(r"\bco-?living\b", re.I), 4, "coliving", "coliving"),
    Rule(re.compile(r"\brent by the room\b", re.I), 5, "coliving", "rent by the room"),
    Rule(re.compile(r"\bshared housing\b", re.I), 3, "coliving", "shared housing"),
    Rule(re.compile(r"\blooking for a room\b", re.I), -5, "demand", "looking for a room"),
    Rule(re.compile(r"\bneed a place\b", re.I), -4, "demand", "need a place"),
    Rule(re.compile(r"\bdeposit before viewing\b", re.I), -5, "caution", "deposit before viewing"),
    Rule(re.compile(r"\bcashapp only\b", re.I), -4, "caution", "cashapp only"),
)


def score_lead(lead: LeadCandidate) -> LeadCandidate:
    """Return a scored copy of a lead. Pure: no I/O and does not mutate input."""
    text = f"{lead.title}\n{lead.body_text}"
    score = 0
    reasons: list[str] = []
    tags: list[str] = []
    seen_tags: set[str] = set()

    for rule in RULES:
        if rule.pattern.search(text):
            score += rule.weight
            if rule.tag not in seen_tags:
                seen_tags.add(rule.tag)
                tags.append(rule.tag)
            sign = "+" if rule.weight >= 0 else ""
            reasons.append(f"matched '{rule.label}' ({sign}{rule.weight}, {rule.tag})")

    return dataclasses.replace(
        lead,
        score=score,
        score_band=band_for_score(score),
        score_reasons=reasons,
        tags=tags,
    )


def band_for_score(score: int) -> str:
    if score >= HOT_THRESHOLD:
        return "hot"
    if score >= WARM_THRESHOLD:
        return "warm"
    return "low"


def score_store(leads_path: Path, *, only_unscored: bool = False) -> dict[str, Any]:
    rows = load(leads_path)
    scored = 0
    skipped = 0
    updated: list[LeadCandidate] = []
    for lead in rows.values():
        if only_unscored and lead.score_band != "unscored":
            updated.append(lead)
            skipped += 1
            continue
        updated.append(score_lead(lead))
        scored += 1
    save(leads_path, updated)
    return {
        "scored": scored,
        "skipped": skipped,
        "total": len(updated),
        "leads_path": str(leads_path),
    }
