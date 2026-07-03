"""Extract LeadCandidate records from operator-saved Facebook captures."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .models import LeadCandidate, now_iso

_CAPTURE_SUFFIXES = {".html", ".htm", ".txt", ".md", ".csv"}
_PRICE_RE = re.compile(r"(?:\$\s*)?\d[\d,]*(?:\.\d{1,2})?(?:\s*(?:/\s*mo|per month|monthly|mo))?", re.I)
_RANGE_RE = re.compile(r"\d[\d,]*\s*-\s*\$?\s*\d[\d,]*")
_LOCATION_RE = re.compile(r"\bLocation:\s*(.+?)(?=\s+Seller:|\s+Price:|$)", re.I)
_SELLER_RE = re.compile(r"\bSeller:\s*(.+?)(?=\s+Location:|\s+Price:|$)", re.I)


def should_skip_capture(path: Path) -> bool:
    return path.name == ".DS_Store" or path.name.endswith(".meta.json") or path.suffix not in _CAPTURE_SUFFIXES


def load_sidecar(path: Path) -> dict[str, Any]:
    sidecar = _sidecar_path(path)
    if not sidecar.exists():
        return {}
    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def extract_captures(root: Path) -> list[LeadCandidate]:
    leads: list[LeadCandidate] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or should_skip_capture(path):
            continue
        leads.extend(extract_capture(path, load_sidecar(path)))
    return leads


def extract_capture(path: Path, meta: dict[str, Any] | None = None) -> list[LeadCandidate]:
    """Dispatch a saved capture file into one or more LeadCandidate records."""
    meta = meta or {}
    try:
        if path.suffix.lower() in {".html", ".htm"}:
            leads = [_extract_html(path, meta)]
        elif path.suffix.lower() in {".txt", ".md"}:
            leads = [_extract_text(path, meta)]
        elif path.suffix.lower() == ".csv":
            leads = _extract_csv(path, meta)
        else:
            return []
    except Exception as exc:
        leads = [_failed_lead(path, meta, exc)]
    return [_apply_meta_overrides(lead, meta) for lead in leads]


def parse_price(text: str) -> tuple[str, float | None, str]:
    raw = text.strip()
    currency = "USD" if "$" in raw else ""
    if not raw or raw.lower() == "free" or _RANGE_RE.search(raw):
        return raw, None, currency
    match = _PRICE_RE.search(raw)
    if not match:
        return raw, None, currency
    token = match.group(0).replace("$", "")
    token = re.sub(r"(?i)\s*(/\s*mo|per month|monthly|mo)\s*$", "", token).strip()
    try:
        return raw, float(token.replace(",", "")), currency
    except ValueError:
        return raw, None, currency


def _extract_html(path: Path, meta: dict[str, Any]) -> LeadCandidate:
    html = path.read_text(encoding="utf-8", errors="replace")
    if "__FB_CAPTURE_PARSE_ERROR__" in html:
        raise ValueError("parse marker found in malformed fixture")

    soup = BeautifulSoup(html, "html.parser")
    text = _collapse_ws(soup.get_text("\n"))
    title = _meta_content(soup, "og:title") or _first_text(soup, ["h1", "title"])
    description = _meta_content(soup, "og:description")
    source_url = _meta_content(soup, "og:url") or ""
    body_text = description or text
    price_text, price_value, currency = _find_price(title, body_text)
    json_ld = _extract_json_ld(soup)

    if json_ld:
        price_text = price_text or _json_string(json_ld.get("price"))
        if price_value is None and price_text:
            price_text, price_value, currency_from_price = parse_price(price_text)
            currency = currency or currency_from_price
        currency = currency or _json_string(json_ld.get("priceCurrency"))

    location = _json_nested_name(json_ld, "areaServed") if json_ld else ""
    location = location or _regex_group(_LOCATION_RE, text)
    seller_name = _json_nested_name(json_ld, "seller") if json_ld else ""
    seller_name = seller_name or _regex_group(_SELLER_RE, text) or _seller_from_aria(soup)
    seller_name = seller_name or _first_text(soup, ["strong"])
    images = _extract_images(soup)
    source_type = _infer_source_type(source_url, text, meta)
    extraction = "ok" if _meta_content(soup, "og:title") and _meta_content(soup, "og:description") else "partial"

    return LeadCandidate(
        source_url=source_url,
        source_type=source_type,
        title=title,
        body_text=body_text,
        price_text=price_text,
        price_value=price_value,
        currency=currency,
        location=location,
        seller_name=seller_name,
        images=images,
        capture_time=_capture_time(path, meta),
        capture_path=_repo_relative(path),
        extraction=extraction,
    )


def _extract_text(path: Path, meta: dict[str, Any]) -> LeadCandidate:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = lines[0] if lines else path.stem
    body_text = "\n".join(lines[1:]) if len(lines) > 1 else ""
    combined = "\n".join(lines)
    price_text, price_value, currency = _find_price(combined)
    return LeadCandidate(
        source_url=str(meta.get("source_url") or ""),
        source_type=str(meta.get("source_type") or "manual_note"),
        title=title,
        body_text=body_text,
        price_text=price_text,
        price_value=price_value,
        currency=currency,
        location=_regex_group(_LOCATION_RE, combined),
        capture_time=_capture_time(path, meta),
        capture_path=_repo_relative(path),
        extraction="ok" if lines else "failed",
    )


def _extract_csv(path: Path, meta: dict[str, Any]) -> list[LeadCandidate]:
    leads: list[LeadCandidate] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            price_text, price_value, currency = parse_price(raw.get("price_text", ""))
            leads.append(
                LeadCandidate(
                    source_url=raw.get("source_url", ""),
                    source_type=raw.get("source_type", "csv_import") or "csv_import",
                    title=raw.get("title", ""),
                    body_text=raw.get("body_text", ""),
                    price_text=price_text,
                    price_value=price_value,
                    currency=currency,
                    location=raw.get("location", ""),
                    seller_name=raw.get("seller_name", ""),
                    capture_time=_capture_time(path, meta),
                    capture_path=_repo_relative(path),
                    extraction="ok",
                )
            )
    return leads


def _failed_lead(path: Path, meta: dict[str, Any], exc: Exception) -> LeadCandidate:
    return LeadCandidate(
        source_url=str(meta.get("source_url") or ""),
        source_type=str(meta.get("source_type") or "other"),
        title=path.stem,
        body_text=f"parse failed: {exc}",
        capture_time=_capture_time(path, meta),
        capture_path=_repo_relative(path),
        extraction="failed",
    )


def _apply_meta_overrides(lead: LeadCandidate, meta: dict[str, Any]) -> LeadCandidate:
    data = lead.as_dict()
    for key in ("source_url", "source_type"):
        if meta.get(key):
            data[key] = meta[key]
    if meta.get("captured_at"):
        data["capture_time"] = meta["captured_at"]
    overrides = meta.get("overrides")
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key in data:
                data[key] = value
        if "price_text" in overrides:
            data["price_text"], data["price_value"], data["currency"] = parse_price(str(overrides["price_text"]))
    data["id"] = ""
    return LeadCandidate(**data)


def _find_price(*texts: str) -> tuple[str, float | None, str]:
    for text in texts:
        range_match = _RANGE_RE.search(text or "")
        if range_match:
            start = range_match.start()
            raw = range_match.group(0)
            if start > 0 and text[start - 1] == "$":
                raw = "$" + raw
            return parse_price(raw)
        match = _PRICE_RE.search(text or "")
        if match:
            return parse_price(match.group(0))
    return "", None, ""


def _meta_content(soup: BeautifulSoup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    value = tag.get("content") if tag else ""
    return str(value).strip() if value else ""


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for selector in selectors:
        tag = soup.find(selector)
        if tag and tag.get_text(strip=True):
            return tag.get_text(strip=True)
    return ""


def _extract_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _extract_images(soup: BeautifulSoup) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for img in soup.find_all("img"):
        src = str(img.get("src") or "").strip()
        if not src:
            continue
        images.append({"src_name": Path(src).name, "alt": str(img.get("alt") or "")})
    return images


def _seller_from_aria(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(lambda candidate: candidate.has_attr("aria-label")):
        label = str(tag.get("aria-label") or "")
        if label.lower().startswith("seller "):
            return label.split(" ", 1)[1].strip()
    return ""


def _json_string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _json_nested_name(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if isinstance(value, dict):
        return _json_string(value.get("name"))
    return _json_string(value)


def _regex_group(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def _infer_source_type(source_url: str, text: str, meta: dict[str, Any]) -> str:
    if meta.get("source_type"):
        return str(meta["source_type"])
    low = (source_url + " " + text).lower()
    if "marketplace" in low:
        return "marketplace_listing"
    if "/groups/" in low or "group" in low:
        return "group_post"
    if "corporate lease" in low or "landlord" in low:
        return "group_post"
    if "facebook.com" in low:
        return "page_post"
    return "other"


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _capture_time(path: Path, meta: dict[str, Any]) -> str:
    if meta.get("captured_at"):
        return str(meta["captured_at"])
    try:
        path.stat()
        return now_iso()
    except OSError:
        return now_iso()


def _repo_relative(path: Path) -> str:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    try:
        return str(resolved.relative_to(cwd))
    except ValueError:
        return str(path)


def _sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")
