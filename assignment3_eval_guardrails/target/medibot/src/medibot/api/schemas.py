from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str
    accessible_collections: list[str]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # Accepted only so a mismatch can be rejected; the role used is ALWAYS the token's.
    role: str | None = None


class Source(BaseModel):
    source_document: str
    section_title: str
    collection: str


class RerankScore(BaseModel):
    source_document: str
    section_title: str
    score: float
    hybrid_rank: int


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    retrieval_type: str  # "hybrid_rag" | "sql_rag"
    role: str
    blocked: bool = False
    accessible_collections: list[str]
    sql_query: str | None = None
    rerank: list[RerankScore] = []


class CollectionsResponse(BaseModel):
    role: str
    collections: list[str]
    sql_tables: list[str]
