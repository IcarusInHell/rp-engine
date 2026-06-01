"""Story card router package.

Split from the former monolithic ``routers/cards.py`` into domain sub-routers
(crud, authoring, analytics, admin) plus shared lookup helpers (``_lookup``).

ROUTE ORDER IS LOAD-BEARING. FastAPI matches routes in registration order, and
``crud`` owns the catch-all ``/{card_type}/{name}`` (and single-segment
``POST /{card_type}``) routes. Those would shadow the specific paths
(``/reindex``, ``/schema/{card_type}``, ``/validate``, ``/suggest``, ``/audit``,
``/connections``, ``/gaps/{name}/evidence``, ``/generate-name``) if mounted
first. So crud MUST be included LAST. A route-order assertion test guards this.
"""

from fastapi import APIRouter

from .admin import router as admin_router
from .analytics import router as analytics_router
from .authoring import router as authoring_router
from .crud import router as crud_router

# Each sub-router carries the /api/cards prefix itself (so the empty-path list
# route stays non-empty at registration). The aggregator just fixes ORDER.
router = APIRouter()

# Specific paths first …
router.include_router(admin_router)       # /reindex, /schema/{type}, /validate
router.include_router(analytics_router)   # /audit, /gaps/{name}/evidence, /connections
router.include_router(authoring_router)   # /suggest, /generate-name
# … catch-all (/{card_type}/{name}, POST /{card_type}) LAST.
router.include_router(crud_router)

__all__ = ["router"]
