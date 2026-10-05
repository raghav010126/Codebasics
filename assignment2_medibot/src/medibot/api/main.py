"""FastAPI backend: /login, /chat, /collections/{role}, /health."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from medibot import auth, llm, rbac
from medibot.api.schemas import (
    ChatRequest, ChatResponse, CollectionsResponse, LoginRequest, LoginResponse,
)
from medibot.config import get_settings
from medibot.rag.pipeline import handle_chat

log = logging.getLogger("medibot.api")


def _warm_models() -> None:
    """Load embedders, reranker and the Qdrant client once at startup."""
    from medibot.retrieval import embeddings, rerank, store

    embeddings.dense_model()
    embeddings.sparse_model()
    rerank.reranker()
    store.get_client()


def create_app(warm: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if warm:
            _warm_models()
        yield

    app = FastAPI(title="MediBot API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8501", "http://127.0.0.1:8501", "http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/login", response_model=LoginResponse)
    def login(body: LoginRequest):
        user = auth.authenticate(body.username, body.password)
        if user is None:
            raise HTTPException(401, "Invalid username or password.")
        username, role = user
        return LoginResponse(
            access_token=auth.create_token(username, role),
            username=username,
            role=role,
            accessible_collections=rbac.collections_for(role),
        )

    @app.post("/chat", response_model=ChatResponse)
    def chat(body: ChatRequest, user: tuple[str, str] = Depends(auth.current_user)):
        _, role = user  # the only trusted source of the role
        if body.role is not None and body.role != role:
            raise HTTPException(403, "The role in the request does not match your authenticated role.")
        try:
            result = handle_chat(body.question, role)
        except llm.LLMError as e:
            log.warning("LLM failure: %s", e)
            raise HTTPException(503, f"The language model is unavailable: {e}") from e
        except Exception as e:  # vector store / unexpected
            log.exception("chat failed")
            raise HTTPException(503, "MediBot could not complete the request. Please try again.") from e
        return ChatResponse(
            answer=result.answer,
            sources=result.sources,
            retrieval_type=result.retrieval_type,
            role=role,
            blocked=result.blocked,
            accessible_collections=rbac.collections_for(role),
            sql_query=result.sql_query,
            rerank=result.rerank,
        )

    @app.get("/collections/{role}", response_model=CollectionsResponse)
    def collections(role: str):
        if role not in rbac.ROLES:
            raise HTTPException(404, f"Unknown role '{role}'. Valid roles: {', '.join(rbac.ROLES)}")
        return CollectionsResponse(
            role=role, collections=rbac.collections_for(role), sql_tables=list(rbac.sql_tables_for(role))
        )

    @app.get("/health")
    def health():
        out = {"status": "ok", "qdrant": False, "points": 0}
        try:
            from medibot.retrieval.store import get_client

            out["points"] = get_client().count(get_settings().collection_name, exact=True).count
            out["qdrant"] = True
        except Exception as e:
            out["status"] = "degraded"
            out["detail"] = type(e).__name__
        return out

    return app


app = create_app()
