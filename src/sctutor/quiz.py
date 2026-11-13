import asyncio
import json
import time
import unicodedata
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

from .config import UserError
from .knowledge import Knowledge
from .learning import observe, report
from .policy import Policy
from .prompts import rule


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    type: Literal["choice", "blank", "short", "code"]
    prompt: str = Field(min_length=1, max_length=4000)
    options: list[str] = Field(default_factory=list, max_length=6)
    answer: str = Field(min_length=1, max_length=6000)
    rubric: str = Field(min_length=1, max_length=4000)
    hint: str = Field(min_length=1, max_length=1000)
    concept_ids: list[str] = Field(min_length=1, max_length=5)
    page_ids: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def choices(self):
        if self.type == "choice":
            if not 2 <= len(self.options) <= 6 or len(set(self.options)) != len(self.options) or self.answer not in self.options:
                raise ValueError("choice requires distinct options and one matching answer")
        elif self.options:
            raise ValueError("only choice questions have options")
        if any(not x.strip() or len(x) > 1000 for x in self.options):
            raise ValueError("invalid option")
        return self


class Grade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correct: StrictBool
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=2000)


class Hint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)


class HintCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: StrictBool
    level_ok: StrictBool
    supported: StrictBool
    reason: str = Field(max_length=2000)


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class Quiz:
    def __init__(self, store, models, clock=time.time):
        self.store, self.models, self.clock = store, models, clock
        self.knowledge = Knowledge(store, models)
        self.lock = asyncio.Lock()

    def valid(self, data):
        concepts = {x["id"] for x in self.knowledge.concepts(approved=True) if not x["restricted"]}
        return (bool(data["concept_ids"]) and set(data["concept_ids"]) <= concepts
                and self.knowledge.valid_pages(data["page_ids"])
                and not Policy(self.store).assess(data["prompt"], data["concept_ids"], data["page_ids"])["blocked"])

    def questions(self):
        rows = self.store.query("SELECT * FROM questions ORDER BY created,id")
        for r in rows:
            r["data"] = json.loads(r["data"])
            r["available"] = self.valid(r["data"])
        return rows

    def add_question(self, raw, previous=None):
        try:
            q = Question.model_validate(raw).model_dump()
        except ValidationError:
            raise UserError("题目格式无效，未保存。") from None
        if not self.valid(q):
            raise UserError("题目须关联审核通过、非作业限制的知识点和现行课件页。")
        # Evidence must belong to the selected concepts, not an arbitrary valid page.
        allowed = {p for x in self.knowledge.concepts(approved=True)
                   if x["id"] in q["concept_ids"] for p in x["pages"]}
        if not set(q["page_ids"]) <= allowed:
            raise UserError("题目出处不属于所选知识点。")
        ident = uuid4().hex
        with self.store.connect() as c:
            version = 1
            if previous:
                old = c.execute("SELECT version FROM questions WHERE id=?", (previous,)).fetchone()
                if not old:
                    raise UserError("题目不存在。")
                version = old["version"] + 1
                c.execute("UPDATE questions SET status='rejected' WHERE id=?", (previous,))
            c.execute("INSERT INTO questions VALUES (?,?,?,'pending',?)", (ident, version, json.dumps(q, ensure_ascii=False), self.clock()))
        return {"id": ident, "version": version, "status": "pending"}

    def review_question(self, ident, status):
        if status not in {"pending", "approved", "rejected"}:
            raise UserError("审核状态无效。")
        rows = self.store.query("SELECT * FROM questions WHERE id=?", (ident,))
        if not rows or (status == "approved" and not self.valid(json.loads(rows[0]["data"]))):
            raise UserError("题目不存在或知识点/来源不可用。")
        self.store.execute("UPDATE questions SET status=? WHERE id=?", (status, ident))
        return {"id": ident, "status": status}

    async def generate(self, concept_id, kind="choice"):
        if kind not in {"choice", "blank", "short", "code"}:
            raise UserError("题型无效。")
        found = [x for x in self.knowledge.concepts(approved=True) if x["id"] == concept_id and not x["restricted"]]
        if not found:
            raise UserError("请先审核非作业限制的知识点。")
        x = found[0]
        raw = await self.models.chat(rule("questions"), {"type": kind, "concept": {"id": x["id"], "name": x["name"]}, "sources": self.knowledge.sources(x["pages"])}, purpose="questions")
        if not isinstance(raw, dict) or raw.get("type") != kind or raw.get("concept_ids") != [concept_id] or not set(raw.get("page_ids", [])) <= set(x["pages"]):
            raise UserError("生成题目的类型、知识点或出处无效。")
        return self.add_question(raw)

    async def start(self, count=3, concept_id=None, review_id=None):
        async with self.lock:
            if not 1 <= count <= 10:
                raise UserError("小测题量须为1至10。")
            if self.store.active_quiz():
                raise UserError("已有活动小测，请继续或取消后再开始。")
            states = report(self.store, self.knowledge, self.clock())["concepts"]
            priorities = {x["id"]: i for i, x in enumerate(states)}
            available = [q for q in self.questions() if q["status"] == "approved" and q["available"] and (not concept_id or concept_id in q["data"]["concept_ids"])]
            due = self.reviews()
            due = [r for r in due if r["available"] and r["due"] <= self.clock() and r["status"] == "pending" and (not review_id or r["id"] == review_id)]
            selected = []
            if review_id:
                if not due:
                    raise UserError("复测任务未到期、已完成或来源不可用。")
                count = 1
            for r in due:
                q = next((q for q in available if q["id"] == r["question_id"]), None)
                if q and q["id"] not in {q["id"] for q, _ in selected}:
                    selected.append((q, r["id"]))
                if len(selected) >= count:
                    break
            if not review_id:
                available.sort(key=lambda q: (min(priorities.get(cid, 100000) for cid in q["data"]["concept_ids"]), q["created"], q["id"]))
                for q in available:
                    if len(selected) >= count:
                        break
                    if q["id"] not in {x["id"] for x, _ in selected}:
                        selected.append((q, None))
            notice = ""
            if len(selected) < count:
                notice = f"审核题不足，当前可用{len(selected)}题。"
                candidates = [x for x in states if not x["restricted"] and (not concept_id or x["id"] == concept_id)]
                if candidates and self.store.account == "teacher":
                    try:
                        await self.generate(candidates[0]["id"])
                        notice += "已补充1道待审题，不加入本次测验。"
                    except Exception:
                        notice += "自动补题失败，请在管理页重试。"
                if not selected:
                    raise UserError(notice + "请先在管理页审核题目。")
            qid = uuid4().hex
            with self.store.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                if c.execute("SELECT 1 FROM quizzes q JOIN quiz_owners o ON o.quiz_id=q.id WHERE q.status='active' AND o.account=?", (self.store.account,)).fetchone() or c.execute("SELECT 1 FROM turns t JOIN session_owners o ON o.session_id=t.session_id WHERE t.status='running' AND o.account=?", (self.store.account,)).fetchone():
                    raise UserError("存在活动小测或答疑，请稍后。")
                c.execute("INSERT INTO quizzes VALUES (?,?,'active',NULL,?)", (qid, self.clock(), notice))
                c.execute("INSERT INTO quiz_owners VALUES (?,?)", (qid, self.store.account))
                for pos, (q, rid) in enumerate(selected):
                    c.execute("INSERT INTO quiz_items(id,quiz_id,question_id,snapshot,position,review_id) VALUES (?,?,?,?,?,?)", (uuid4().hex, qid, q["id"], json.dumps(q["data"], ensure_ascii=False), pos, rid))
            return self.view(qid)

    def checked_item(self, item_id):
        rows = self.store.query("SELECT i.*,q.status quiz_status FROM quiz_items i JOIN quizzes q ON i.quiz_id=q.id WHERE i.id=?", (item_id,))
        if not rows:
            raise UserError("测验题不存在。")
        item = rows[0]
        self.store.require_quiz(item["quiz_id"])
        item["data"] = json.loads(item["snapshot"])
        q = self.store.query("SELECT status,data FROM questions WHERE id=?", (item["question_id"],))
        if not q or q[0]["status"] != "approved" or not self.valid(item["data"]) or q[0]["data"] != item["snapshot"]:
            raise UserError("题目已停用或来源已变化，请取消小测后重新开始。")
        return item

    def view(self, qid):
        self.store.require_quiz(qid)
        active = self.store.active_quiz()
        if active and active != qid:
            raise UserError("活动小测期间不能查看其他测验的解答。")
        quizzes = self.store.query("SELECT * FROM quizzes WHERE id=?", (qid,))
        if not quizzes:
            raise UserError("小测不存在。")
        result = {**quizzes[0], "items": []}
        for row in self.store.query("SELECT * FROM quiz_items WHERE quiz_id=? ORDER BY position", (qid,)):
            data = json.loads(row["snapshot"])
            public = {k: row[k] for k in ("id", "position", "attempts", "done", "hint")}
            public.update({k: data[k] for k in ("type", "prompt", "options")})
            public["concepts"] = [r["name"] for r in self.knowledge.concepts() if r["id"] in data["concept_ids"]]
            if Policy(self.store).assess(concept_ids=data["concept_ids"], page_ids=data["page_ids"])["max_level"] < 2 and public["hint"]:
                public["hint"] = "请根据已有思路再尝试一次；教师当前未开放概念提示。"
            public["available"] = True
            try:
                self.checked_item(row["id"])
            except UserError:
                public.update(available=False, prompt="题目或来源受限/已失效，请取消后重新开始。",
                              options=[], concepts=[], hint="")
            if row["done"] and public["available"]:
                public.update(answer=data["answer"], rubric=data["rubric"], feedback=row["feedback"], first_correct=bool(row["first_correct"]), citations=[{k: x[k] for k in ("id", "file", "page")} for x in self.knowledge.sources(data["page_ids"])])
            result["items"].append(public)
        return result

    async def hint(self, data, answer):
        if Policy(self.store).assess(concept_ids=data["concept_ids"], page_ids=data["page_ids"])["max_level"] < 2:
            return "请根据已有思路再尝试一次；教师当前未开放概念提示。"
        for _ in range(3):
            try:
                raw = await self.models.chat(rule("hint"), {"question": data, "student_answer": answer}, purpose="quiz-hint")
                text = Hint.model_validate(raw).text
                verdict = HintCheck.model_validate(await self.models.chat(rule("tutor") + '\n独立检查下列小测提示是否只含L2信息、不泄露参考答案且有证据支持。仅输出{"passed":true或false,"level_ok":true或false,"supported":true或false,"reason":"原因"}。', {"question": data, "draft": text, "level": 2, "sources": self.knowledge.sources(data["page_ids"])}, purpose="quiz-hint-check"))
                if verdict.passed and verdict.level_ok and verdict.supported:
                    return text
            except ValidationError:
                continue
            except Exception:
                break
        return "暂未生成合规提示，请根据自己的理解再答一次。"

    async def submit(self, item_id, answer, attempt, request_id):
        async with self.lock:
            item = self.checked_item(item_id)
            existing = self.store.query("SELECT * FROM attempts WHERE request_id=?", (request_id,))
            if existing:
                old = existing[0]
                if old["item_id"] != item_id or old["answer"] != answer or old["number"] != attempt:
                    raise UserError("提交标识已被其他作答使用。")
                return self.view(item["quiz_id"])
            if item["quiz_status"] != "active" or item["done"] or attempt != item["attempts"] + 1:
                raise UserError("作答已完成或次数过期，请刷新进度。")
            data = item["data"]
            if data["type"] in {"choice", "blank"}:
                correct = normalize(answer) == normalize(data["answer"])
                grade = {"correct": correct, "score": float(correct), "reason": "程序按规范化参考答案判定", "needs_review": False}
            else:
                try:
                    grade = Grade.model_validate(await self.models.chat(rule("grade"), {"question": data, "student_answer": answer, "sources": self.knowledge.sources(data["page_ids"])}, purpose="quiz-grade")).model_dump()
                    grade["needs_review"] = True
                except ValidationError:
                    raise UserError("批改格式无效，未扣除作答次数，请重试。") from None
            done = grade["correct"] or attempt == 2
            # Never expose grading rationale or the stored hint on first failure.
            hint = await self.hint(data, answer) if not done else ""
            with self.store.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                current = c.execute("SELECT attempts,done FROM quiz_items WHERE id=?", (item_id,)).fetchone()
                if current["attempts"] != attempt - 1 or current["done"] or not c.execute("SELECT 1 FROM quizzes WHERE id=? AND status='active'", (item["quiz_id"],)).fetchone():
                    raise UserError("本题进度已变化，请刷新。")
                self.checked_item(item_id)
                c.execute("INSERT INTO attempts VALUES (?,?,?,?,?)", (request_id, item_id, attempt, answer, json.dumps(grade, ensure_ascii=False)))
                c.execute("UPDATE quiz_items SET attempts=?,done=?,first_correct=COALESCE(first_correct,?),feedback=?,hint=? WHERE id=?", (attempt, done, grade["correct"], grade["reason"] + ("（模型批改，待人工抽查）" if grade["needs_review"] else ""), hint, item_id))
            return self.view(item["quiz_id"])

    def finish(self, qid):
        self.store.require_quiz(qid)
        now = self.clock()
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            q = c.execute("SELECT * FROM quizzes WHERE id=?", (qid,)).fetchone()
            if not q:
                raise UserError("小测不存在。")
            if q["status"] == "completed":
                return self.view(qid)
            items = c.execute("SELECT * FROM quiz_items WHERE quiz_id=?", (qid,)).fetchall()
            if q["status"] != "active" or not items or any(not i["done"] for i in items):
                raise UserError("请先完成所有题目再交卷。")
            for item in items:
                data = self.checked_item(item["id"])["data"]
                for cid in set(data["concept_ids"]):
                    observe(c, cid, item["id"], bool(item["first_correct"]), now, self.store.mastery_table)
                if item["review_id"]:
                    c.execute("UPDATE reviews SET status='completed' WHERE id=?", (item["review_id"],))
                else:
                    for days in ([7] if item["first_correct"] else [1, 3, 7]):
                        c.execute("INSERT OR IGNORE INTO reviews VALUES (?,?,?,?,?,'pending')", (uuid4().hex, item["question_id"], item["id"], days, now + days * 86400))
            c.execute("UPDATE quizzes SET status='completed',finished=? WHERE id=?", (now, qid))
        return self.view(qid)

    def cancel(self, qid):
        self.store.require_quiz(qid)
        self.store.execute("UPDATE quizzes SET status='cancelled' WHERE id=? AND status='active'", (qid,))
        return self.view(qid)

    def reviews(self):
        valid = {q["id"] for q in self.questions() if q["status"] == "approved" and q["available"]}
        return [{**r, "available": r["question_id"] in valid} for r in self.store.query("SELECT r.* FROM reviews r JOIN quiz_items i ON i.id=r.origin_item JOIN quiz_owners o ON o.quiz_id=i.quiz_id WHERE o.account=? ORDER BY r.due,r.id", (self.store.account,))]

    def citation(self, item_id, page_id):
        item = self.checked_item(item_id)
        active = self.store.active_quiz()
        if (active and active != item["quiz_id"]) or not item["done"] or page_id not in item["data"]["page_ids"]:
            raise UserError("该题尚未完成或未引用此页。")
        return self.knowledge.sources([page_id])[0]
