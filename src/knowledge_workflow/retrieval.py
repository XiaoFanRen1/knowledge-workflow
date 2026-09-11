"""Scope filtering precedes both lexical and semantic candidate limits."""
from __future__ import annotations

import heapq
import json
import re

from .content import tokenize


def identifiers(query):
    quoted = re.findall(r"`([^`]{1,128})`", query)
    tokens = re.findall(r"(?<![A-Za-z0-9_])[A-Za-z0-9_]+(?![A-Za-z0-9_])", query)
    selected = [word for word in tokens if len(word) >= 2 and
                (word.isupper() or any(c.isdigit() for c in word) or "_" in word)]
    return list(dict.fromkeys(word.casefold() for word in [*quoted, *selected]))[:32]


def body_identifier_coverage(query_ids, body):
    body = body.casefold()
    return sum(bool(re.search(r"(?<![a-z0-9_])" + re.escape(identifier) + r"(?![a-z0-9_])", body))
               for identifier in query_ids)


def scope_sql(workspace=None, chip=None, include_history=False):
    conditions, args = [], []
    if not include_history:
        conditions.append("d.status NOT IN ('deprecated','superseded','archived','retired','obsolete','historical')")
    if workspace:
        conditions.append("(d.workspace_json='[]' OR EXISTS (SELECT 1 FROM json_each(d.workspace_json) WHERE value=?))")
        args.append(workspace)
    if chip:
        conditions.append("(d.chip='' OR lower(d.chip)=lower(?))")
        args.append(chip)
    return " AND ".join(conditions) or "1", args


def lexical(generation, query, scope, limit=40):
    tokens = list(dict.fromkeys(word for word in tokenize(query).split() if re.search(r"\w", word)))[:64]
    if not tokens:
        return []
    match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
    predicate, args = scope_sql(**scope)
    with generation.connect() as db:
        rows = db.execute(f"SELECT c.id,bm25(chunks_fts) AS score FROM chunks_fts "
            f"JOIN chunks c ON c.id=chunks_fts.rowid JOIN documents d ON d.path=c.path "
            f"WHERE chunks_fts MATCH ? AND {predicate} AND c.role NOT IN ('routing','search-terms') "
            "ORDER BY score,c.id LIMIT ?", (match, *args, limit)).fetchall()
    return [{"rowid": row["id"], "score": -row["score"], "retriever": "lexical"} for row in rows]


def semantic(generation, query, scope, model, limit=40):
    import numpy as np
    vector = model.encode([query], query=True)[0]
    predicate, args = scope_sql(**scope)
    best = []
    with generation.connect() as db:
        cursor = db.execute(f"SELECT c.id,v.dim,v.blob FROM chunks c JOIN chunk_vec v ON c.id=v.id "
            f"JOIN documents d ON d.path=c.path WHERE {predicate} AND c.role NOT IN ('routing','search-terms')", args)
        while rows := cursor.fetchmany(1024):
            for row in rows:
                if row["dim"] != model.profile["dimension"] or len(row["blob"]) != row["dim"] * 4:
                    raise ValueError("invalid_index_vector")
            matrix = np.stack([np.frombuffer(row["blob"], dtype=np.float32) for row in rows])
            scores = matrix @ vector
            for row, score in zip(rows, scores, strict=True):
                item = (float(score), -row["id"], row["id"])
                if len(best) < limit:
                    heapq.heappush(best, item)
                elif item > best[0]:
                    heapq.heapreplace(best, item)
    return [{"rowid": number, "score": score, "retriever": "semantic"}
            for score, _, number in sorted(best, reverse=True)]


def merge(lexical_hits, semantic_hits):
    scores, hits = {}, {}
    for group in (lexical_hits, semantic_hits):
        for rank, hit in enumerate(group):
            number = hit["rowid"]
            scores[number] = scores.get(number, 0) + 1 / (60 + rank + 1)
            hits[number] = hit
    return [{**hits[number], "score": scores[number], "retriever": "hybrid"}
            for number in sorted(scores, key=lambda n: (-scores[n], n))]
