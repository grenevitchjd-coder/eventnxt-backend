# eventnxt-backend: test/test_self_promos.py  (verification harness)
#
# Self promos (migration 0042) + the promo-stats endpoint:
#   1. A self promo (no guest_id) creates with a discount and no reward.
#   2. Kind rules: self promo with reward terms → 400; referral code
#      without reward_type → 400; edit can't add reward terms to a self
#      promo; referral machinery (redemption options, per-code bonus
#      tiers) refuses self promos.
#   3. A 100%-off self promo checks out ($0 instant-paid path): Sale
#      rows attribute to the code, computed_reward is None, and NO
#      BonusAward rows appear even with event default bonus tiers set —
#      a self promo must not inherit them.
#   4. /promo-stats math: tickets = SUM(quantity), dollars = SUM(amount),
#      missing-amount rows counted separately, zero-sale codes present,
#      referral codes carry referrer_name, native + CSV count together.
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_self_promos.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

import app.services.email as email_mod
from app.main import app
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

email_mod.send_email = lambda **kw: None

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
D1 = "2026-12-24"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "SelfPromos", "start_date": D1, "end_date": D1}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)
H = {"Authorization": "Bearer tok"}

TT = {"description": None, "max_per_order": 20, "admits": 1, "sales_start": None, "sales_end": None,
      "is_active": True, "sort_order": 0, "valid_date": None}


def main():
    print("== setup: one GA type, published page ==")
    pool = c.post(f"/events/{EV}/seating-categories",
                  json={"name": "GA", "capacity": 50, "sales_grain": "ga", "row_label": None}, headers=H).json()
    tt = c.post(f"/events/{EV}/ticket-types",
                json={**TT, "name": "GA", "price_cents": 1000, "quantity": 50,
                      "seating_category_id": pool["id"]}, headers=H).json()
    c.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = c.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]

    print("== 1. self promo creates without a referrer ==")
    r = c.post(f"/events/{EV}/promo-codes",
               json={"code": "EARLYBIRD", "discount_type": "percentage", "discount_value": 100}, headers=H)
    check("self promo 201", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
    early = r.json()
    check("guest_id is null", early["guest_id"] is None)
    check("reward_type is null", early["reward_type"] is None)

    print("== 2. kind rules hold ==")
    r = c.post(f"/events/{EV}/promo-codes",
               json={"code": "BAD1", "reward_type": "flat_amount", "reward_value": 5}, headers=H)
    check("self promo with reward terms → 400", r.status_code == 400, f"{r.status_code}")
    gt = c.post(f"/events/{EV}/guest-types", json={"name": "Referrer", "guest_mode": "invite"}, headers=H).json()
    g = c.post(f"/events/{EV}/guests",
               json={"name": "Sarah", "email": "sarah@x.com", "guest_type_id": gt["id"],
                     "allocation_status": "pending", "party_size": 1, "guest_mode": "invite"},
               headers=H).json()
    r = c.post(f"/events/{EV}/promo-codes", json={"guest_id": g["id"], "code": "BAD2"}, headers=H)
    check("referral code without reward_type → 400", r.status_code == 400, f"{r.status_code}")
    r = c.post(f"/events/{EV}/promo-codes",
               json={"guest_id": g["id"], "code": "SARAH10", "reward_type": "flat_amount",
                     "reward_value": 2, "discount_type": "percentage", "discount_value": 10}, headers=H)
    check("referral code still creates as before", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
    sarah = r.json()
    r = c.patch(f"/events/{EV}/promo-codes/{early['id']}",
                json={"code": "EARLYBIRD", "reward_type": "flat_amount", "reward_value": 5,
                      "discount_type": "percentage", "discount_value": 100}, headers=H)
    check("edit can't add reward terms to a self promo", r.status_code == 400, f"{r.status_code}")
    tier = c.post(f"/events/{EV}/redemption-tiers", json={"points_required": 10}, headers=H)
    tier_id = tier.json()["id"] if tier.status_code == 201 else None
    if tier_id:
        r = c.put(f"/events/{EV}/promo-codes/{early['id']}/redemption-options/{tier_id}",
                  json={"cash_value": 5, "ticket_value": None}, headers=H)
        check("redemption options refuse a self promo", r.status_code == 400, f"{r.status_code}")
    r = c.put(f"/events/{EV}/promo-codes/{early['id']}/bonus-tiers",
              json={"tiers": [{"tickets_required": 1, "bonus_value": 1}]}, headers=H)
    check("per-code bonus tiers refuse a self promo", r.status_code == 400, f"{r.status_code}")

    print("== 3. checkout with a self promo: attributed, rewardless, bonus-proof ==")
    c.post(f"/events/{EV}/bonus-tiers", json={"tickets_required": 1, "bonus_value": 100}, headers=H)
    r = c.post(f"/public/events/{slug}/checkout",
               json={"buyer_name": "Buyer", "buyer_email": "b@x.com", "promo_code": "earlybird",
                     "items": [{"ticket_type_id": tt["id"], "quantity": 2}]})
    check("100%-off self promo checkout instant-paid", r.status_code == 200 and r.json().get("status") == "paid",
          f"{r.status_code} {r.text[:200]}")

    sales = c.get(f"/events/{EV}/sales", headers=H).json()
    attributed = [s for s in sales if s["promo_code_id"] == early["id"]]
    check("sale row attributed to the self promo", len(attributed) == 1, f"{len(attributed)}")
    check("computed_reward is None on a self-promo sale",
          attributed and attributed[0]["computed_reward"] is None)
    codes = c.get(f"/events/{EV}/promo-codes", headers=H).json()
    early_now = next(x for x in codes if x["id"] == early["id"])
    check("no bonus awards on the self promo (despite event default tier)",
          early_now["bonus_awards"] == [], str(early_now["bonus_awards"]))

    print("== 4. /promo-stats math ==")
    # CSV import: 3 tickets @ $30 for SARAH10, plus one amount-less row.
    r = c.post(f"/events/{EV}/sales/import",
               json={"rows": [
                   {"promo_code": "SARAH10", "buyer_name": "A", "buyer_email": "a@x.com", "amount": 30,
                    "ticket_type": "GA", "quantity": 3, "sale_date": D1, "external_transaction_id": "x1"},
                   {"promo_code": "SARAH10", "buyer_name": "B", "buyer_email": "bb@x.com", "amount": None,
                    "ticket_type": "GA", "quantity": 1, "sale_date": D1, "external_transaction_id": "x2"},
               ]}, headers=H)
    check("import accepted", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

    stats = {row["code"]: row for row in c.get(f"/events/{EV}/promo-stats", headers=H).json()}
    e, sa = stats.get("EARLYBIRD"), stats.get("SARAH10")
    check("both codes present", e is not None and sa is not None, str(list(stats)))
    check("self promo: 2 tickets sold", e and e["tickets_sold"] == 2, e and str(e["tickets_sold"]))
    check("self promo: $0 sold (100% off)", e and float(e["amount_sold"]) == 0.0, e and str(e["amount_sold"]))
    check("self promo row has no referrer_name", e and e["referrer_name"] is None)
    check("referral code: 4 tickets sold (3 + amount-less 1)", sa and sa["tickets_sold"] == 4,
          sa and str(sa["tickets_sold"]))
    check("referral code: $30 sold", sa and float(sa["amount_sold"]) == 30.0, sa and str(sa["amount_sold"]))
    check("referral code: 1 row missing amount", sa and sa["rows_missing_amount"] == 1,
          sa and str(sa["rows_missing_amount"]))
    check("referral code carries referrer_name", sa and sa["referrer_name"] == "Sarah",
          sa and str(sa["referrer_name"]))
    zero = c.post(f"/events/{EV}/promo-codes",
                  json={"code": "GHOST", "discount_type": "flat_amount", "discount_value": 1}, headers=H)
    check("zero-sale code appears in stats", zero.status_code == 201 and
          any(x["code"] == "GHOST" and x["tickets_sold"] == 0
              for x in c.get(f"/events/{EV}/promo-stats", headers=H).json()))

    print()
    if failures:
        print(f"self promos: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("self promos: all clear")


if __name__ == "__main__":
    main()