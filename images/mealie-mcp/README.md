# Mealie MCP server

This image lets an assistant such as Claude work with Mealie recipes over MCP. It serves stateless Streamable HTTP with plain JSON responses at `/mcp` on port 8000, and `/healthz` reports that the process is serving.

There are four tools:

- `search_recipes` finds recipes by name or description, by relevance or by most recently updated or added.
- `get_recipe` reads one recipe. Ingredients, instructions and notes come back as plain-text lines.
- `create_recipe` adds a recipe.
- `update_recipe` changes the fields it is given. Lists are replaced whole. An unchanged line keeps its existing Mealie entry, including parsed foods and units, section titles, step IDs and ingredient links. New lines are stored as unparsed text.

Recipes cannot be deleted, and tags, categories, images and meal plans are not available. Before each update, the server records the previous values of the changed fields as a "Before AI edit" event on the recipe's timeline, so the old version can be copied back from the Mealie UI. It logs the slug and the given reason for each write, but no recipe content.

Configuration comes from the environment:

- `MEALIE_API_URL`, where the server reaches Mealie, for example `http://mealie.mealie.svc.cluster.local`
- `MEALIE_PUBLIC_URL`, the address users open, used for recipe links
- `MEALIE_TOKEN`, an API token for the Mealie account the assistant acts as
- `MCP_ALLOWED_HOSTS`, comma-separated `Host` header values accepted on `/mcp`; other hosts get HTTP 421

The server has no authentication of its own. Put it behind something that authenticates callers, and give it a dedicated non-admin Mealie user so a leaked token only grants that user's recipe access.
