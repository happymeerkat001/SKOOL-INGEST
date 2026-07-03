"""Deterministic posting draft templates."""
from __future__ import annotations

TEMPLATES: dict[str, str] = {
    "room_listing": (
        "Available in {location}: {room_desc}\n\n"
        "Price: {price}\n"
        "Move-in: {move_in}\n\n"
        "Message me if you'd like details or want to schedule a time to see it."
    ),
    "coliving_room": (
        "Private room available in a shared home in {location}.\n\n"
        "{room_desc}\n"
        "Monthly price: {price}\n"
        "Available: {move_in}\n\n"
        "Send a message for more information."
    ),
}


def render(template_id: str, slots: dict[str, str]) -> str:
    if template_id not in TEMPLATES:
        raise KeyError(f"unknown template: {template_id}")
    required = {part.split("}", 1)[0] for part in TEMPLATES[template_id].split("{")[1:]}
    for name in sorted(required):
        if not str(slots.get(name, "")).strip():
            raise KeyError(f"missing template slot: {name}")
    try:
        return TEMPLATES[template_id].format(**slots)
    except KeyError as exc:
        missing = exc.args[0]
        raise KeyError(f"missing template slot: {missing}") from exc
