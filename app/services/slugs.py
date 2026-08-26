"""
Slug generation for public event pages — auto-generated from the title,
but the organizer can override it (checked for uniqueness either way).
"""

from slugify import slugify
from sqlalchemy.orm import Session

from app.models.event_profile import EventProfile


def generate_unique_slug(db: Session, desired: str, exclude_profile_id=None) -> str:
    """
    Turns `desired` (either the event title, or an organizer-typed custom
    slug) into a URL-safe, unique slug. Appends a short numeric suffix only
    if there's a real collision — keeps slugs clean in the common case.
    """
    base = slugify(desired) or "event"
    candidate = base
    suffix = 1
    while True:
        query = db.query(EventProfile).filter(EventProfile.slug == candidate)
        if exclude_profile_id:
            query = query.filter(EventProfile.id != exclude_profile_id)
        if not query.first():
            return candidate
        suffix += 1
        candidate = f"{base}-{suffix}"