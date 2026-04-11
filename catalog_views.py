"""Server-rendered catalog pages.

Wire into main.py:

    from catalog_views import router as catalog_views_router
    app.include_router(catalog_views_router)

Place this BEFORE the Astro static mount but AFTER the API routers.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from auth import get_db
from models import Entity, Release, Track, release_entities

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _optional_user_from_request(request: Request):
    """Return the current user or None if not authenticated."""
    from auth import COOKIE_NAME, SECRET_KEY, ALGORITHM
    from jose import JWTError, jwt as jose_jwt
    from models import User

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            return None
    except JWTError:
        return None
    db = next(get_db())
    try:
        return db.query(User).filter(User.email == email).first()
    finally:
        db.close()


def _format_artist_names(entities):
    """Format entity names with roles for display.

    Entities come from the release_entities association with optional roles.
    For now, just join names — role formatting can be added later when
    the association proxy exposes roles.
    """
    if not entities:
        return "Unknown"
    return ", ".join(e.name for e in entities)


def _release_summary_dict(release: Release) -> dict:
    """Serialize a release for the __INITIAL_DATA__ payload."""
    return {
        "product_code": release.product_code,
        "title": release.title,
        "entities": [{"id": e.id, "name": e.name, "slug": e.slug} for e in release.entities],
        "release_date": release.release_date.isoformat() if release.release_date else None,
        "cover_art_url": f"/api/releases/{release.product_code}/cover" if release.cover_art_path else None,
        "status": release.status,
        "track_count": len(release.tracks),
        "total_duration_seconds": sum(t.duration_seconds or 0 for t in release.tracks),
    }


def _release_detail_dict(release: Release) -> dict:
    """Serialize a release with full detail for __INITIAL_DATA__."""
    d = _release_summary_dict(release)
    d["artist_names"] = _format_artist_names(release.entities)
    d["description"] = release.description
    d["format_specs"] = release.format_specs
    d["tracks"] = [
        {
            "id": t.id,
            "track_number": t.track_number,
            "title": t.title,
            "duration_seconds": t.duration_seconds,
            "stream_url": f"/api/releases/{release.product_code}/tracks/{t.id}/stream",
        }
        for t in release.tracks
    ]
    d["distribution_links"] = [
        {"id": dl.id, "platform": dl.platform, "url": dl.url, "label": dl.label}
        for dl in release.distribution_links
    ]
    d["metadata"] = [
        {"id": m.id, "key": m.key, "value": m.value, "sort_order": m.sort_order}
        for m in sorted(release.metadata_pairs, key=lambda x: x.sort_order)
    ]
    return d


def _build_json_ld(release: Release, artist_names: str) -> dict:
    """Build schema.org MusicAlbum JSON-LD for SEO."""
    ld = {
        "@context": "https://schema.org",
        "@type": "MusicAlbum",
        "name": release.title,
        "albumProductionType": "https://schema.org/StudioAlbum",
        "url": f"https://a-u.supply/catalog/{release.product_code}",
    }

    if release.release_date:
        ld["datePublished"] = release.release_date.isoformat()

    if artist_names:
        ld["byArtist"] = {
            "@type": "MusicGroup",
            "name": artist_names,
        }

    if release.cover_art_path:
        ld["image"] = f"https://a-u.supply/api/releases/{release.product_code}/cover"

    if release.tracks:
        ld["numTracks"] = len(release.tracks)
        ld["track"] = {
            "@type": "ItemList",
            "numberOfItems": len(release.tracks),
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": t.track_number,
                    "item": {
                        "@type": "MusicRecording",
                        "name": t.title,
                        "position": t.track_number,
                        **({"duration": f"PT{int(t.duration_seconds // 60)}M{int(t.duration_seconds % 60)}S"}
                           if t.duration_seconds else {}),
                        "url": f"https://a-u.supply/api/releases/{release.product_code}/tracks/{t.id}/stream",
                    },
                }
                for t in release.tracks
            ],
        }

    # Distribution links as offers
    if release.distribution_links:
        ld["offers"] = [
            {
                "@type": "Offer",
                "url": dl.url,
                "name": dl.label or dl.platform,
                "category": dl.platform,
            }
            for dl in release.distribution_links
        ]

    if release.description:
        ld["description"] = release.description

    return ld


@router.get("/catalog")
def catalog_page(request: Request, db: Session = Depends(get_db)):
    """Render the catalog grid page with all published releases."""
    user = _optional_user_from_request(request)

    q = (
        db.query(Release)
        .options(
            joinedload(Release.entities),
            joinedload(Release.tracks),
        )
        .filter(Release.status == "published")
        .order_by(Release.release_date.desc().nullslast())
    )

    releases_raw = q.all()

    # Deduplicate (joinedload can produce duplicates)
    seen = set()
    releases = []
    for r in releases_raw:
        if r.id not in seen:
            seen.add(r.id)
            releases.append(r)

    # Build filter options
    entities = db.query(Entity).order_by(Entity.name).all()

    years_raw = (
        db.query(func.strftime("%Y", Release.release_date))
        .filter(Release.status == "published", Release.release_date.isnot(None))
        .distinct()
        .all()
    )
    years = sorted([int(y[0]) for y in years_raw if y[0]], reverse=True)

    # Build initial data for client-side hydration
    initial_data = {
        "releases": [_release_summary_dict(r) for r in releases],
        "entities": [{"id": e.id, "name": e.name, "slug": e.slug} for e in entities],
        "years": years,
    }

    return templates.TemplateResponse(request, "catalog.html", context={
        "releases": releases,
        "entities": entities,
        "years": years,
        "initial_data": initial_data,
    })


@router.get("/catalog/{code}")
def release_page(code: str, request: Request, db: Session = Depends(get_db)):
    """Render the release detail / product specification page."""
    user = _optional_user_from_request(request)

    release = (
        db.query(Release)
        .options(
            joinedload(Release.entities),
            joinedload(Release.tracks),
            joinedload(Release.distribution_links),
            joinedload(Release.metadata_pairs),
        )
        .filter(Release.product_code == code)
        .first()
    )

    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    # Only show drafts to authenticated users
    if release.status == "draft" and user is None:
        raise HTTPException(status_code=404, detail="Release not found")

    artist_names = _format_artist_names(release.entities)
    json_ld = _build_json_ld(release, artist_names)
    detail_data = _release_detail_dict(release)

    initial_data = {
        "release": detail_data,
    }

    return templates.TemplateResponse(request, "release.html", context={
        "release": release,
        "artist_names": artist_names,
        "json_ld": json_ld,
        "initial_data": initial_data,
    })
