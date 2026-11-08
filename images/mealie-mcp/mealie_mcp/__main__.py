"""Serve the Mealie MCP tools on port 8000.

Configuration comes from the environment:
- MEALIE_API_URL: where this process reaches Mealie, e.g. its cluster Service
- MEALIE_PUBLIC_URL: the Mealie address users open, for recipe links
- MEALIE_TOKEN: API token of the Mealie account the assistant acts as
- MCP_ALLOWED_HOSTS: comma-separated Host headers clients may use
"""

import logging
import os

import uvicorn

from .mealie import Mealie
from .server import build


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Request lines would log search terms; recipe writes are logged by name instead.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    mealie = Mealie(
        api_url=os.environ["MEALIE_API_URL"],
        public_url=os.environ["MEALIE_PUBLIC_URL"],
        token=os.environ["MEALIE_TOKEN"],
    )
    hosts = [h.strip() for h in os.environ["MCP_ALLOWED_HOSTS"].split(",") if h.strip()]
    uvicorn.run(build(mealie, hosts), host="0.0.0.0", port=8000, proxy_headers=False, server_header=False)


main()
