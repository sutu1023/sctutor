import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import UserError
from .prompts import rule


class ConceptDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    page_ids: list[str] = Field(min_length=1, max_length=8)
    aliases: list[str] = Field(default_factory=list, max_length=12)


class RelationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prerequisite: str
    target: str
    page_ids: list[str] = Field(min_length=1, max_length=8)


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concepts: list[ConceptDraft] = Field(max_length=12)
    relations: list[RelationDraft] = Field(default_factory=list, max_length=20)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["pending", "approved", "rejected"]
    name: str | None = Field(default=None, min_length=1, max_length=120)
    restricted: bool | None = None


class Knowledge:
    def __init__(self, store, models=None):
        self.store, self.models = store, models

    def valid_pages(self, ids):
        return bool(ids) and all(self.store.query("SELECT id FROM pages WHERE id=?", (pid,)) for pid in ids)

    def sources(self, ids):
        if not self.valid_pages(ids):
            raise UserError("知识来源已更新或不可用，请重新抽取和审核。")
        rows = [self.store.query("SELECT p.*,d.path file,a.caption FROM pages p JOIN documents d ON p.doc_id=d.id LEFT JOIN page_assets a ON a.page_id=p.id WHERE p.id=?", (pid,))[0] for pid in dict.fromkeys(ids)]
        for row in rows:
            if row.get("caption"):
                row["text"] += "\n" + row["caption"]
        return rows

    def concepts(self, approved=False):
        rows = self.store.query("SELECT * FROM concepts ORDER BY name,id")
        for r in rows:
            r["pages"] = json.loads(r["pages"])
            r["available"] = self.valid_pages(r["pages"])
            r["restricted"] = bool(r["restricted"])
        return [r for r in rows if r["status"] == "approved" and r["available"]] if approved else rows

    def relations(self, approved=False):
        valid = {r["id"] for r in self.concepts(approved=True)}
        rows = self.store.query("SELECT * FROM relations ORDER BY id")
        for r in rows:
            r["pages"] = json.loads(r["pages"])
            r["available"] = self.valid_pages(r["pages"]) and {r["prerequisite"], r["target"]} <= valid
        return [r for r in rows if r["status"] == "approved" and r["available"]] if approved else rows

    def aliases(self, approved=False):
        valid = {r["id"] for r in self.concepts(approved=True)}
        rows = self.store.query("SELECT a.*,c.name canonical FROM aliases a JOIN concepts c ON a.concept_id=c.id ORDER BY a.name,a.id")
        return [r for r in rows if r["status"] == "approved" and r["concept_id"] in valid] if approved else rows

    def expand(self, query):
        additions = []
        for a in self.aliases(approved=True):
            if a["name"].casefold() in query.casefold() and a["canonical"] not in additions:
                additions.append(a["canonical"])
        return query + ("\n相关课程术语：" + "、".join(additions[:12]) if additions else "")

    def add_extraction(self, raw, allowed):
        try:
            data = Extraction.model_validate(raw)
        except ValidationError:
            raise UserError("知识抽取格式无效，未发布候选。") from None
        names = {x.name for x in data.concepts}
        if len(names) != len(data.concepts):
            raise UserError("抽取知识点名称重复。")
        for item in [*data.concepts, *data.relations]:
            if not set(item.page_ids) <= set(allowed) or not self.valid_pages(item.page_ids):
                raise UserError("抽取引用不属于输入的现行课件页。")
        for x in data.concepts:
            if any(not a.strip() or len(a) > 120 for a in x.aliases):
                raise UserError("术语别名无效。")
        for r in data.relations:
            if r.prerequisite not in names or r.target not in names or r.prerequisite == r.target:
                raise UserError("前置关系必须引用本批两个不同知识点。")
        ids = {}
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for x in data.concepts:
                signature = hashlib.sha256((x.name.casefold() + json.dumps(sorted(set(x.page_ids)))).encode()).hexdigest()
                row = c.execute("SELECT id FROM concepts WHERE signature=?", (signature,)).fetchone()
                cid = row["id"] if row else uuid4().hex
                ids[x.name] = cid
                c.execute("INSERT OR IGNORE INTO concepts VALUES (?,?,?,'pending',0,?)", (cid, x.name, json.dumps(sorted(set(x.page_ids))), signature))
                for alias in set(a.strip() for a in x.aliases):
                    c.execute("INSERT OR IGNORE INTO aliases VALUES (?,?,?,'pending')", (uuid4().hex, cid, alias))
            for r in data.relations:
                c.execute("INSERT OR IGNORE INTO relations VALUES (?,?,?,?,'pending')", (uuid4().hex, ids[r.prerequisite], ids[r.target], json.dumps(sorted(set(r.page_ids)))))
        return {"concept_ids": list(ids.values()), "notice": "候选已保存，需本机管理员审核；未自动批准。"}

    async def extract(self, page_ids):
        if not 1 <= len(page_ids) <= 8:
            raise UserError("每批请选择1至8页，控制调用量。")
        sources = self.sources(page_ids)
        raw = await self.models.chat(rule("knowledge"), {"sources": [{k: s[k] for k in ("id", "text", "notes")} for s in sources]}, purpose="knowledge")
        return self.add_extraction(raw, page_ids)

    def review(self, kind, ident, body):
        if kind not in {"concepts", "aliases", "relations"}:
            raise UserError("未知审核类型。")
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute(f"SELECT * FROM {kind} WHERE id=?", (ident,)).fetchone()
            if not rows:
                raise UserError("审核项不存在。")
            if body.status == "approved":
                if kind == "concepts":
                    self.sources(json.loads(rows["pages"]))
                elif kind == "aliases":
                    if rows["concept_id"] not in {r["id"] for r in self.concepts(approved=True)}:
                        raise UserError("请先审核现行知识点。")
                else:
                    self.sources(json.loads(rows["pages"]))
                    valid = {r["id"] for r in self.concepts(approved=True)}
                    if not {rows["target"], rows["prerequisite"]} <= valid:
                        raise UserError("请先审核关系两端的现行知识点。")
                    edges = [(r["prerequisite"], r["target"]) for r in self.relations(approved=True)]
                    edges.append((rows["prerequisite"], rows["target"]))
                    pending, seen = [rows["target"]], set()
                    while pending:
                        node = pending.pop()
                        if node == rows["prerequisite"]:
                            raise UserError("前置关系不能形成环。")
                        if node not in seen:
                            seen.add(node)
                            pending.extend(b for a, b in edges if a == node)
            c.execute(f"UPDATE {kind} SET status=? WHERE id=?", (body.status, ident))
            if body.name is not None:
                if kind == "relations":
                    raise UserError("关系没有名称字段。")
                c.execute(f"UPDATE {kind} SET name=? WHERE id=?", (body.name, ident))
            if body.restricted is not None:
                if kind != "concepts":
                    raise UserError("只有知识点可设置作业限制。")
                c.execute("UPDATE concepts SET restricted=? WHERE id=?", (body.restricted, ident))
        return {"id": ident, "status": body.status}
