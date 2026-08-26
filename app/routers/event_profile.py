from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.event_profile import EventProfile
from app.schemas.event_profile import (
    EventProfileCreateOrUpdateRequest,
    EventProfileResponse,
    PublicEventProfileResponse,
)
from app.services.deps import CurrentUser
from app.services.event_access import require_event_access
from app.services.slugs import generate_unique_slug
from app.services.file_upload import upload_banner_photo

router = APIRouter(tags=["event-profile"])


def _get_or_404(db: Session, event_id: str) -> EventProfile:
    profile = db.query(EventProfile).filter(EventProfile.event_id == event_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="No profile has been set up for this event yet.")
    return profile


@router.get("/events/{event_id}/profile", response_model=EventProfileResponse)
def get_profile(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    return _get_or_404(db, event_id)


@router.put("/events/{event_id}/profile", response_model=EventProfileResponse)
def upsert_profile(
    event_id: str,
    payload: EventProfileCreateOrUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    """Creates the profile on first save, updates it on every save after."""
    profile = db.query(EventProfile).filter(EventProfile.event_id == event_id).first()

    desired_slug = payload.slug or payload.title
    slug = generate_unique_slug(db, desired_slug, exclude_profile_id=profile.id if profile else None)

    if profile:
        profile.title = payload.title
        profile.description = payload.description
        profile.address = payload.address
        profile.external_ticket_url = payload.external_ticket_url
        profile.slug = slug
    else:
        profile = EventProfile(
            event_id=event_id,
            title=payload.title,
            description=payload.description,
            address=payload.address,
            external_ticket_url=payload.external_ticket_url,
            slug=slug,
        )
        db.add(profile)

    db.commit()
    db.refresh(profile)
    return profile


@router.post("/events/{event_id}/profile/banner-photo", response_model=EventProfileResponse)
async def upload_profile_banner(
    event_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    profile = _get_or_404(db, event_id)
    url = await upload_banner_photo(file)
    profile.banner_photo_url = url
    db.commit()
    db.refresh(profile)
    return profile


@router.post("/events/{event_id}/profile/publish", response_model=EventProfileResponse)
def publish_profile(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    profile = _get_or_404(db, event_id)
    profile.is_published = True
    profile.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(profile)
    return profile


@router.post("/events/{event_id}/profile/unpublish", response_model=EventProfileResponse)
def unpublish_profile(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    profile = _get_or_404(db, event_id)
    profile.is_published = False
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/public/events/{slug}", response_model=PublicEventProfileResponse)
def get_public_event_profile(slug: str, db: Session = Depends(get_db)):
    """
    No authentication — this is the actual shareable page. Unpublished
    profiles 404 exactly like nonexistent ones, so a guessed/leaked slug
    for a draft event doesn't confirm anything exists.
    """
    profile = (
        db.query(EventProfile).filter(EventProfile.slug == slug, EventProfile.is_published.is_(True)).first()
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Event not found.")
    return profile