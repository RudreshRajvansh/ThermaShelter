from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from fastapi import Query
from pydantic import BaseModel

from shelter_engine.errors import EngineError, SimulationFailed, WeatherError
from shelter_engine.schema import CommanderCard

from .. import operate
from ..deps import Context, get_context
from ..schemas import Post, PostSummary

router = APIRouter()

POST_ID = r"^[A-Za-z0-9_-]{1,32}$"
STATUS_ORDER = {"red": 0, "amber": 1, "green": 2, None: 3}


class CardResult(BaseModel):
    post_id: str
    card: CommanderCard | None = None
    error: str | None = None


def local_now(context: Context, site_id: str) -> datetime:
    site = context.site(site_id)
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=site.time_zone_h)


def issue_card(context: Context, post: dict, lang: str,
               now: datetime | None = None) -> CommanderCard:
    if context.library is None:
        raise HTTPException(status_code=503, detail="The material database is not loaded.")
    site = context.site(post["site_id"])
    base = context.engine(post["site_id"])
    moment = now or local_now(context, post["site_id"])
    card = operate.tonight(post, site, context.library, Path(context.settings.data_dir), lang,
                           moment, {"comfort_model_id": base.comfort_model.id,
                                    "fuel": post.get("fuel", "kerosene"),
                                    "references": context.references,
                                    "catalogue": context.catalogue})
    context.store.save_card(post["id"], lang, card.issued_at.isoformat(),
                            card.model_dump(mode="json"))
    return card


def issue_all(context: Context, lang: str) -> list[CardResult]:
    results = []
    for post in context.store.list_posts():
        try:
            results.append(CardResult(post_id=post["id"], card=issue_card(context, post, lang)))
        except (EngineError, WeatherError, SimulationFailed, HTTPException) as error:
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            results.append(CardResult(post_id=post["id"], error=str(detail)))
    return sorted(results, key=lambda item: (
        STATUS_ORDER[item.card.status if item.card else None], item.post_id))


@router.get("/posts", response_model=list[PostSummary], tags=["operate"])
def list_posts(lang: str = Query(default="en", pattern="^(en|hi)$"),
               context: Context = Depends(get_context)) -> list[PostSummary]:
    summaries = []
    for payload in context.store.list_posts():
        card = context.store.load_card(payload["id"], lang)
        summaries.append(PostSummary(
            post=Post.model_validate(payload),
            status=card["status"] if card else None,
            night_of=card["night_of"] if card else None,
            issued_at=card["issued_at"] if card else None))
    return sorted(summaries, key=lambda item: (STATUS_ORDER[item.status], item.post.id))


@router.put("/posts/{post_id}", response_model=Post, tags=["operate"])
def save_post(post: Post, post_id: str = PathParam(pattern=POST_ID),
              context: Context = Depends(get_context)) -> Post:
    if post.id != post_id:
        raise HTTPException(status_code=422, detail="The post id in the path and body differ.")
    context.site(post.site_id)
    context.store.save_post(post.id, post.model_dump(mode="json"))
    return post


@router.delete("/posts/{post_id}", status_code=204, tags=["operate"])
def delete_post(post_id: str = PathParam(pattern=POST_ID),
                context: Context = Depends(get_context)) -> None:
    if not context.store.delete_post(post_id):
        raise HTTPException(status_code=404, detail=f"There is no post {post_id}.")


@router.post("/posts/cards", response_model=list[CardResult], tags=["operate"])
def cards_for_every_post(lang: str = Query(default="en", pattern="^(en|hi)$"),
                         context: Context = Depends(get_context)) -> list[CardResult]:
    return issue_all(context, lang)


@router.post("/posts/{post_id}/card", response_model=CommanderCard, tags=["operate"])
def new_card(post_id: str = PathParam(pattern=POST_ID),
             lang: str = Query(default="en", pattern="^(en|hi)$"),
             context: Context = Depends(get_context)) -> CommanderCard:
    post = context.store.load_post(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"There is no post {post_id}.")
    try:
        return issue_card(context, post, lang)
    except (EngineError, WeatherError, SimulationFailed) as error:
        raise HTTPException(status_code=502, detail=(
            f"Tonight's card for {post_id} could not be computed: {error}")) from error


@router.get("/posts/{post_id}/card", response_model=CommanderCard, tags=["operate"])
def last_card(post_id: str = PathParam(pattern=POST_ID),
              lang: str = Query(default="en", pattern="^(en|hi)$"),
              context: Context = Depends(get_context)) -> CommanderCard:
    card = context.store.load_card(post_id, lang)
    if card is None:
        raise HTTPException(status_code=404, detail=(
            f"No card has been issued for {post_id} in this language yet."))
    return CommanderCard.model_validate(card)
