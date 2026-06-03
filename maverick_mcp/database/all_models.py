"""Single import point that registers every ORM model with ``Base.metadata``.

Component models live in their own service packages and are normally imported
lazily (inside MCP tools). That means ``Base.metadata`` is incomplete unless
something imports them first, so ``create_all`` / Alembic autogenerate would
miss their tables. Importing this module once registers them all.

Use it wherever the full schema must be known:
- ``ensure_database_schema()`` (so ``create_all`` covers every component)
- Alembic ``env.py`` (so ``target_metadata`` is complete)

Keep imports side-effect free: each module only defines models.
"""

from __future__ import annotations

# Core models (stocks, price cache, screening result tables, portfolio,
# backtest persistence, decision log). Importing the package registers them.
from maverick_mcp.data import models as _core_models  # noqa: F401
from maverick_mcp.database.base import Base

# Component models, one per service package.
from maverick_mcp.services.journal import models as _journal_models  # noqa: F401
from maverick_mcp.services.risk import models as _risk_models  # noqa: F401
from maverick_mcp.services.screening import models as _screening_models  # noqa: F401
from maverick_mcp.services.signals import models as _signals_models  # noqa: F401
from maverick_mcp.services.watchlist import models as _watchlist_models  # noqa: F401
from maverick_mcp.vc_loop import models as _vc_loop_models  # noqa: F401

__all__ = ["Base"]
