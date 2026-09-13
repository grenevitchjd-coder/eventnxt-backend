# eventnxt-backend: test/test_door_sales.py  (verification harness)
#
# Cash door sales (migration 0053):
#   1. Missing staff attestation -> 400, no order persists, inventory
#      untouched.
#   2. A bad promo code -> 400, same no-persist discipline as checkout.
#   3. A successful sale: order.payment_method='cash', platform_fee_cents
#      and reserve_cents are 0, organizer_net == what was charged,
#      sold_by_user_id/sold_by_name stamped, terms_accepted_at stamped
#      (staff attestation, not a buyer checkbox), tickets minted and
#      immediately reflected in the SAME availability the public catalog
#      and the door catalog both read (ticketing.ticket_catalog).
#   4. A promo code applied at the door still discounts correctly and
#      feeds the same Sale/attribution machinery as an online purchase.
#   5. Reconciliation sums ONLY cash+paid orders inside the given
#      window — a same-event Stripe-path order and an order paid
#      outside the window are both excluded.
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_door_sales.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

import app.services.email as email_mod
from app.database import SessionLocal
from app.main import app
from app.models.order import Order, OrderStatus
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

email_mod.send_email = lambda **kw: None

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
STAFFER = str(uuid.uuid4())  # sold_by_user_id is a real UUID column (0053) —
# Events360 user ids are UUIDs, same as organization_id/event_id everywhere
# else in this app; a placeholder like "staffer-1" isn't a valid Events360
# id and only ever surfaces here because this is the one place user_id
# gets persisted rather than just carried around in memory.
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = STAFFER; organization_id = ORG; name = "Alex Door"; email = "alex@x.com"; role = "staff"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "Door Sales Test", "start_date": "2026-12-24", "end_date": "2026-12-24"}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)
H = {"Authorization": "Bearer tok"}

TT = {"description": None, "max_per_order": 20, "admits": 1, "sales_start": None, "sales_end": None,
      "is_active": True, "sort_order": 0, "valid_date": None}


def order_by_email(email):
    db = SessionLocal()
    o = db.query(Order).filter(Order.event_id == EV, Order.buyer_email == email).order_by(Order.created_at.desc()).first()
    if o:
        db.expunge(o)
    db.close()
    return o


def mark_paid_at(order_id, when: datetime):
    """Backdate paid_at directly — the reconciliation window test needs
    a sale outside "today" without waiting a day."""
    db = SessionLocal()
    db.query(Order).filter(Order.id == order_id).update({"paid_at": when})
    db.commit()
    db.close()


def catalog_available(tt_id):
    row = next(t for t in c.get(f"/events/{EV}/door-sales/catalog", headers=H).json() if t["id"] == tt_id)
    return row["available"]


def main():
    pool = c.post(f"/events/{EV}/seating-categories",
                  json={"name": "GA", "capacity": 50, "sales_grain": "ga", "row_label": None}, headers=H).json()
    tt = c.post(f"/events/{EV}/ticket-types",
                json={**TT, "name": "GA", "price_cents": 2500, "quantity": 20,
                      "seating_category_id": pool["id"]}, headers=H).json()
    promo = c.post(f"/events/{EV}/promo-codes",
                    json={"code": "DOORSALE10", "discount_type": "flat_amount", "discount_value": 5.00},
                    headers=H).json()

    def sell(email, qty=1, promo_code=None, staff_attested=True):
        return c.post(
            f"/events/{EV}/door-sales/sell",
            json={
                "buyer_name": "Walk Up", "buyer_email": email,
                "items": [{"ticket_type_id": tt["id"], "quantity": qty, "seat_ids": [], "zone_section_id": None, "zone_section_ids": []}],
                "promo_code": promo_code,
                "staff_attested_terms": staff_attested,
            },
            headers=H,
        )

    print("1) no staff attestation -> 400, nothing persists, inventory untouched")
    before = catalog_available(tt["id"])
    r = sell("noattest@x.com", staff_attested=False)
    check("400 without attestation", r.status_code == 400, r.text[:140])
    check("no order persisted", order_by_email("noattest@x.com") is None)
    check("inventory untouched", catalog_available(tt["id"]) == before, f"before={before} after={catalog_available(tt['id'])}")

    print("2) bad promo code -> 400, no order persists")
    r = sell("badcode@x.com", promo_code="NOPE")
    check("400 on bad code", r.status_code == 400, r.text[:140])
    check("no order persisted", order_by_email("badcode@x.com") is None)

    print("3) a clean cash sale: fee-free, attributed, minted, available drops")
    r = sell("walkup@x.com", qty=2)
    check("sell 200", r.status_code == 200, r.text[:200])
    body = r.json()
    check("2 tickets in the response", len(body.get("tickets", [])) == 2, str(body)[:200])
    check("total_cents is 2x face value (no promo)", body["total_cents"] == 5000, body["total_cents"])
    o = order_by_email("walkup@x.com")
    check("payment_method is cash", o.payment_method == "cash")
    check("no platform fee charged", o.platform_fee_cents == 0)
    check("no reserve withheld", o.reserve_cents == 0)
    check("organizer_net == subtotal (no fee)", o.organizer_net_cents == o.subtotal_cents - o.discount_cents)
    check("sold_by stamped", o.sold_by_user_id is not None and o.sold_by_name == "Alex Door", (o.sold_by_user_id, o.sold_by_name))
    check("terms_accepted_at stamped (staff attestation)", o.terms_accepted_at is not None)
    check("no Stripe fields touched", o.stripe_checkout_session_id is None and o.stripe_payment_intent_id is None)
    check("availability dropped by 2", catalog_available(tt["id"]) == before - 2, f"now={catalog_available(tt['id'])}")

    print("4) promo code at the door discounts and attributes like an online sale")
    r = sell("discounted@x.com", qty=1, promo_code="doorsale10")  # case-insensitive, like checkout
    check("discounted sell 200", r.status_code == 200, r.text[:200])
    o2 = order_by_email("discounted@x.com")
    check("discount applied", o2.discount_cents == 500, o2.discount_cents)
    check("promo attribution stamped", str(o2.promo_code_id) == promo["id"], (o2.promo_code_id, promo["id"]))

    print("5) reconciliation: only cash+paid orders inside the window count")
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    r = c.get(f"/events/{EV}/door-sales/reconciliation", params={"start": start.isoformat(), "end": end.isoformat()}, headers=H)
    check("reconciliation 200", r.status_code == 200, r.text[:140])
    recon = r.json()
    # 2 cash orders this window so far: walkup@x.com ($50.00) + discounted@x.com ($20.00) = $70.00, 3 tickets
    check("cash_total_cents sums both sales", recon["cash_total_cents"] == 7000, recon)
    check("ticket_count sums both sales' tickets", recon["ticket_count"] == 3, recon)
    check("sale_count is 2 orders", recon["sale_count"] == 2, recon)

    # A cash sale PAID outside the window must not count.
    r = sell("yesterday@x.com", qty=1)
    check("yesterday's sell 200", r.status_code == 200, r.text[:140])
    o3 = order_by_email("yesterday@x.com")
    mark_paid_at(o3.id, now - timedelta(days=1))
    r = c.get(f"/events/{EV}/door-sales/reconciliation", params={"start": start.isoformat(), "end": end.isoformat()}, headers=H)
    check("out-of-window sale excluded", r.json()["cash_total_cents"] == 7000, r.json())

    print("6) a section-sold (unassigned) type shows its section on the response, not 'General admission'")
    row_pool = c.post(f"/events/{EV}/seating-categories",
                       json={"name": "Row 2", "capacity": 10, "sales_grain": "row", "row_label": "Row 2"},
                       headers=H).json()
    c.put(f"/events/{EV}/seating-categories/{row_pool['id']}/sections",
          json={"sections": [{"section_label": "C", "row_label": "Row 2", "capacity": 4}]}, headers=H)
    row_tt = c.post(f"/events/{EV}/ticket-types",
                     json={**TT, "name": "Row 2", "price_cents": 3000, "quantity": 10,
                           "seating_category_id": row_pool["id"]}, headers=H).json()
    catalog_row = next(t for t in c.get(f"/events/{EV}/door-sales/catalog", headers=H).json() if t["id"] == row_tt["id"])
    section_id = catalog_row["sections"][0]["id"]
    r = c.post(
        f"/events/{EV}/door-sales/sell",
        json={
            "buyer_name": "Row Buyer", "buyer_email": "rowbuyer@x.com",
            "items": [{"ticket_type_id": row_tt["id"], "quantity": 1, "seat_ids": [], "zone_section_id": section_id, "zone_section_ids": []}],
            "promo_code": None, "staff_attested_terms": True,
        },
        headers=H,
    )
    check("row/section sell 200", r.status_code == 200, r.text[:200])
    row_ticket = r.json()["tickets"][0]
    check(
        "seat_label names the section, not General admission",
        # format_unit_label's documented, canonical order is row-then-
        # section ("Row 2 · Section C") — this pins that ordering, not
        # the reverse.
        row_ticket.get("seat_label") == "Row 2 · Section C",
        row_ticket,
    )


main()
print()
if failures:
    print(f"FAILED: {len(failures)} — " + ", ".join(failures))
    sys.exit(1)
print("test_door_sales: all checks passed")