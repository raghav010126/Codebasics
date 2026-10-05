import pytest

from medibot import rbac


def test_collection_map_matches_spec():
    assert rbac.collections_for("doctor") == ["general", "clinical", "nursing"]
    assert rbac.collections_for("nurse") == ["general", "nursing"]
    assert rbac.collections_for("billing_executive") == ["general", "billing"]
    assert rbac.collections_for("technician") == ["general", "equipment"]
    assert rbac.collections_for("admin") == list(rbac.COLLECTIONS)


def test_general_visible_to_all():
    for role in rbac.ROLES:
        assert "general" in rbac.collections_for(role)


def test_sql_access():
    assert rbac.sql_tables_for("billing_executive") == ("claims",)
    assert rbac.sql_tables_for("admin") == ("claims", "maintenance_tickets")
    for role in ("doctor", "nurse", "technician"):
        assert rbac.sql_tables_for(role) == ()


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        rbac.collections_for("janitor")


def test_refusal_text_is_generated_from_map():
    msg = rbac.collection_refusal("nurse", ["billing"])
    assert msg.startswith("As a nurse")
    assert "billing documents" in msg
    assert "general and nursing" in msg
    assert "clinical" not in msg
