# eventnxt-backend: app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import auth, me, guest_types, seating_categories, guests, events, event_profile, event_settings, rsvp, sales, ticket_types, checkout, orders_admin

app = FastAPI(
    title="EventNXT",
    description="Guest lists, RSVPs, and influencer-tracked ticket sales for the FashioNXT suite.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(events.router)
app.include_router(event_profile.router)
app.include_router(event_settings.router)
app.include_router(guest_types.router)
app.include_router(seating_categories.router)
app.include_router(guests.router)
app.include_router(rsvp.router)
app.include_router(sales.router)
app.include_router(ticket_types.router)
app.include_router(checkout.router)
app.include_router(orders_admin.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}