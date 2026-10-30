"""Policy / SOP retrieval with pre-ranking ACL & temporal filtering.

Supports:
- lexical: offline BM25 + term-overlap ranking over authorized policy chunks.
- hybrid: Reciprocal Rank Fusion (RRF) combining BM25 lexical rank and real embedding
  cosine similarity from a configured EmbeddingProvider. Never uses random vectors.
"""

import math
import re
from collections import Counter

import httpx
from sqlalchemy import or_, select

from apps.api.config import settings
from apps.api.models import Policy


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _bm25_scores(query_tokens: list[str], docs_tokens: list[list[str]]) -> list[float]:
    if not query_tokens or not docs_tokens:
        return [0.0 for _ in docs_tokens]
    n_docs = len(docs_tokens)
    avgdl = sum(len(d) for d in docs_tokens) / max(n_docs, 1)
    k1, b = 1.5, 0.75
    df: Counter[str] = Counter()
    for doc in docs_tokens:
        for term in set(doc):
            df[term] += 1
    scores: list[float] = []
    q_set = set(query_tokens)
    for doc in docs_tokens:
        tf = Counter(doc)
        dl = len(doc)
        score = 0.0
        for term in q_set:
            if tf[term] == 0:
                continue
            idf = math.log(1.0 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            denom = tf[term] + k1 * (1.0 - b + b * (dl / max(avgdl, 1.0)))
            score += idf * ((tf[term] * (k1 + 1.0)) / max(denom, 1e-9))
        scores.append(score)
    return scores


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def fetch_embeddings(texts: list[str]) -> list[list[float]]:
    if not settings.embedding_api_key or not settings.embedding_model:
        raise ValueError("EMBEDDING_PROVIDER_NOT_CONFIGURED")
    url = settings.embedding_base_url.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.embedding_api_key}",
        "Content-Type": "application/json",
    }
    response = httpx.post(
        url,
        headers=headers,
        json={"model": settings.embedding_model, "input": texts},
        timeout=settings.llm_timeout_seconds,
    )
    if response.status_code != 200:
        raise ValueError(f"EMBEDDING_PROVIDER_ERROR_{response.status_code}")
    payload = response.json()
    data = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
    vectors = [item.get("embedding", []) for item in data]
    if len(vectors) != len(texts) or any(not v for v in vectors):
        raise ValueError("EMBEDDING_RESPONSE_INVALID")
    return vectors


def retrieve_policies(
    db,
    actor,
    effective_at: str,
    query: str,
    top_k: int = 3,
    mode: str | None = None,
) -> list[dict]:
    active_mode = mode or settings.retrieval_mode
    if active_mode not in {"lexical", "hybrid"}:
        raise ValueError("INVALID_RETRIEVAL_MODE")
    if active_mode == "hybrid" and (not settings.embedding_api_key or not settings.embedding_model):
        raise ValueError("EMBEDDING_PROVIDER_NOT_CONFIGURED")

    # 1. Pre-filter by tenant and temporal validity BEFORE ranking or context building
    candidates = db.scalars(
        select(Policy).where(
            Policy.tenant_id == actor.tenant_id,
            Policy.effective_from <= effective_at,
            or_(Policy.effective_to.is_(None), Policy.effective_to > effective_at),
        )
    ).all()

    # 2. Enforce role ACL BEFORE ranking
    authorized = [row for row in candidates if actor.role in row.allowed_roles]
    if not authorized:
        return []

    query_tokens = _tokenize(query)
    docs_tokens = [_tokenize(f"{row.section_id} {row.content}") for row in authorized]
    bm25 = _bm25_scores(query_tokens, docs_tokens)

    # Require at least one lexical term match in lexical mode so unrelated secret queries return []
    lexical_pairs: list[tuple[float, int, Policy]] = []
    q_terms = set(query_tokens)
    for idx, row in enumerate(authorized):
        overlap = len(q_terms & set(_tokenize(row.content)))
        if overlap > 0:
            lexical_pairs.append((bm25[idx] + overlap * 0.01, overlap, row))

    lexical_pairs.sort(key=lambda item: (-item[0], -item[1], item[2].id))
    limit = min(max(top_k, 1), 5)

    if active_mode == "lexical":
        return [
            {column.name: getattr(row, column.name) for column in row.__table__.columns}
            for _, _, row in lexical_pairs[:limit]
        ]

    # 3. Hybrid mode: Reciprocal Rank Fusion (RRF) of lexical rank and real embedding similarity
    texts = [query] + [f"{row.section_id}: {row.content}" for row in authorized]
    vectors = fetch_embeddings(texts)
    query_vec, doc_vecs = vectors[0], vectors[1:]
    sem_ranked = sorted(
        [( _cosine_similarity(query_vec, doc_vecs[i]), authorized[i]) for i in range(len(authorized))],
        key=lambda pair: (-pair[0], pair[1].id),
    )

    rrf_k = 60.0
    rrf_scores: dict[str, float] = {row.id: 0.0 for row in authorized}
    for rank, (_, _, row) in enumerate(lexical_pairs, start=1):
        rrf_scores[row.id] += 1.0 / (rrf_k + rank)
    for rank, (sim, row) in enumerate(sem_ranked, start=1):
        if sim > 0:
            rrf_scores[row.id] += 1.0 / (rrf_k + rank)

    combined = [
        (rrf_scores[row.id], row)
        for row in authorized
        if rrf_scores[row.id] > 0
    ]
    combined.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [
        {column.name: getattr(row, column.name) for column in row.__table__.columns}
        for _, row in combined[:limit]
    ]

