"""Story HTTP adapters; all routes use the application's protected middleware."""

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.core.config import settings
from app.db.models import User
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.services import stories as service

router = Router()


def _reader(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    service.require_read(db, user)
    return user


def _author(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    service.require_manage(db, user)
    return user


@router.get("/v1/stories")
def list_stories(
    q: str = Query("", max_length=256),
    status: str = Query("", max_length=16),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(_reader),
    db: Session = Depends(get_db),
):
    return service.list_stories(
        user=user, db=db, q=q, status=status, limit=limit, offset=offset
    )


@router.post("/v1/stories")
def create_story(
    payload: service.StoryPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(_author),
    db: Session = Depends(get_db),
):
    return service.create_story(
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.get("/v1/stories/choices/{kind}")
def story_choices(
    kind: str,
    q: str = Query("", max_length=256),
    user=Depends(_author),
    db: Session = Depends(get_db),
):
    return service.choices(kind=kind, q=q, user=user, db=db)


@router.get("/v1/stories/{story_id}")
def get_story(
    story_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(_reader),
    db: Session = Depends(get_db),
):
    return service.get_story(story_id=story_id, framework=framework, user=user, db=db)


@router.patch("/v1/stories/{story_id}")
def update_story(
    story_id: str,
    payload: service.StoryPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(_author),
    db: Session = Depends(get_db),
):
    return service.update_story(
        story_id=story_id,
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.delete("/v1/stories/{story_id}")
def delete_story(
    story_id: str,
    request: Request,
    expected_version: int = Query(..., ge=1),
    user=Depends(_author),
    db: Session = Depends(get_db),
):
    return service.delete_story(
        story_id=story_id,
        expected_version=expected_version,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.get("/v1/stories/{story_id}/changelog")
def story_changelog(
    story_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(_reader),
    db: Session = Depends(get_db),
):
    return service.changelog(
        story_id=story_id, limit=limit, offset=offset, user=user, db=db
    )


@router.post("/v1/stories/{story_id}/items")
def add_items(
    story_id: str,
    payload: service.ItemsPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(_author),
    db: Session = Depends(get_db),
):
    return service.add_items(
        story_id=story_id,
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.patch("/v1/stories/{story_id}/items/{item_id}")
def update_item(
    story_id: str,
    item_id: str,
    payload: service.ItemPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(_author),
    db: Session = Depends(get_db),
):
    return service.update_item(
        story_id=story_id,
        item_id=item_id,
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.delete("/v1/stories/{story_id}/items/{item_id}")
def remove_item(
    story_id: str,
    item_id: str,
    request: Request,
    expected_version: int = Query(..., ge=1),
    framework: str = settings.default_framework_slug,
    user=Depends(_author),
    db: Session = Depends(get_db),
):
    return service.remove_item(
        story_id=story_id,
        item_id=item_id,
        expected_version=expected_version,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.put("/v1/stories/{story_id}/layout")
def save_layout(
    story_id: str,
    payload: service.LayoutPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(_author),
    db: Session = Depends(get_db),
):
    return service.save_layout(
        story_id=story_id,
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.get("/v1/stories/{story_id}/comments")
def list_comments(story_id: str, user=Depends(_reader), db: Session = Depends(get_db)):
    return service.list_comments(story_id=story_id, user=user, db=db)


@router.post("/v1/stories/{story_id}/comments")
def add_comment(
    story_id: str,
    payload: service.CommentPayload,
    request: Request,
    user=Depends(_reader),
    db: Session = Depends(get_db),
):
    return service.add_comment(
        story_id=story_id,
        payload=payload,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.delete("/v1/stories/{story_id}/comments/{comment_id}")
def delete_comment(
    story_id: str,
    comment_id: str,
    request: Request,
    user=Depends(_reader),
    db: Session = Depends(get_db),
):
    return service.delete_comment(
        story_id=story_id,
        comment_id=comment_id,
        user=user,
        db=db,
        context=change_context(request),
    )
