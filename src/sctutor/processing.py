"""Bounded, restart-visible teacher pipeline. Failures never imply approval."""
import asyncio
import hashlib
import json
import re
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .config import UserError
from .knowledge import Knowledge, Review
from .prompts import rule
from .quiz import Question
from .retrieval import ingest
from .vision import enhance


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generate_questions: StrictBool = False
    review_questions: StrictBool = False


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: StrictBool
    supported: StrictBool
    answer_ok: StrictBool
    rubric_ok: StrictBool
    restricted: StrictBool
    uncertain: StrictBool
    reason: str = Field(min_length=1, max_length=2000)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def signature(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class Pipeline:
    def __init__(self, store, models, settings, renderer, quiz, lock):
        self.store, self.models, self.settings = store, models, settings
        self.renderer, self.quiz, self.lock = renderer, quiz, lock
        self.knowledge = Knowledge(store, models)
        self.task = None
        store.execute("""CREATE TABLE IF NOT EXISTS pipeline_jobs (
            id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL,
            stage TEXT NOT NULL, detail TEXT NOT NULL, options TEXT NOT NULL)""")
        store.execute("""CREATE TABLE IF NOT EXISTS pipeline_cache (
            key TEXT PRIMARY KEY, kind TEXT NOT NULL, result TEXT NOT NULL)""")
        store.execute("""CREATE TABLE IF NOT EXISTS auto_reviews (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ident TEXT NOT NULL,
            created REAL NOT NULL, snapshot TEXT NOT NULL, verdict TEXT NOT NULL, outcome TEXT NOT NULL)""")

    def recover(self):
        self.store.execute("UPDATE pipeline_jobs SET status='blocked',detail=? WHERE status='running'",
                           (encoded([{"stage": "restart", "status": "blocked", "message": "服务中断，请重新启动处理；有效缓存保留。"}]),))

    def jobs(self):
        return [{**r, "detail": json.loads(r["detail"]), "options": json.loads(r["options"])}
                for r in self.store.query("SELECT * FROM pipeline_jobs ORDER BY created DESC LIMIT 10")]

    async def start(self, options):
        if self.lock.locked():
            raise UserError("课件正在处理中，请稍候。")
        await self.lock.acquire()
        ident = uuid4().hex
        try:
            self.store.execute("INSERT INTO pipeline_jobs VALUES (?,?,'running','queued','[]',?)", (ident, time.time(), encoded(options.model_dump())))
            self.task = asyncio.create_task(self.run(ident, options))
        except BaseException:
            self.lock.release()
            raise
        return {"id": ident, "status": "running"}

    def record(self, ident, stage, events, status="running"):
        self.store.execute("UPDATE pipeline_jobs SET status=?,stage=?,detail=? WHERE id=?",
                           (status, stage, encoded(events), ident))

    def cache(self, key):
        return self.store.query("SELECT result FROM pipeline_cache WHERE key=?", (key,))

    def save_cache(self, key, kind, result):
        self.store.execute("INSERT OR REPLACE INTO pipeline_cache VALUES (?,?,?)", (key, kind, encoded(result)))

    def current(self, kind, ident):
        collection = self.quiz.questions() if kind == "questions" else getattr(self.knowledge, kind)()
        return next((x for x in collection if x["id"] == ident), None)

    def review_payload(self, kind, item):
        if kind == "questions":
            data = Question.model_validate(item["data"]).model_dump()
            if not self.quiz.valid(data):
                raise UserError("题目来源或知识点不可用。")
            concepts = [x for x in self.knowledge.concepts(approved=True) if x["id"] in data["concept_ids"]]
            if not set(data["page_ids"]) <= {p for x in concepts for p in x["pages"]}:
                raise UserError("题目出处不属于所选知识点。")
            return {"kind": kind, "candidate": data, "concepts": concepts,
                    "sources": self.knowledge.sources(data["page_ids"])}
        if kind == "aliases":
            concept = self.current("concepts", item["concept_id"])
            if not concept or concept["status"] != "approved" or concept["restricted"]:
                raise UserError("别名知识点未通过或受限制。")
            return {"kind": kind, "candidate": item, "concept": concept, "sources": self.knowledge.sources(concept["pages"])}
        if kind == "relations":
            concepts = [self.current("concepts", item[x]) for x in ("prerequisite", "target")]
            if any(not x or x["status"] != "approved" or x["restricted"] for x in concepts):
                raise UserError("关系依赖未通过或受限制。")
            return {"kind": kind, "candidate": item, "concepts": concepts, "sources": self.knowledge.sources(item["pages"])}
        if item["restricted"]:
            raise UserError("作业限制知识点须人工审核。")
        return {"kind": kind, "candidate": item, "sources": self.knowledge.sources(item["pages"])}

    async def auto_review(self, kind, item):
        if item["status"] != "pending":
            return {"status": "skipped", "id": item["id"]}
        key = None
        try:
            payload = self.review_payload(kind, item)
            key = signature(["review", item["id"], payload, rule("auto-review"), self.model_key])
            cached = self.cache(key)
            if cached:
                previous = Verdict.model_validate(json.loads(cached[0]["result"]))
                return {"status": "manual", "id": item["id"], "message": previous.reason,
                        "notice": "沿用此前未通过复核记录，仍需人工审核，未重复调用"}
            # A conservative lexical barrier supplements, but does not replace, content review.
            restricted = bool(re.search(r"作业|考试|提交.{0,8}(代码|报告|答案)|\b(homework|assignment|exam)\b", encoded(payload), re.I))
            if restricted:
                verdict = Verdict(passed=False, supported=False, answer_ok=False, rubric_ok=False,
                                  restricted=True, uncertain=True, reason="疑似作业或考试材料，留待教师审核。")
            else:
                verdict = Verdict.model_validate(await self.models.chat(rule("auto-review"), payload, purpose="auto-review"))
            approved = verdict.passed and verdict.supported and verdict.answer_ok and verdict.rubric_ok and not verdict.restricted and not verdict.uncertain
            # A human edit/rejection or source update during the model call invalidates the verdict.
            current = self.current(kind, item["id"])
            if current != item or self.review_payload(kind, current) != payload:
                raise UserError("复核期间内容或来源发生变化，保留人工处理。")
            if approved:
                if kind == "questions":
                    self.quiz.review_question(item["id"], "approved")
                else:
                    self.knowledge.review(kind, item["id"], Review(status="approved"))
            elif verdict.restricted and kind == "concepts":
                self.knowledge.review(kind, item["id"], Review(status="pending", restricted=True))
            outcome = "approved" if approved else "manual"
            self.store.execute("INSERT INTO auto_reviews(kind,ident,created,snapshot,verdict,outcome) VALUES (?,?,?,?,?,?)",
                               (kind, item["id"], time.time(), encoded(payload), encoded(verdict.model_dump()), outcome))
            self.save_cache(key, "review", verdict.model_dump())
            return {"id": item["id"], "status": outcome, "message": verdict.reason}
        except Exception as exc:
            message = str(exc) if isinstance(exc, UserError) else "独立复核失败或格式无效，留待人工审核。"
            self.store.execute("INSERT INTO auto_reviews(kind,ident,created,snapshot,verdict,outcome) VALUES (?,?,?,?,?,?)",
                               (kind, item["id"], time.time(), encoded(item), encoded({"message": message}), "blocked"))
            return {"id": item["id"], "status": "blocked", "message": message}

    @property
    def model_key(self):
        return [getattr(self.models, "settings", self.settings).llm_base_url,
                getattr(self.models, "settings", self.settings).llm_model,
                self.settings.llm_provider, self.settings.llm_profile]

    async def run(self, ident, options):
        events = []
        try:
            self.record(ident, "ingest", events)
            results = await ingest(self.store, self.models, self.settings.course_dir)
            events.append({"stage": "ingest", "results": results})
            self.record(ident, "enhance", events)
            if self.settings.enable_enhancement:
                enhanced = await enhance(self.store, self.models, self.settings.course_dir, self.renderer)
                events.append({"stage": "enhance", "results": enhanced})
            else:
                enhanced = []
                events.append({"stage": "enhance", "status": "skipped",
                               "message": "原页/视觉增强未启用，继续文本处理。"})
            if any(x["status"] in {"error", "blocked"} for x in [*results, *enhanced]):
                raise UserError("入库或增强存在失败，本次不自动发布；修复后重试，旧内容仍可用。")
            pages = self.store.query("SELECT id FROM pages ORDER BY doc_id,page")
            if not pages:
                raise UserError("无现行课件页可抽取。")
            # 32 batches/run, 100 items/kind/run, 10 generated questions/run.
            for offset in range(0, min(len(pages), 256), 8):
                self.record(ident, "extract", events)
                ids = [x["id"] for x in pages[offset:offset + 8]]
                key = signature(["extract", self.knowledge.sources(ids), rule("knowledge"), self.model_key])
                if not self.cache(key):
                    result = await self.knowledge.extract(ids)
                    self.save_cache(key, "extract", result)
                events.append({"stage": "extract", "pages": offset + len(ids), "total": len(pages)})
            if len(pages) > 256:
                events.append({"stage": "extract", "status": "blocked", "message": "达到本次256页上限，请人工分批处理其余页。"})
            for kind in ("concepts", "aliases", "relations"):
                pending = [x for x in getattr(self.knowledge, kind)() if x["status"] == "pending"]
                for item in pending[:100]:
                    self.record(ident, "review-" + kind, events)
                    events.append({"stage": "review-" + kind, **await self.auto_review(kind, item)})
                if len(pending) > 100:
                    events.append({"stage": "review-" + kind, "status": "blocked", "message": "达到本次100项上限，剩余项目下次处理。"})
            if options.generate_questions:
                concepts = [x for x in self.knowledge.concepts(approved=True) if not x["restricted"]]
                for concept in concepts[:10]:
                    self.record(ident, "generate-questions", events)
                    key = signature(["generate", concept, self.knowledge.sources(concept["pages"]), rule("questions"), self.model_key])
                    if not self.cache(key):
                        result = await self.quiz.generate(concept["id"])
                        self.save_cache(key, "generate", result)
                    events.append({"stage": "generate-questions", "concept": concept["id"], "status": "pending-or-cached"})
                if len(concepts) > 10:
                    events.append({"stage": "generate-questions", "status": "manual", "message": "每次最多生成10题，其余知识点可手动出题。"})
            if options.review_questions:
                pending = [x for x in self.quiz.questions() if x["status"] == "pending"]
                for item in pending[:100]:
                    self.record(ident, "review-questions", events)
                    events.append({"stage": "review-questions", **await self.auto_review("questions", item)})
                if len(pending) > 100:
                    events.append({"stage": "review-questions", "status": "blocked", "message": "达到100题复核上限，剩余题下次处理。"})
            blocked = any(x.get("status") in {"blocked", "manual"} for x in events)
            self.record(ident, "done", events, "needs-review" if blocked else "completed")
        except BaseException as exc:
            message = str(exc) if isinstance(exc, UserError) else "处理失败或中断，请修复后重试；已完成步骤的缓存保留。"
            events.append({"stage": "error", "status": "blocked", "message": message})
            self.record(ident, "error", events, "blocked")
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self.lock.release()
