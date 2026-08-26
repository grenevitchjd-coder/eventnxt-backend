from datetime import datetime, timezone
from dateutil import parser as date_parser

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.event_profile import EventProfile
from app.models.event_profile_link import EventProfileLink
from app.models.event_profile_schedule_item import EventProfileScheduleItem
from app.models.event_profile_photo import EventProfilePhoto, MAX_GALLERY_PHOTOS
from app.schemas.event_profile import (
    EventProfileCreateOrUpdateRequest,
    EventProfileResponse,
    EventProfileLinkCreateRequest,
    EventProfileLinkResponse,
    EventProfileScheduleItemCreateRequest,
    EventProfileScheduleItemResponse,
    PublicScheduleItemResponse,
    PublicDailyScheduleItemResponse,
    EventProfilePhotoResponse,
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


def _parse_cached_date(raw: str | None):
    return date_parser.isoparse(raw) if raw else None


def _refresh_cached_dates(profile: EventProfile, user: CurrentUser) -> None:
    """
    Pulls fresh dates from Events360 onto the profile — require_event_access
    already fetched them for this request (user.event_data), so this is
    just writing them down. Called at the moments staleness actually
    matters (adding a schedule item, publishing), not just the main Save
    button, so a profile that predates the event having confirmed dates
    self-heals the next time anything meaningful happens to it.
    """
    profile.cached_start_date = _parse_cached_date(user.event_data.get("start_date"))
    profile.cached_end_date = _parse_cached_date(user.event_data.get("end_date"))


def _split_schedule_for_public(items):
    """
    Daily items are shown ONCE, not expanded per day of the event — just
    the label and a plain wall-clock time, with zero Date/timezone
    conversion anywhere. One-time items pass through with their own
    specific date, unchanged.
    """
    daily = []
    special = []
    for item in items:
        if item.is_recurring:
            if item.time_of_day:
                daily.append(
                    PublicDailyScheduleItemResponse(
                        label=item.label, time_of_day=item.time_of_day.strftime("%H:%M")
                    )
                )
        else:
            if item.event_datetime:
                special.append(PublicScheduleItemResponse(label=item.label, event_datetime=item.event_datetime))
    daily.sort(key=lambda x: x.time_of_day)
    special.sort(key=lambda x: x.event_datetime)
    return daily, special


# ---------- Profile itself ----------


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
    """
    Creates the profile on first save, updates it on every save after.
    Also refreshes the cached event dates from Events360 every time —
    require_event_access already fetched them for this request.
    """
    profile = db.query(EventProfile).filter(EventProfile.event_id == event_id).first()

    desired_slug = payload.slug or payload.title
    slug = generate_unique_slug(db, desired_slug, exclude_profile_id=profile.id if profile else None)

    cached_start = _parse_cached_date(user.event_data.get("start_date"))
    cached_end = _parse_cached_date(user.event_data.get("end_date"))

    if profile:
        profile.title = payload.title
        profile.description = payload.description
        profile.address = payload.address
        profile.external_ticket_url = payload.external_ticket_url
        profile.slug = slug
        profile.cached_start_date = cached_start
        profile.cached_end_date = cached_end
    else:
        profile = EventProfile(
            event_id=event_id,
            title=payload.title,
            description=payload.description,
            address=payload.address,
            external_ticket_url=payload.external_ticket_url,
            slug=slug,
            cached_start_date=cached_start,
            cached_end_date=cached_end,
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


@router.post("/events/{event_id}/profile/logo", response_model=EventProfileResponse)
async def upload_profile_logo(
    event_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    profile = _get_or_404(db, event_id)
    url = await upload_banner_photo(file)  # same validation/storage — just a different destination field
    profile.logo_url = url
    db.commit()
    db.refresh(profile)
    return profile


@router.post("/events/{event_id}/profile/publish", response_model=EventProfileResponse)
def publish_profile(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    profile = _get_or_404(db, event_id)
    _refresh_cached_dates(profile, user)
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


# ---------- Links (contacts / socials) ----------


@router.get("/events/{event_id}/profile/links", response_model=list[EventProfileLinkResponse])
def list_links(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    profile = _get_or_404(db, event_id)
    return (
        db.query(EventProfileLink)
        .filter(EventProfileLink.event_profile_id == profile.id)
        .order_by(EventProfileLink.sort_order)
        .all()
    )


@router.post("/events/{event_id}/profile/links", response_model=EventProfileLinkResponse, status_code=201)
def create_link(
    event_id: str,
    payload: EventProfileLinkCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    profile = _get_or_404(db, event_id)
    link = EventProfileLink(
        event_profile_id=profile.id, kind=payload.kind, label=payload.label, value=payload.value
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.delete("/events/{event_id}/profile/links/{link_id}", status_code=204)
def delete_link(
    event_id: str,
    link_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    profile = _get_or_404(db, event_id)
    link = (
        db.query(EventProfileLink)
        .filter(EventProfileLink.id == link_id, EventProfileLink.event_profile_id == profile.id)
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Link not found.")
    db.delete(link)
    db.commit()


# ---------- Schedule items ----------


@router.get("/events/{event_id}/profile/schedule", response_model=list[EventProfileScheduleItemResponse])
def list_schedule_items(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    profile = _get_or_404(db, event_id)
    return (
        db.query(EventProfileScheduleItem)
        .filter(EventProfileScheduleItem.event_profile_id == profile.id)
        .order_by(EventProfileScheduleItem.sort_order, EventProfileScheduleItem.event_datetime)
        .all()
    )


@router.post(
    "/events/{event_id}/profile/schedule", response_model=EventProfileScheduleItemResponse, status_code=201
)
def create_schedule_item(
    event_id: str,
    payload: EventProfileScheduleItemCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    profile = _get_or_404(db, event_id)
    if payload.is_recurring:
        # A daily item needs the event's real date range to expand across —
        # refresh now so it doesn't silently vanish on the public page if
        # this profile predates the event having confirmed dates.
        _refresh_cached_dates(profile, user)
    item = EventProfileScheduleItem(
        event_profile_id=profile.id,
        label=payload.label,
        is_recurring=payload.is_recurring,
        event_datetime=payload.event_datetime,
        time_of_day=payload.time_of_day,
        sort_order=payload.sort_order,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/events/{event_id}/profile/schedule/{item_id}", status_code=204)
def delete_schedule_item(
    event_id: str,
    item_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    profile = _get_or_404(db, event_id)
    item = (
        db.query(EventProfileScheduleItem)
        .filter(EventProfileScheduleItem.id == item_id, EventProfileScheduleItem.event_profile_id == profile.id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found.")
    db.delete(item)
    db.commit()


# ---------- Gallery photos ----------


@router.get("/events/{event_id}/profile/photos", response_model=list[EventProfilePhotoResponse])
def list_gallery_photos(
    event_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_event_access)
):
    profile = _get_or_404(db, event_id)
    return (
        db.query(EventProfilePhoto)
        .filter(EventProfilePhoto.event_profile_id == profile.id)
        .order_by(EventProfilePhoto.sort_order)
        .all()
    )


@router.post("/events/{event_id}/profile/photos", response_model=EventProfilePhotoResponse, status_code=201)
async def upload_gallery_photo(
    event_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    profile = _get_or_404(db, event_id)
    existing_count = (
        db.query(EventProfilePhoto).filter(EventProfilePhoto.event_profile_id == profile.id).count()
    )
    if existing_count >= MAX_GALLERY_PHOTOS:
        raise HTTPException(
            status_code=400, detail=f"Maximum of {MAX_GALLERY_PHOTOS} extra photos already added."
        )
    url = await upload_banner_photo(file)
    photo = EventProfilePhoto(event_profile_id=profile.id, url=url, sort_order=existing_count)
    db.add(photo)
    db.commit()
    db.refresh(photo)
    return photo


@router.delete("/events/{event_id}/profile/photos/{photo_id}", status_code=204)
def delete_gallery_photo(
    event_id: str,
    photo_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_event_access),
):
    profile = _get_or_404(db, event_id)
    photo = (
        db.query(EventProfilePhoto)
        .filter(EventProfilePhoto.id == photo_id, EventProfilePhoto.event_profile_id == profile.id)
        .first()
    )
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found.")
    db.delete(photo)
    db.commit()


# ---------- Public page ----------


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

    links = (
        db.query(EventProfileLink)
        .filter(EventProfileLink.event_profile_id == profile.id)
        .order_by(EventProfileLink.sort_order)
        .all()
    )
    schedule_items = (
        db.query(EventProfileScheduleItem)
        .filter(EventProfileScheduleItem.event_profile_id == profile.id)
        .all()
    )
    daily_schedule, schedule = _split_schedule_for_public(schedule_items)
    photos = (
        db.query(EventProfilePhoto)
        .filter(EventProfilePhoto.event_profile_id == profile.id)
        .order_by(EventProfilePhoto.sort_order)
        .all()
    )

    return PublicEventProfileResponse(
        title=profile.title,
        description=profile.description,
        address=profile.address,
        banner_photo_url=profile.banner_photo_url,
        logo_url=profile.logo_url,
        external_ticket_url=profile.external_ticket_url,
        cached_start_date=profile.cached_start_date,
        cached_end_date=profile.cached_end_date,
        links=links,
        daily_schedule=daily_schedule,
        schedule=schedule,
        photos=photos,
    )