"""
Alembic environment configuration for Maverick-MCP.

This file configures Alembic to work with the existing Django database,
managing only tables with the mcp_ prefix.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import every model so target_metadata covers the full schema: core models
# plus all component models (journal, watchlist, signals, risk, screening,
# vc_loop). Importing all_models registers them on the shared Base.metadata.
import maverick_mcp.database.all_models  # noqa: F401
from maverick_mcp.database.base import Base as DataBase

combined_metadata = DataBase.metadata

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Load .env so the CLI resolves the same DATABASE_URL as the app (`uv run` does
# not auto-load .env). Default to the app's SQLite database, matching
# SelfContainedDatabaseConfig.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv optional
    pass

# Get database URL from environment or use the app default
DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("MCP_DATABASE_URL")
    or "sqlite:///maverick_mcp.db"
)

# Override sqlalchemy.url in alembic.ini
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# add your model's MetaData object here
# for 'autogenerate' support
# Use the combined metadata from both Base objects
target_metadata = combined_metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def include_object(object, name, type_, reflected, compare_to):
    """Manage only the tables defined in our model metadata.

    ``target_metadata`` now contains every Maverick-MCP model (core +
    components), so Alembic should manage exactly those tables and ignore any
    other tables that happen to exist in the database.
    """
    if type_ == "table":
        return name in target_metadata.tables
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
