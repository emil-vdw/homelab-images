"""MCP tools over the household's Mealie recipes, served as stateless Streamable HTTP.

Callers must be authenticated before requests reach this process; the server
accepts anyone using an allowed Host header. Its power is bounded by the tools
below and by the Mealie account behind its token.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .mealie import Changes, Mealie, MealieError, Order, Recipe, RecipeSummary

INSTRUCTIONS = """\
Tools for the user's recipe collection in Mealie.
Find recipes with search_recipes, then read one with get_recipe before changing it.
Ingredients, instructions and notes are lists of plain-text lines, one ingredient
or step per line. When updating, send the complete new list for any of these you
change; unchanged lines keep the data Mealie has parsed for them. Recipes cannot
be deleted. Share the recipe's url with the user after creating or updating it.
"""

Lines = list[str] | None


def build(mealie: Mealie, allowed_hosts: list[str]) -> Starlette:
    server = MCPServer("mealie", instructions=INSTRUCTIONS)
    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

    @server.tool(annotations=read_only)
    def search_recipes(
        query: Annotated[str, Field(description="Words to match in the name or description. Empty lists all.")] = "",
        order: Annotated[Order, Field(description="Use recently_updated or recently_added for 'last week's...'")] = "relevance",
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> list[RecipeSummary]:
        """Find recipes by name or description."""
        return _call(lambda: mealie.search(query, order, limit))

    @server.tool(annotations=read_only)
    def get_recipe(slug: str) -> Recipe:
        """Read a whole recipe by its slug."""
        return _call(lambda: mealie.get(slug))

    @server.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False, openWorldHint=False))
    def create_recipe(
        name: str,
        ingredients: list[str],
        instructions: list[str],
        description: str = "",
        servings: float | None = None,
        recipe_yield: Annotated[str, Field(description="e.g. '1 loaf' or '12 cookies'")] = "",
        prep_time: Annotated[str, Field(description="e.g. '15 minutes'")] = "",
        cook_time: str = "",
        total_time: str = "",
        notes: Lines = None,
    ) -> Recipe:
        """Add a new recipe to the collection."""
        changes = Changes(
            name=name,
            description=description or None,
            servings=servings,
            recipe_yield=recipe_yield or None,
            prep_time=prep_time or None,
            cook_time=cook_time or None,
            total_time=total_time or None,
            ingredients=ingredients,
            instructions=instructions,
            notes=notes,
        )
        return _call(lambda: mealie.create(changes))

    @server.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True, openWorldHint=False))
    def update_recipe(
        slug: str,
        change_summary: Annotated[str, Field(description="Short reason shown in the recipe's history, e.g. 'less spicy'")],
        name: str | None = None,
        description: str | None = None,
        servings: float | None = None,
        recipe_yield: str | None = None,
        prep_time: str | None = None,
        cook_time: str | None = None,
        total_time: str | None = None,
        ingredients: Annotated[Lines, Field(description="Complete replacement list")] = None,
        instructions: Annotated[Lines, Field(description="Complete replacement list")] = None,
        notes: Annotated[Lines, Field(description="Complete replacement list")] = None,
    ) -> Recipe:
        """Change an existing recipe. Omitted fields stay as they are.

        The previous values are saved to the recipe's timeline in Mealie.
        Renaming a recipe changes its slug; use the slug in the result afterwards.
        """
        changes = Changes(
            name=name,
            description=description,
            servings=servings,
            recipe_yield=recipe_yield,
            prep_time=prep_time,
            cook_time=cook_time,
            total_time=total_time,
            ingredients=ingredients,
            instructions=instructions,
            notes=notes,
        )
        return _call(lambda: mealie.update(slug, changes, change_summary))

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return server.streamable_http_app(
        stateless_http=True,
        # Plain JSON responses: nothing here streams, and SSE through proxies is fragile.
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts, allowed_origins=[]),
        host="0.0.0.0",
    )


def _call[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except MealieError as error:
        raise ToolError(str(error)) from error

