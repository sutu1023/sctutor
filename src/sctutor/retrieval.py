import hashlib
import json
import re
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
from rank_bm25 import BM25Okapi

from .config import UserError
from .knowledge import Knowledge
from .pptx import chunks, parse_pptx


def tokenize(text):
    result = re.findall(r"[a-z0-9_]+", text.lower())
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend([run] if len(run) == 1 else [run[i:i+2] for i in range(len(run)-1)])
    return result


def fingerprint(key, text):
    return hashlib.sha256((key + "\0" + text).encode()).hexdigest()


async def ingest(store, models, directory: Path):
    directory = directory.resolve()
    if not directory.is_dir():
        raise UserError("课件目录不存在，未更改索引。")
    paths = sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() == ".pptx")
    old = {d["path"]: d for d in store.query("SELECT * FROM documents WHERE root=?",
                                            (str(directory),))}
    seen, report = set(), []
    for path in paths:
        rel = path.relative_to(directory).as_posix()
        seen.add(rel)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if (rel in old and old[rel]["hash"] == digest
                    and old[rel]["embedding_key"] == models.embedding_key):
                report.append({"file": rel, "status": "unchanged", "change": "unchanged",
                               "pages": store.query("SELECT count(*) n FROM pages WHERE doc_id=?", (old[rel]["id"],))[0]["n"]})
                continue
            slides = parse_pptx(path)
            if not slides:
                raise ValueError("empty presentation")
            prepared = [(s, uuid4().hex, chunks(s.content)) for s in slides]
            texts = list(dict.fromkeys(text for _, _, cs in prepared for text in cs))
            cache = {}
            for text in texts:
                rows = store.query("SELECT vector FROM embedding_cache WHERE key=?",
                                   (fingerprint(models.embedding_key, text),))
                if rows:
                    cache[text] = json.loads(rows[0]["vector"])
            missing = [t for t in texts if t not in cache]
            # Cache each successful batch so interrupted imports don't repay all previous batches.
            for offset in range(0, len(missing), 8):
                batch = missing[offset:offset + 8]
                vectors = await models.embed(batch)
                if len(vectors) != len(batch):
                    raise UserError("嵌入服务返回数量不符。")
                with store.connect() as c:
                    for text, vector in zip(batch, vectors, strict=True):
                        cache[text] = vector
                        c.execute("INSERT OR REPLACE INTO embedding_cache VALUES (?,?)",
                                  (fingerprint(models.embedding_key, text), json.dumps(vector)))
            if len({len(v) for v in cache.values()}) > 1:
                raise UserError("新旧嵌入维度不一致，请更换 DATA_DIR 后重建。")
            doc_id = uuid4().hex
            with store.connect() as c:
                if rel in old:
                    c.execute("DELETE FROM documents WHERE id=?", (old[rel]["id"],))
                c.execute("INSERT INTO documents VALUES (?,?,?,?,?)",
                          (doc_id, str(directory), rel, digest, models.embedding_key))
                for s, pid, cs in prepared:
                    c.execute("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?)",
                              (pid, doc_id, s.page, s.title, s.text, s.notes, s.images, bool(cs)))
                    c.executemany("INSERT INTO chunks VALUES (?,?,?,?)",
                                  [(uuid4().hex, pid, t, json.dumps(cache[t])) for t in cs])
            report.append({"file": rel, "status": "indexed", "change": "modified" if rel in old else "added", "pages": len(slides),
                           "unindexed_pages": sum(not cs for _, _, cs in prepared)})
        except Exception as e:
            # No paths/credentials/provider payloads from arbitrary exceptions enter output.
            report.append({"file": rel, "status": "error",
                           "message": str(e) if isinstance(e, UserError)
                           else "无法解析或处理该文件，保留此前可用版本。"})
    for rel, doc in old.items():
        if rel not in seen:
            store.execute("DELETE FROM documents WHERE id=?", (doc["id"],))
            report.append({"file": rel, "status": "deleted"})
    store.execute("INSERT INTO processing(created,kind,status,detail) VALUES (?,'ingest',?,?)",
                  (time.time(), "error" if any(r["status"] == "error" for r in report) else "completed",
                   json.dumps(report, ensure_ascii=False)))
    return report


def reciprocal_rank_fusion(branches, limit=20, k=60):
    """Nominate top-limit per branch, but do not erase a nominee's known tail rank.

    Both local branches already score the whole index. Truncating their rank maps
    makes a small 20→21 movement remove half of a candidate's score, even when
    the other branch strongly retrieves it. Keep the candidate budget unchanged
    and fuse each nominee using all its positive ranks; non-hits still earn zero.
    """
    ranks, candidates = [], set()
    for values in branches:
        order = sorted((i for i, value in enumerate(values) if value > 0),
                       key=lambda i: (-values[i], i))
        ranks.append({i: rank for rank, i in enumerate(order, 1)})
        candidates.update(order[:limit])
    return {i: sum(1 / (k + rank[i]) for rank in ranks if i in rank)
            for i in candidates}


def near_duplicate(left, right):
    def grams(text):
        value = re.sub(r"\W+", "", text.casefold())
        return {value[i:i+2] for i in range(len(value)-1)}
    a, b = grams(left), grams(right)
    return bool(a and b) and len(a & b) / len(a | b) >= 0.85


def prefer_body(pages):
    clusters = []
    for page in pages:
        existing = next((i for i, old in enumerate(clusters) if near_duplicate(page["page_text"], old["page_text"])), None)
        if existing is None:
            clusters.append(page)
        elif re.search(r"summary|summery|摘要", clusters[existing]["file"], re.I) and not re.search(r"summary|summery|摘要", page["file"], re.I):
            clusters[existing] = page
    return [{k: v for k, v in page.items() if k != "page_text"} for page in clusters[:5]]


class Retriever:
    def __init__(self, store, models):
        self.store, self.models = store, models

    async def search(self, query, tid=None):
        if not query.strip():
            return []
        query = Knowledge(self.store).expand(query)
        options = self.store.query('SELECT selected_doc FROM turn_options WHERE turn_id=?', (tid,)) if tid else []
        doc = options[0]['selected_doc'] if options else None
        rows = self.store.query(
            "SELECT c.text,c.vector,c.id chunk_id,p.id,p.page,p.title,p.text page_text,d.path file,d.embedding_key "
            "FROM chunks c JOIN pages p ON p.id=c.page_id JOIN documents d ON d.id=p.doc_id "
            "WHERE (? IS NULL OR d.id=?) ORDER BY c.rowid", (doc, doc))
        if not rows:
            raise UserError("课件索引为空，请先运行 sctutor ingest。")
        if any(r["embedding_key"] != self.models.embedding_key for r in rows):
            raise UserError("嵌入模型或接口已变更，请重新入库全部课件。")
        query_vector = (await self.models.embed([
            "Instruct: Given a course question, retrieve relevant lecture slides.\nQuery: " + query
        ], tid))[0]
        matrix = np.array([json.loads(r["vector"]) for r in rows], dtype=float)
        vector = np.array(query_vector, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != vector.shape[0]:
            raise UserError("查询与索引向量维度不符，请重建索引。")
        cosine = matrix @ vector / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector))
        tokens = [tokenize(r["text"]) or ["__empty__"] for r in rows]
        bm25 = BM25Okapi(tokens).get_scores(tokenize(query))
        scores = reciprocal_rank_fusion((bm25, cosine))
        pages = {}
        for i in sorted(scores, key=lambda i: (-scores[i], i)):
            r = rows[i]
            if r["id"] not in pages:
                pages[r["id"]] = {k: r[k] for k in ("id", "file", "page", "title", "page_text")}
                pages[r["id"]].update(text=r["text"], score=scores[i])
            elif r["id"] in pages and len(pages[r["id"]]["text"]) < 2400:
                pages[r["id"]]["text"] += "\n" + r["text"]
        return prefer_body(list(pages.values()))
