# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Thin pymilvus wrapper for VTO catalog vector search."""

from __future__ import annotations

import json

_INDEX_PARAMS = {
    "metric_type": "COSINE",
    "index_type": "HNSW",
    "params": {"M": 16, "efConstruction": 200},
}
_SEARCH_PARAMS = {"metric_type": "COSINE", "params": {"ef": 64}}


class MilvusStore:
    """384-dim Milvus collection for text or visual garment embeddings.

    Args:
        host: Milvus server hostname.
        port: Milvus server port.
        collection_name: Name of the Milvus collection.
        dim: Embedding dimension (384 for both BGE-small and DINOv3-ViT-S).

    """

    def __init__(
        self,
        host: str,
        port: int,
        collection_name: str,
        dim: int = 384,
    ) -> None:
        from pymilvus import connections  # noqa: PLC0415

        self._collection_name = collection_name
        self._dim = dim
        connections.connect(host=host, port=port)
        self._collection = self._get_or_create_collection()

    def _get_or_create_collection(self):  # type: ignore[return]
        """Return existing collection or create it with standard schema."""
        from pymilvus import (  # noqa: PLC0415
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            utility,
        )

        if utility.has_collection(self._collection_name):
            coll = Collection(self._collection_name)
            coll.load()
            return coll

        fields = [
            FieldSchema(
                name="id",
                dtype=DataType.VARCHAR,
                max_length=64,
                is_primary=True,
            ),
            FieldSchema(
                name="item_id",
                dtype=DataType.VARCHAR,
                max_length=64,
            ),
            FieldSchema(
                name="vector",
                dtype=DataType.FLOAT_VECTOR,
                dim=self._dim,
            ),
        ]
        schema = CollectionSchema(fields, description=self._collection_name)
        coll = Collection(self._collection_name, schema)
        coll.create_index("vector", _INDEX_PARAMS)
        coll.load()
        return coll

    def upsert(
        self,
        ids: list[str],
        item_ids: list[str],
        vectors: list[list[float]],
    ) -> None:
        """Insert or replace vectors.

        Args:
            ids: Unique vector IDs (e.g. f"{item_id}_text").
            item_ids: Catalog item UUIDs (for retrieval join).
            vectors: List of 384-dim float vectors.

        """
        self._collection.delete(f'id in {json.dumps(ids)}')
        self._collection.insert([ids, item_ids, vectors])
        self._collection.flush()

    def search(
        self,
        query_vector: list[float],
        top_k: int = 20,
        expr: str | None = None,
    ) -> list[dict]:
        """Search for nearest neighbours.

        Args:
            query_vector: 384-dim query embedding.
            top_k: Number of results to return.
            expr: Optional Milvus boolean expression filter.

        Returns:
            List of dicts with keys ``item_id`` and ``score``.

        """
        results = self._collection.search(
            data=[query_vector],
            anns_field="vector",
            param=_SEARCH_PARAMS,
            limit=top_k,
            expr=expr,
            output_fields=["item_id"],
        )
        hits = []
        for hit in results[0]:
            hits.append(
                {"item_id": hit.entity.get("item_id"), "score": hit.score}
            )
        return hits

    def drop(self) -> None:
        """Drop the collection (used by seed script --recreate-collections)."""
        from pymilvus import utility  # noqa: PLC0415

        if utility.has_collection(self._collection_name):
            utility.drop_collection(self._collection_name)
