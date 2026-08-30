"""
eventnxt-backend: app/scripts/seed_test_ticket_types.py

Creates sample ticket types for an event so Chunk 4 can be tested
end-to-end before the admin UI exists. Run from the Heroku console:

    python -m app.scripts.seed_test_ticket_types <event-slug> <organization-id>

Find the organization id first, from the EVENTS360-BACKEND console:

    python -c "from app.database import SessionLocal; from app.models.organization import Organization; db=SessionLocal(); [print(o.id, '-', o.name) for o in db.query(Organization).all()]"

Creates three types if none exist yet for the event (idempotent — a
second run refuses instead of duplicating):
  - Test General Admission  $20.00  x 50
  - Test Front Row          $50.00  x 10  (max 4/order)
  - Test Comp Ticket        $0.00   x 20  (the free path — skips Stripe)
"""

import sys
import uuid

from app.database import SessionLocal
from app.models.event_profile import EventProfile
from app.models.ticket_type import TicketType


def run():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    slug, org_id = sys.argv[1], sys.argv[2]

    db = SessionLocal()
    try:
        profile = db.query(EventProfile).filter(EventProfile.slug == slug).first()
        if not profile:
            print(f"No event profile found with slug '{slug}'.", file=sys.stderr)
            sys.exit(1)

        existing = db.query(TicketType).filter(TicketType.event_id == profile.event_id).count()
        if existing:
            print(f"Event already has {existing} ticket type(s) — refusing to duplicate. Nothing done.")
            return

        org_uuid = uuid.UUID(org_id)
        specs = [
            ("Test General Admission", 2000, 50, 10),
            ("Test Front Row", 5000, 10, 4),
            ("Test Comp Ticket", 0, 20, 2),
        ]
        for i, (name, price, qty, max_per) in enumerate(specs):
            db.add(
                TicketType(
                    event_id=profile.event_id,
                    organization_id=org_uuid,
                    name=name,
                    price_cents=price,
                    quantity=qty,
                    max_per_order=max_per,
                    sort_order=i,
                )
            )
        db.commit()
        print(f"Created {len(specs)} test ticket types for '{profile.title}' ({slug}).")
        print("Delete or edit them later from the admin UI (Chunk 5), or rerun checkout tests freely.")
    finally:
        db.close()


if __name__ == "__main__":
    run()