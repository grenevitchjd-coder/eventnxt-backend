# eventnxt-backend: test/test_import_matching.py  (verification harness)
#
# Slice 3 of the external-events work: imported sales matched ONCE at
# import time (services/sale_matching) and stamped onto the Sale row,
# with seating math reading the stamp through the one shared
# imported_heads_for_pool. Pinned against a synthetic export shaped
# exactly like the real box-office artifact (per-ticket rows, quantity
# 1, barcode column, "Coupon CODE: -$X.XX" discount cells, day either
# in the ticket name or file-level, NO price column):
#
#   1. mapping endpoints: normalized-label upsert + list
#   2. coupon parsing -> promo attribution + face-value amount fill
#      (percentage reward computes from the filled amount)
#   3. day routing: a day token in the label BEATS the file-level day;
#      the file-level day covers tokenless labels; routing runs through
#      the slice-2 pool-name families (bare base = night 1)
#   4. non-admission mappings: imported + attributed, never in room math
#   5. barcode dedup on re-upload
#   6. stamped heads land on the right pool per night in the summary;
#      pre-0049 UNSTAMPED rows still count via NORMALIZED name (the
#      trailing-space case the old exact ilike dropped)
#   7. comp placement enforces against the same imported heads
#      (display == enforcement, structurally)
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_import_matching.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

import app.services.email as email_mod
from app.database import SessionLocal
from app.main import app
from app.models.sale import Sale, SaleSource
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

email_mod.send_email = lambda **kw: None

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
D1, D2, D3 = "2026-10-08", "2026-10-09", "2026-10-10"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "Ext Fest", "start_date": D1, "end_date": D3}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)
H = {"Authorization": "Bearer tok"}


def imp(rows):
    return c.post(f"/events/{EV}/sales/import", json={"rows": rows}, headers=H).json()


def summary():
    return {r["category_name"]: r for r in c.get(f"/events/{EV}/seating-categories/summary", headers=H).json()}


def main():
    # ---- bootstrap: external per-day event, slice-2 fanned room ----
    c.patch(f"/events/{EV}/settings",
            json={"ticketing_mode": "external", "sales_source": "csv", "ticket_span": "per_day"}, headers=H)
    row2 = c.post(f"/events/{EV}/seating-categories",
                  json={"name": "Row 2 Preferred", "capacity": 20, "sales_grain": "ga"}, headers=H).json()
    c.post(f"/events/{EV}/seating-categories/{row2['id']}/fan-out", headers=H)
    lounge = c.post(f"/events/{EV}/seating-categories",
                    json={"name": "Champagne Lounge", "capacity": 10, "sales_grain": "ga"}, headers=H).json()
    pools = {p["name"]: p for p in c.get(f"/events/{EV}/seating-categories", headers=H).json()}
    row2_d2 = pools["Row 2 Preferred (10/09)"]

    ref = c.post(f"/events/{EV}/referrers", json={"name": "Vanessa", "email": "vanessa@x.com"}, headers=H).json()
    vanessa = c.post(f"/events/{EV}/promo-codes",
                     json={"guest_id": ref["id"], "code": "VANESSA2510", "reward_type": "percentage",
                           "reward_value": 10, "discount_type": "flat_amount", "discount_value": 6.5},
                     headers=H).json()

    # ---- 1. mapping endpoints ----
    r = c.put(f"/events/{EV}/sales/type-mappings", json={"mappings": [
        {"raw_label": "SEC C: Preferred - Row 2 ", "seating_category_id": row2["id"], "face_value_cents": 10500},
        {"raw_label": "Drink coupon", "seating_category_id": None, "is_admission": False},
        {"raw_label": "Champagne Lounge: Table 2 Seat: 1", "seating_category_id": lounge["id"]},
    ]}, headers=H)
    check("mappings PUT returns 3, labels normalized",
          r.status_code == 200 and len(r.json()) == 3
          and any(m["raw_label"] == "sec c: preferred - row 2" for m in r.json()), r.text)

    # ---- 2 + 3. the Thursday file (file-level day D1, no price column) ----
    res = imp([
        {"buyer_name": "Naomi Steele", "buyer_email": "naomi@x.com",
         "ticket_type": "SEC C: Preferred - Row 2", "quantity": 1,
         "discount_text": "Coupon VANESSA2510: -$6.50", "event_day": D1,
         "external_transaction_id": "1554659410656671"},
        # day token in the label BEATS the file-level day:
        {"buyer_name": "Jack White", "buyer_email": "jack@x.com",
         "ticket_type": "FRIDAY \u2013 Row 2 Preferred", "quantity": 1, "event_day": D1,
         "external_transaction_id": "1554675414540341"},
        {"buyer_name": "Brenna Tanzosh", "buyer_email": "brenna@x.com",
         "ticket_type": "Drink coupon", "quantity": 1,
         "discount_text": "Coupon VANESSA2510: -$3.50", "event_day": D1,
         "external_transaction_id": "1555045263213571"},
        {"buyer_name": "Celeste Trapp", "buyer_email": "celeste@x.com",
         "ticket_type": "Champagne Lounge: Table 2 Seat: 1", "quantity": 1, "event_day": D1,
         "external_transaction_id": "1554728559844623"},
    ])
    check("import lands 4 rows", res["imported"] == 4, res)

    db = SessionLocal()
    try:
        by_tx = {s.external_transaction_id: s for s in db.query(Sale).filter(Sale.event_id == EV).all()}
        naomi = by_tx["1554659410656671"]
        check("coupon cell attributes the promo code",
              str(naomi.promo_code_id) == vanessa["id"], naomi.promo_code_id)
        check("face value minus coupon fills the missing amount (98.50)",
              naomi.amount == Decimal("98.50"), naomi.amount)
        check("percentage reward computes from the filled amount (9.85)",
              naomi.computed_reward == Decimal("9.85"), naomi.computed_reward)
        check("mapped label stamps the night-1 base pool + day",
              str(naomi.seating_category_id) == row2["id"] and naomi.event_day == D1,
              (naomi.seating_category_id, naomi.event_day))
        jack = by_tx["1554675414540341"]
        check("FRIDAY token beats the file day: stamped to the (10/09) clone",
              str(jack.seating_category_id) == row2_d2["id"] and jack.event_day == D2,
              (jack.seating_category_id, jack.event_day))
        brenna = by_tx["1555045263213571"]
        check("drink coupon: not admission, no pool, promo still credited",
              brenna.is_admission is False and brenna.seating_category_id is None
              and str(brenna.promo_code_id) == vanessa["id"], (brenna.is_admission, brenna.promo_code_id))
        celeste = by_tx["1554728559844623"]
        check("lounge mapping stamps the lone pool",
              str(celeste.seating_category_id) == lounge["id"], celeste.seating_category_id)

        # ---- 6b. a pre-0049 legacy row: unstamped, trailing space ----
        db.add(Sale(event_id=EV, ticket_type="Row 2 Preferred ", quantity=2,
                    source=SaleSource.CSV_UPLOAD, is_admission=True))
        db.commit()
    finally:
        db.close()

    # ---- 5. barcode dedup on re-upload of the same (grown) file ----
    res2 = imp([
        {"buyer_name": "Naomi Steele", "ticket_type": "SEC C: Preferred - Row 2", "quantity": 1,
         "event_day": D1, "external_transaction_id": "1554659410656671"},
        {"buyer_name": "New Buyer", "ticket_type": "SEC C: Preferred - Row 2", "quantity": 1,
         "event_day": D1, "external_transaction_id": "9999999999"},
    ])
    check("re-upload: old barcode skipped, new row lands",
          res2["skipped_duplicates"] == 1 and res2["imported"] == 1, res2)

    # ---- 6. summary: heads on the right pools/nights; nothing phantom ----
    s = summary()
    check("night-1 base pool: 2 mapped + 2 legacy-name heads = 4",
          s["Row 2 Preferred"]["box_office"] == 4, s["Row 2 Preferred"])
    check("(10/09) clone: exactly the FRIDAY-token head",
          s["Row 2 Preferred (10/09)"]["box_office"] == 1, s["Row 2 Preferred (10/09)"])
    check("(10/10) clone untouched", s["Row 2 Preferred (10/10)"]["box_office"] == 0)
    check("drink coupon in no pool's math",
          sum(r["box_office"] for r in s.values()) == 6, {k: v["box_office"] for k, v in s.items()})
    check("lounge shows its head", s["Champagne Lounge"]["box_office"] == 1)

    # ---- 7. display == enforcement, at the sites that enforce ----
    # Pool-level PRIORITY placement is deliberately optimistic (the
    # long-standing estimated-vs-confirmed exposure, reconciled on
    # Seating summary) — pinned as such. What DOES honor imported heads
    # is _pool_room_components: the sectionless section-summary display
    # and the allotment-recipient room check both read it, and it now
    # counts stamped + legacy heads via the same shared function.
    imp([{"ticket_type": "Champagne Lounge: Table 2 Seat: 1", "quantity": 1, "event_day": D1,
          "external_transaction_id": f"tbl-{i}"} for i in range(7)])  # lounge now 8/10 imported
    sect = c.get(f"/events/{EV}/seating-categories/section-summary", headers=H).json()
    lounge_rows = [p for p in sect if p["category_id"] == lounge["id"]][0]["sections"]
    check("sectionless display: left = capacity - imported heads (2)",
          lounge_rows[0]["bought"] == 8 and lounge_rows[0]["left"] == 2, lounge_rows)
    gt = c.post(f"/events/{EV}/guest-types", json={"name": "Lounge Comps", "guest_mode": "invite"}, headers=H).json()
    c.post(f"/events/{EV}/guest-types/{gt['id']}/seating-priorities",
           json={"seating_category_id": lounge["id"]}, headers=H)
    g_big = c.post(f"/events/{EV}/guests",
                   json={"name": "Party Three", "email": "p3@x.com", "guest_type_id": gt["id"],
                         "allocation_status": "confirmed", "party_size": 3}, headers=H).json()
    check("pool-level priority placement stays deliberately optimistic (pinned)",
          g_big["seating_category_id"] == lounge["id"], g_big)
    s2 = summary()
    check("...and the summary reconciles the exposure (box_office 8, committed 3)",
          s2["Champagne Lounge"]["box_office"] == 8 and s2["Champagne Lounge"]["committed"] == 3,
          s2["Champagne Lounge"])

    # ---- 8. all-days packages (0050): one row, every night ----
    c.put(f"/events/{EV}/sales/type-mappings", json={"mappings": [
        {"raw_label": "Weekend Pass – Row 2 Preferred", "seating_category_id": row2["id"], "all_days": True},
    ]}, headers=H)
    before = {k: v["box_office"] for k, v in summary().items()}
    res8 = imp([
        {"buyer_name": "Pass Buyer", "ticket_type": "Weekend Pass – Row 2 Preferred", "quantity": 1,
         "event_day": D1, "external_transaction_id": f"wkp-{uuid.uuid4()}"},  # file day deliberately set: must be ignored
    ])
    check("package row imports", res8["imported"] == 1, res8)
    after = summary()
    check("package counts into the base pool (night 1)",
          after["Row 2 Preferred"]["box_office"] == before["Row 2 Preferred"] + 1, after["Row 2 Preferred"])
    check("...and the (10/09) clone",
          after["Row 2 Preferred (10/09)"]["box_office"] == before["Row 2 Preferred (10/09)"] + 1)
    check("...and the (10/10) clone",
          after["Row 2 Preferred (10/10)"]["box_office"] == before["Row 2 Preferred (10/10)"] + 1)
    check("...but never into other families",
          after["Champagne Lounge"]["box_office"] == before["Champagne Lounge"])
    db = SessionLocal()
    try:
        pkg = db.query(Sale).filter(Sale.event_id == EV, Sale.all_days.is_(True)).first()
        check("package stamp: base pool, no single day, all_days",
              str(pkg.seating_category_id) == row2["id"] and pkg.event_day is None and pkg.all_days is True,
              (pkg.seating_category_id, pkg.event_day, pkg.all_days))
    finally:
        db.close()

    print()
    if failures:
        print(f"import matching: {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("import matching: all clear")


if __name__ == "__main__":
    main()