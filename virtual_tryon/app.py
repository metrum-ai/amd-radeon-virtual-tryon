# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""VTO FastAPI application entry point."""

from __future__ import annotations

import os

import redis.asyncio as aioredis
from fastapi import FastAPI
from prometheus_client import make_asgi_app as prom_asgi

from virtual_tryon.adapters.garment_catalog_adapter import GarmentCatalogAdapter
from virtual_tryon.api.routes import agent as agent_router
from virtual_tryon.api.routes import catalog as catalog_router
from virtual_tryon.api.routes import history as history_router
from virtual_tryon.api.routes import runtime as runtime_router
from virtual_tryon.api.routes import speech as speech_router
from virtual_tryon.api.routes import styling as styling_router
from virtual_tryon.catalog.catalog_service import CatalogService
from virtual_tryon.catalog.garment_embedder import GarmentEmbedder
from virtual_tryon.catalog.search_service import SearchService
from virtual_tryon.storage.customer_feedback_store import CustomerFeedbackStore
from virtual_tryon.storage.milvus_store import MilvusStore
from virtual_tryon.storage.postgres import PostgresPool
from virtual_tryon.storage.runtime_context_store import RuntimeContextStore
from virtual_tryon.storage.tryon_history import TryOnHistory

app = FastAPI(title="VTO API", version="0.1.0")
app.mount("/metrics", prom_asgi())

_adapter: GarmentCatalogAdapter | None = None
_runtime_store: RuntimeContextStore | None = None
_tryon_history: TryOnHistory | None = None
_feedback_store: CustomerFeedbackStore | None = None


def get_adapter() -> GarmentCatalogAdapter:
    """Return the singleton GarmentCatalogAdapter.

    Returns:
        Wired GarmentCatalogAdapter instance.

    Raises:
        RuntimeError: If called before app startup.

    """
    if _adapter is None:
        raise RuntimeError("Adapter not initialised — app not started yet")
    return _adapter


def get_runtime_store() -> RuntimeContextStore:
    """Return the singleton RuntimeContextStore.

    Returns:
        Wired RuntimeContextStore instance.

    Raises:
        RuntimeError: If called before app startup.

    """
    if _runtime_store is None:
        raise RuntimeError(
            "RuntimeContextStore not initialised — app not started yet"
        )
    return _runtime_store


def get_tryon_history() -> TryOnHistory:
    """Return the singleton TryOnHistory store.

    Returns:
        Wired TryOnHistory instance.

    Raises:
        RuntimeError: If called before app startup.

    """
    if _tryon_history is None:
        raise RuntimeError("TryOnHistory not initialised — app not started yet")
    return _tryon_history


def get_feedback_store() -> CustomerFeedbackStore:
    """Return the singleton CustomerFeedbackStore.

    Returns:
        Wired CustomerFeedbackStore instance.

    Raises:
        RuntimeError: If called before app startup.

    """
    if _feedback_store is None:
        raise RuntimeError(
            "CustomerFeedbackStore not initialised — app not started yet"
        )
    return _feedback_store


# Override the placeholder dependency in each router with the real singleton getters
app.dependency_overrides[catalog_router._get_adapter] = get_adapter
app.dependency_overrides[styling_router._get_adapter] = get_adapter
app.dependency_overrides[agent_router._get_adapter] = get_adapter
app.dependency_overrides[runtime_router._get_store] = get_runtime_store
app.dependency_overrides[history_router._get_history] = get_tryon_history
app.dependency_overrides[history_router._get_feedback_store] = (
    get_feedback_store
)


@app.on_event("startup")
async def startup() -> None:
    """Initialise DB pool, Redis, Milvus stores, embedder, adapter, and runtime store."""
    global _adapter, _runtime_store, _tryon_history, _feedback_store

    _openclaw_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    if not _openclaw_token:
        raise RuntimeError(
            "OPENCLAW_GATEWAY_TOKEN is required but not set or empty. "
            "Configure it in your environment or .env file."
        )

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL is required but not set. "
            "Configure it in your environment or .env file."
        )
    redis_url = os.environ.get("REDIS_URL", "redis://valkey:6379")
    milvus_host = os.environ.get("MILVUS_HOST", "localhost")
    milvus_port = int(os.environ.get("MILVUS_PORT", "19530"))

    db = await PostgresPool.connect(db_url)
    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    catalog = CatalogService(db)

    device = os.environ.get("EMBEDDER_DEVICE", "cpu")
    embedder = GarmentEmbedder(device=device)
    text_store = MilvusStore(milvus_host, milvus_port, "vto_catalog_text")
    visual_store = MilvusStore(milvus_host, milvus_port, "vto_catalog_visual")

    search = SearchService(catalog, embedder, text_store, visual_store)
    _adapter = GarmentCatalogAdapter(catalog, search, db)
    _runtime_store = RuntimeContextStore(redis_client, db)
    _tryon_history = TryOnHistory(db)
    _feedback_store = CustomerFeedbackStore(db)


app.include_router(agent_router.router)
app.include_router(speech_router.router)
app.include_router(catalog_router.router)
app.include_router(styling_router.router)
app.include_router(runtime_router.router)
app.include_router(history_router.router)


@app.get("/healthz")
async def healthz() -> dict:
    """Health check endpoint.

    Returns:
        Status dict.

    """
    return {"status": "ok"}
