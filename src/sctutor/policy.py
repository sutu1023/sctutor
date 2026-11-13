"""Local teacher policy. Models never grant exemptions from these constraints."""
import json

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from .config import UserError

BLOCKED = "当前内容受到教师辅导策略限制，请调整问题或咨询助教。"


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_level: StrictInt = Field(default=4, ge=1, le=5)
    allow_full_answer: StrictBool = False
    web_search: StrictBool = False
    homework_level: StrictInt = Field(default=3, ge=1, le=3)
    blocked_concepts: list[str] = Field(default_factory=list, max_length=100)
    blocked_keywords: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("blocked_keywords", "blocked_concepts")
    @classmethod
    def bounded_strings(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if not value or len(value) > 120:
                raise ValueError("每项须为1–120字符")
            if value not in result:
                result.append(value)
        return result


class Policy:
    def __init__(self, store):
        self.store = store

    def get(self):
        row = self.store.query("SELECT * FROM teaching_policy WHERE id=1")
        return {**PolicyInput().model_dump(), "version": 0} if not row else {
            **PolicyInput().model_dump(), **json.loads(row[0]["data"]), "version": row[0]["version"]}

    def save(self, body, settings=None):
        if body.web_search:
            if settings is None or not settings.tavily_api_key:
                raise UserError('请先在本地配置TAVILY_API_KEY，再开启联网。')
        from .knowledge import Knowledge
        valid = {x["id"] for x in Knowledge(self.store).concepts(approved=True)}
        if not set(body.blocked_concepts) <= valid:
            raise UserError("禁答知识点必须已经审核且来源现行。")
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT version FROM teaching_policy WHERE id=1").fetchone()
            version = row[0] + 1 if row else 1
            c.execute("INSERT OR REPLACE INTO teaching_policy VALUES (1,?,?)",
                      (version, json.dumps(body.model_dump(), ensure_ascii=False)))
        return self.get()

    def assess(self, text="", concept_ids=(), page_ids=()):
        """Conservatively constrain all selected evidence, not just model labels."""
        p = self.get()
        pages = set(page_ids)
        ids = set(concept_ids)
        texts = [text]
        restricted = False
        for row in self.store.query("SELECT * FROM concepts WHERE status='approved'"):
            source = set(json.loads(row["pages"]))
            if row["id"] in ids or source & pages:
                ids.add(row["id"])
                restricted |= bool(row["restricted"])
                texts.append(row["name"])
        for pid in pages:
            rows = self.store.query("SELECT p.text,p.notes,a.caption FROM pages p LEFT JOIN page_assets a ON a.page_id=p.id WHERE p.id=?", (pid,))
            for row in rows:
                texts.extend(v for v in row.values() if v)
        haystack = "\n".join(texts).casefold()
        blocked = bool(ids & set(p["blocked_concepts"])) or any(
            word.casefold() in haystack for word in p["blocked_keywords"])
        cap = min(p['max_level'], 5 if p['allow_full_answer'] else 4)
        return {"blocked": blocked, "restricted": restricted, "max_level": min(cap, p["homework_level"])
                if restricted else cap, "version": p["version"]}

    def turn_state(self, turn):
        rows = self.store.query("SELECT * FROM turn_context WHERE turn_id=?", (turn["id"],))
        data = rows[0] if rows else {}
        ids = json.loads(data.get("concepts", "[]"))
        candidates = json.loads(data.get("candidates", "[]"))
        citations = turn.get("citations", [])
        if isinstance(citations, str):
            citations = json.loads(citations)
        pages = {x["id"] for x in [*candidates, *citations]}
        stored = self.store.query("SELECT session_id,created,rowid position FROM turns WHERE id=?", (turn["id"],))
        texts = [turn["question"]]
        if stored:
            row = stored[0]
            preceding = self.store.query("SELECT action,question FROM turns WHERE session_id=? AND (created<? OR (created=? AND rowid<=?)) ORDER BY created DESC,rowid DESC",
                                        (row["session_id"], row["created"], row["created"], row["position"]))
            for t in preceding:
                texts.append(t["question"])
                if t["action"] == "ask":
                    break
        external = self.store.query('SELECT sources FROM external_sources WHERE turn_id=?', (turn['id'],))
        state = self.assess("\n".join(texts), ids, pages)
        if external:
            state['blocked'] |= not self.get()['web_search']
            state['blocked'] |= self.assess(text=external[0]['sources'])['blocked']
        return state

    def require_turn(self, turn):
        state = self.turn_state(turn)
        if state["blocked"] or turn["level"] > state["max_level"]:
            raise UserError(BLOCKED)
        return state

    def require_pages(self, page_ids, text="", concept_ids=()):
        if self.assess(text, concept_ids, page_ids)["blocked"]:
            raise UserError(BLOCKED)
