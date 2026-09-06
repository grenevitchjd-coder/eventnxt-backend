# eventnxt-backend: app/services/sale_matching.py
"""
THE match, in one place (0049): how an imported sales row's free-text
ticket type becomes (pool, event day, admission?, amount). Runs once at
import time and stamps the result onto the Sale row — seating math
reads the stamp instead of re-guessing names per query, so display and
enforcement can never drift, and the stamp survives pool renames.

Resolution order for a label, most-specific first:
  1. saved mapping on the FULL normalized label
  2. saved mapping on the label with its day token stripped
     ("THURSDAY - Row 3 Preferred" -> "Row 3 Preferred")
  3. a pool whose normalized NAME equals the full label
  4. a pool whose normalized name equals the day-stripped label
The show day comes from the label's own day token when it has one
(weekday name or MM/DD, resolved against the event's real days),
else the file-level day the organizer confirmed in staging. The
matched base pool is then routed to that day's family sibling through
the SAME pool_for_day comps use.

The day token is the SHOW night. A "sale date"/purchase-date column is
never used for day routing — someone buys a Thursday ticket in
September.
"""
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.sale_type_mapping import SaleTypeMapping
from app.models.seating_category import SeatingCategory
from app.services.seating import normalized_name, pool_for_day

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_SEP = r"[\s\-\u2013\u2014:\u00b7,]*"
_LEADING_WD = re.compile(rf"^\s*({_WD})\b{_SEP}", re.IGNORECASE)
_TRAILING_WD = re.compile(rf"{_SEP}\b({_WD})\s*$", re.IGNORECASE)
_DATE_TOKEN = re.compile(r"\(?\b(\d{1,2})\s*/\s*(\d{1,2})\b\)?")
_COUPON = re.compile(r"coupon\s+([A-Za-z0-9_\-]+)\s*:?\s*-?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)?", re.IGNORECASE)


def _weekday_of(iso_day: str) -> int:
    from datetime import date

    return date.fromisoformat(iso_day).weekday()


def extract_day_token(label: str, days: list[str]) -> tuple[str, str | None]:
    """
    (base_label, event_day) — strips one leading/trailing weekday name
    or one MM/DD token and resolves it against the event's real day
    list. A token matching no event day, or a weekday the event has
    TWICE (two Thursdays), resolves to None — never guess — but is
    still stripped from the base label. No token: label back unchanged.
    """
    text = str(label or "")
    if not text.strip():
        return "", None

    m = _DATE_TOKEN.search(text)
    if m:
        mm, dd = int(m.group(1)), int(m.group(2))
        day = next((d for d in days if int(d[5:7]) == mm and int(d[8:10]) == dd), None)
        base = (text[: m.start()] + " " + text[m.end():]).strip(" -\u2013\u2014:\u00b7,\t")
        return base, day

    for rx in (_LEADING_WD, _TRAILING_WD):
        m = rx.search(text)
        if m:
            wd = WEEKDAYS[m.group(1).lower()]
            matches = [d for d in days if _weekday_of(d) == wd]
            base = (text[: m.start()] + text[m.end():]).strip(" -\u2013\u2014:\u00b7,\t")
            return base, matches[0] if len(matches) == 1 else None
    return text, None


def parse_coupon(discount_text) -> tuple[str | None, Decimal | None]:
    """
    Box-office discount cells like "Coupon VANESSA2510: -$19.50" ->
    ("VANESSA2510", Decimal("19.50")). Code without a readable amount
    still returns the code. Anything else: (None, None).
    """
    m = _COUPON.search(str(discount_text or ""))
    if not m:
        return None, None
    code = m.group(1)
    amt = None
    if m.group(2):
        try:
            amt = Decimal(m.group(2).replace(",", ""))
        except InvalidOperation:
            amt = None
    return code, amt


def get_mappings(db: Session, event_id: str) -> dict[str, SaleTypeMapping]:
    return {
        m.raw_label: m
        for m in db.query(SaleTypeMapping).filter(SaleTypeMapping.event_id == event_id).all()
    }


def upsert_mapping(db: Session, event_id: str, raw_label, seating_category_id, face_value_cents, is_admission) -> SaleTypeMapping | None:
    """Upsert by NORMALIZED label; caller commits. Blank labels are ignored."""
    key = normalized_name(raw_label)
    if not key:
        return None
    row = (
        db.query(SaleTypeMapping)
        .filter(SaleTypeMapping.event_id == event_id, SaleTypeMapping.raw_label == key)
        .first()
    )
    if not row:
        row = SaleTypeMapping(event_id=event_id, raw_label=key)
        db.add(row)
    row.seating_category_id = seating_category_id
    row.face_value_cents = face_value_cents
    row.is_admission = bool(is_admission)
    return row


def resolve_label(db: Session, event_id: str, label, file_day, days, mappings=None, pools_by_norm=None):
    """
    -> dict(seating_category_id, event_day, is_admission, face_value_cents)
    for one imported ticket-type label. mappings / pools_by_norm can be
    passed in by batch callers to avoid per-row queries.
    """
    if mappings is None:
        mappings = get_mappings(db, event_id)
    if pools_by_norm is None:
        pools_by_norm = {
            normalized_name(p.name): p.id
            for p in db.query(SeatingCategory).filter(SeatingCategory.event_id == event_id).all()
        }

    base, token_day = extract_day_token(label, days)
    event_day = token_day or (file_day if file_day in days else None) or (file_day or None)
    full_norm, base_norm = normalized_name(label), normalized_name(base)

    mapping = mappings.get(full_norm) or mappings.get(base_norm)
    if mapping is not None:
        if not mapping.is_admission:
            return {"seating_category_id": None, "event_day": event_day, "is_admission": False,
                    "face_value_cents": mapping.face_value_cents}
        pool_id = mapping.seating_category_id
    else:
        pool_id = pools_by_norm.get(full_norm) or pools_by_norm.get(base_norm)

    if pool_id and event_day:
        pool_id = pool_for_day(db, pool_id, event_day)
    return {"seating_category_id": pool_id, "event_day": event_day, "is_admission": True,
            "face_value_cents": mapping.face_value_cents if mapping else None}


def enrich_import_row(db: Session, event_id: str, row: dict, days, mappings, pools_by_norm) -> dict:
    """
    The importer's per-row step: coupon parsing out of the discount
    cell (an explicit promo_code column always wins), the match, and a
    face-value amount fill (face minus parsed discount) when the export
    carries no price column. Returns the enriched dict reconcile_sale_row
    stores; every key it adds is also stamped onto the Sale.
    """
    out = dict(row)
    coupon_code, coupon_discount = parse_coupon(row.get("discount_text"))
    if coupon_code and not (row.get("promo_code") or "").strip():
        out["promo_code"] = coupon_code

    res = resolve_label(db, event_id, row.get("ticket_type"), row.get("event_day"), days, mappings, pools_by_norm)
    out["seating_category_id"] = res["seating_category_id"]
    out["event_day"] = res["event_day"]
    out["is_admission"] = res["is_admission"]

    if out.get("amount") is None and res["face_value_cents"] is not None:
        face = Decimal(res["face_value_cents"]) / 100
        qty = out.get("quantity") or 1
        out["amount"] = max(face * qty - (coupon_discount or Decimal(0)), Decimal(0))
    return out