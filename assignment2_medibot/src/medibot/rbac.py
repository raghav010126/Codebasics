"""Single source of truth for role-based access control.

Every other module (ingestion metadata, Qdrant filters, SQL RAG, refusal text, the
API) derives its permissions from the maps below.
"""

ROLES = ("doctor", "nurse", "billing_executive", "technician", "admin")

# collection -> roles allowed to read it (spec: Data Sources table)
COLLECTION_ROLES: dict[str, tuple[str, ...]] = {
    "general": ROLES,
    "clinical": ("doctor", "admin"),
    "nursing": ("nurse", "doctor", "admin"),
    "billing": ("billing_executive", "admin"),
    "equipment": ("technician", "admin"),
}
COLLECTIONS = tuple(COLLECTION_ROLES)

COLLECTION_DESCRIPTIONS = {
    "general": "HR handbook, leave policy, code of conduct, general staff FAQs",
    "clinical": "treatment protocols, drug formulary, diagnostic reference (lab values, imaging)",
    "nursing": "ICU nursing procedures (SOPs), infection control guidelines",
    "billing": "insurance billing codes (ICD-10, package rates), claim submission and escalation guide",
    "equipment": "equipment operation and maintenance manual (calibration, fault codes, schedules)",
}

# role -> SQLite tables it may query via SQL RAG (table-level least privilege)
SQL_TABLE_ACCESS: dict[str, tuple[str, ...]] = {
    "billing_executive": ("claims",),
    "admin": ("claims", "maintenance_tickets"),
}
ALL_SQL_TABLES = ("claims", "maintenance_tickets")

ROLE_LABELS = {
    "doctor": "doctor",
    "nurse": "nurse",
    "billing_executive": "billing executive",
    "technician": "technician",
    "admin": "admin",
}

COLLECTION_LABELS = {
    "general": "general",
    "clinical": "clinical",
    "nursing": "nursing",
    "billing": "billing",
    "equipment": "equipment",
}


def validate_role(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role!r}")
    return role


def collections_for(role: str) -> list[str]:
    """Collections a role may read, in canonical order."""
    validate_role(role)
    return [c for c, roles in COLLECTION_ROLES.items() if role in roles]


def restricted_collections_for(role: str) -> list[str]:
    allowed = set(collections_for(role))
    return [c for c in COLLECTIONS if c not in allowed]


def sql_tables_for(role: str) -> tuple[str, ...]:
    validate_role(role)
    return SQL_TABLE_ACCESS.get(role, ())


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _article(word: str) -> str:
    return "an" if word[0].lower() in "aeiou" else "a"


def collection_refusal(role: str, requested: list[str]) -> str:
    """Informative refusal for a query aimed only at restricted collections."""
    role_label = ROLE_LABELS[role]
    blocked = _join([COLLECTION_LABELS[c] for c in requested])
    allowed = _join([COLLECTION_LABELS[c] for c in collections_for(role)])
    return (
        f"As {_article(role_label)} {role_label}, you don't have access to {blocked} documents. "
        f"I can only answer questions from the {allowed} collections."
    )


def sql_refusal(role: str, tables: list[str] | None = None) -> str:
    """Refusal for analytics (SQL) questions the role isn't entitled to."""
    role_label = ROLE_LABELS[role]
    allowed = sql_tables_for(role)
    if not allowed:
        return (
            f"As {_article(role_label)} {role_label}, you don't have access to the analytics "
            "database. Analytical queries are limited to billing executives and admins. "
            f"I can still answer document questions from the {_join(collections_for(role))} collections."
        )
    blocked = _join([f"`{t}`" for t in (tables or [])]) or "that table"
    return (
        f"As {_article(role_label)} {role_label}, you can only run analytical queries on "
        f"{_join([f'`{t}`' for t in allowed])}. You don't have access to {blocked}."
    )
