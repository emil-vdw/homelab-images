"""The recipe operations an assistant may perform in Mealie, and nothing else.

Recipes are presented as plain text lines so a model can read and rewrite them
directly. Writes keep Mealie's structured data wherever a line is unchanged,
and every update first records the previous values on the recipe's timeline,
so an unwanted edit can be reverted from the Mealie UI. There is deliberately
no way to delete a recipe.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Literal, TypedDict

import httpx2

logger = logging.getLogger(__name__)

Order = Literal["relevance", "recently_updated", "recently_added"]

_ORDER_BY = {"recently_updated": "updatedAt", "recently_added": "createdAt"}


class MealieError(Exception):
    """A request Mealie rejected or could not serve, safe to show the model."""


class RecipeSummary(TypedDict):
    slug: str
    name: str
    description: str
    created_at: str
    updated_at: str


class Recipe(RecipeSummary):
    url: str
    servings: float | None
    recipe_yield: str
    prep_time: str
    cook_time: str
    total_time: str
    ingredients: list[str]
    instructions: list[str]
    notes: list[str]


@dataclass(frozen=True)
class Changes:
    """New values for a recipe. ``None`` leaves a field unchanged; lists replace."""

    name: str | None = None
    description: str | None = None
    servings: float | None = None
    recipe_yield: str | None = None
    prep_time: str | None = None
    cook_time: str | None = None
    total_time: str | None = None
    ingredients: list[str] | None = None
    instructions: list[str] | None = None
    notes: list[str] | None = None


class Mealie:
    def __init__(self, api_url: str, public_url: str, token: str, transport: httpx2.BaseTransport | None = None):
        self._public_url = public_url.rstrip("/")
        self._http = httpx2.Client(
            base_url=api_url.rstrip("/") + "/api",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )
        self._group_slug: str | None = None

    def search(self, query: str, order: Order = "relevance", limit: int = 10) -> list[RecipeSummary]:
        params: dict[str, str | int] = {"perPage": max(1, min(limit, 50))}
        if query:
            params["search"] = query
        if order in _ORDER_BY:
            params["orderBy"] = _ORDER_BY[order]
            params["orderDirection"] = "desc"
        page = self._request("GET", "/recipes", params=params).json()
        return [_summary(item) for item in page["items"]]

    def get(self, slug: str) -> Recipe:
        return self._view(self._raw(slug))

    def create(self, changes: Changes) -> Recipe:
        if not changes.name:
            raise MealieError("A new recipe needs a name.")
        slug = self._request("POST", "/recipes", json={"name": changes.name}).json()
        logger.info("created recipe %s", slug)
        return self._apply(self._raw(slug), replace(changes, name=None))

    def update(self, slug: str, changes: Changes, summary: str) -> Recipe:
        current = self._raw(slug)
        snapshot = _snapshot(current, changes)
        if not snapshot:
            return self._view(current)
        # Record the old values before writing, so a failed or unwanted edit is
        # always recoverable. A failed write leaves only a redundant event.
        self._request(
            "POST",
            "/recipes/timeline/events",
            json={
                "recipeId": current["id"],
                "subject": f"Before AI edit: {summary}"[:200],
                "eventType": "info",
                "eventMessage": snapshot,
            },
        )
        logger.info("updating recipe %s: %s", slug, summary)
        return self._apply(current, changes)

    def _apply(self, current: dict, changes: Changes) -> Recipe:
        body: dict[str, object] = {}
        for field, key in _SCALARS.items():
            value = getattr(changes, field)
            if value is not None:
                body[key] = value
        if changes.ingredients is not None:
            body["recipeIngredient"] = _merge(
                current["recipeIngredient"], _clean(changes.ingredients), _ingredient_text, _new_ingredient
            )
        if changes.instructions is not None:
            body["recipeInstructions"] = _merge(
                current["recipeInstructions"], _clean(changes.instructions), _step_text, _new_step
            )
        if changes.notes is not None:
            body["notes"] = [{"title": "", "text": text} for text in _clean(changes.notes)]
        if not body:
            return self._view(current)
        return self._view(self._request("PATCH", f"/recipes/{current['slug']}", json=body).json())

    def _raw(self, slug: str) -> dict:
        return self._request("GET", f"/recipes/{slug}").json()

    def _view(self, raw: dict) -> Recipe:
        return {
            **_summary(raw),
            "url": f"{self._public_url}/g/{self._group()}/r/{raw['slug']}",
            "servings": raw.get("recipeServings") or None,
            "recipe_yield": raw.get("recipeYield") or "",
            "prep_time": raw.get("prepTime") or "",
            "cook_time": raw.get("cookTime") or "",
            "total_time": raw.get("totalTime") or "",
            "ingredients": [_ingredient_text(i) for i in raw.get("recipeIngredient") or []],
            "instructions": [_step_text(s) for s in raw.get("recipeInstructions") or []],
            "notes": [n["text"] for n in raw.get("notes") or [] if n.get("text")],
        }

    def _group(self) -> str:
        if self._group_slug is None:
            self._group_slug = self._request("GET", "/users/self").json()["groupSlug"]
        return self._group_slug

    def _request(self, method: str, path: str, **kwargs) -> httpx2.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx2.HTTPError as error:
            raise MealieError(f"Mealie is unreachable: {type(error).__name__}") from error
        if response.status_code == 404:
            raise MealieError("No such recipe. Search for it to find its slug.")
        if response.is_error:
            raise MealieError(f"Mealie rejected the request ({response.status_code}): {_detail(response)}")
        return response


_SCALARS = {
    "name": "name",
    "description": "description",
    "servings": "recipeServings",
    "recipe_yield": "recipeYield",
    "prep_time": "prepTime",
    "cook_time": "cookTime",
    "total_time": "totalTime",
}


def _summary(raw: dict) -> RecipeSummary:
    return {
        "slug": raw["slug"],
        "name": raw["name"],
        "description": raw.get("description") or "",
        "created_at": raw.get("createdAt") or "",
        "updated_at": raw.get("updatedAt") or "",
    }


def _ingredient_text(ingredient: dict) -> str:
    return ingredient.get("display") or ingredient.get("note") or ingredient.get("originalText") or ""


def _step_text(step: dict) -> str:
    return step.get("text") or ""


def _new_ingredient(text: str) -> dict:
    return {"note": text, "display": text, "originalText": text}


def _new_step(text: str) -> dict:
    return {"text": text, "ingredientReferences": []}


def _clean(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if line.strip()]


def _merge(existing: list[dict], lines: list[str], text_of, new) -> list[dict]:
    """Build the new list, reusing an existing entry for each unchanged line.

    Reuse keeps what Mealie knows beyond the text: parsed foods and units,
    section titles, step IDs and ingredient links.
    """
    unused = defaultdict(list)
    for entry in existing:
        unused[text_of(entry)].append(entry)
    return [unused[line].pop(0) if unused[line] else new(line) for line in lines]


def _snapshot(current: dict, changes: Changes) -> str:
    """Describe the current value of every field the changes would alter."""
    before = {
        "Name": current.get("name") or "",
        "Description": current.get("description") or "",
        "Servings": str(current.get("recipeServings") or ""),
        "Yield": current.get("recipeYield") or "",
        "Prep time": current.get("prepTime") or "",
        "Cook time": current.get("cookTime") or "",
        "Total time": current.get("totalTime") or "",
        "Ingredients": [_ingredient_text(i) for i in current.get("recipeIngredient") or []],
        "Instructions": [_step_text(s) for s in current.get("recipeInstructions") or []],
        "Notes": [n.get("text") or "" for n in current.get("notes") or []],
    }
    after = {
        "Name": changes.name,
        "Description": changes.description,
        "Servings": None if changes.servings is None else str(float(changes.servings)),
        "Yield": changes.recipe_yield,
        "Prep time": changes.prep_time,
        "Cook time": changes.cook_time,
        "Total time": changes.total_time,
        "Ingredients": None if changes.ingredients is None else _clean(changes.ingredients),
        "Instructions": None if changes.instructions is None else _clean(changes.instructions),
        "Notes": None if changes.notes is None else _clean(changes.notes),
    }
    sections = []
    for label, old in before.items():
        new = after[label]
        if new is None or new == old:
            continue
        if isinstance(old, list):
            sections.append(f"{label}:\n" + "\n".join(f"- {line}" for line in old))
        else:
            sections.append(f"{label}: {old}")
    return "\n\n".join(sections)


def _detail(response: httpx2.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail)[:300] if detail else response.reason_phrase
