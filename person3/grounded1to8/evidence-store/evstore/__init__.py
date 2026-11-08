"""COPD evidence store: Postgres tables for lookups + pgvector RAG over the same documents.

Built on person 1's export (frontier-store.zip) and the team schema. See README.md.
"""

from evstore.retrieval import ChunkHit, Record, StoreClient

__all__ = ["ChunkHit", "Record", "StoreClient"]
