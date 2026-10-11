"""RBAC filter regression tests on an in-memory Qdrant with synthetic vectors (no models)."""

import pytest
from qdrant_client import QdrantClient, models

from medibot.rbac import COLLECTION_ROLES, ROLES
from medibot.retrieval.search import run_query
from medibot.retrieval.store import DENSE_DIM, DENSE_NAME, SPARSE_NAME, ensure_collection, rbac_filter

NAME = "t"
COLLECTIONS = list(COLLECTION_ROLES)


@pytest.fixture()
def client():
    c = QdrantClient(":memory:")
    ensure_collection(c, NAME)
    points = []
    for i, coll in enumerate(COLLECTIONS):
        # every chunk is an *identical* perfect match, so only the filter can exclude any
        points.append(
            models.PointStruct(
                id=i + 1,
                vector={DENSE_NAME: [1.0] + [0.0] * (DENSE_DIM - 1),
                        SPARSE_NAME: models.SparseVector(indices=[7], values=[1.0])},
                payload={"collection": coll, "access_roles": list(COLLECTION_ROLES[coll]),
                         "source_document": f"{coll}.pdf", "text": coll, "embed_text": coll,
                         "section_title": coll},
            )
        )
    c.upsert(NAME, points)
    return c


DENSE = [1.0] + [0.0] * (DENSE_DIM - 1)
SPARSE = models.SparseVector(indices=[7], values=[1.0])


@pytest.mark.parametrize("mode", ["dense", "sparse", "hybrid"])
@pytest.mark.parametrize("role", ROLES)
def test_only_permitted_collections_returned(client, role, mode):
    pts = run_query(client, NAME, DENSE, SPARSE, role, mode=mode, k=10)
    got = {p.payload["collection"] for p in pts}
    expected = {c for c, roles in COLLECTION_ROLES.items() if role in roles}
    assert got == expected  # nothing restricted AND nothing permitted is missing


def test_hybrid_fusion_does_not_bypass_filter(client):
    """Local mode ignores the outer query_filter during fusion; prefetch filters must hold."""
    pts = run_query(client, NAME, DENSE, SPARSE, "nurse", mode="hybrid", k=10)
    assert {p.payload["collection"] for p in pts} == {"general", "nursing"}


def test_filter_uses_must_only():
    flt = rbac_filter("nurse")
    assert flt.must and not flt.should and not flt.must_not
