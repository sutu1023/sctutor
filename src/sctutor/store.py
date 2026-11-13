import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .auth import IDENTITY
from .config import UserError

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, root TEXT NOT NULL, path TEXT NOT NULL,
 hash TEXT NOT NULL, embedding_key TEXT NOT NULL, UNIQUE(root,path));
CREATE TABLE IF NOT EXISTS pages (
 id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 page INTEGER NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, notes TEXT NOT NULL,
 images INTEGER NOT NULL, indexed INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS chunks (
 id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
 text TEXT NOT NULL, vector TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS embedding_cache (
 key TEXT PRIMARY KEY, vector TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS turns (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 created REAL NOT NULL, action TEXT NOT NULL, question TEXT NOT NULL,
 level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 5), parent TEXT,
 status TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', citations TEXT NOT NULL DEFAULT '[]',
 elapsed REAL);
CREATE UNIQUE INDEX IF NOT EXISTS one_running_turn ON turns(session_id) WHERE status='running';
CREATE TABLE IF NOT EXISTS audits (
 id INTEGER PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id),
 attempt INTEGER NOT NULL, draft TEXT NOT NULL, verdict TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_times (audit_id INTEGER PRIMARY KEY REFERENCES audits(id) ON DELETE CASCADE, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS calls (
 id INTEGER PRIMARY KEY, turn_id TEXT, kind TEXT NOT NULL, model TEXT NOT NULL,
 elapsed REAL NOT NULL, usage TEXT, outcome TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS concepts (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, pages TEXT NOT NULL, status TEXT NOT NULL,
 restricted INTEGER NOT NULL DEFAULT 0, signature TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS aliases (
 id TEXT PRIMARY KEY, concept_id TEXT NOT NULL REFERENCES concepts(id),
 name TEXT NOT NULL, status TEXT NOT NULL, UNIQUE(concept_id,name));
CREATE TABLE IF NOT EXISTS relations (
 id TEXT PRIMARY KEY, prerequisite TEXT NOT NULL REFERENCES concepts(id),
 target TEXT NOT NULL REFERENCES concepts(id), pages TEXT NOT NULL, status TEXT NOT NULL,
 UNIQUE(prerequisite,target,pages));
CREATE TABLE IF NOT EXISTS questions (
 id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL,
 created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS quizzes (
 id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL, finished REAL, notice TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quiz_items (
 id TEXT PRIMARY KEY, quiz_id TEXT NOT NULL REFERENCES quizzes(id), question_id TEXT NOT NULL,
 snapshot TEXT NOT NULL, position INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 done INTEGER NOT NULL DEFAULT 0, first_correct INTEGER, feedback TEXT NOT NULL DEFAULT '',
 hint TEXT NOT NULL DEFAULT '', review_id TEXT, UNIQUE(quiz_id,position));
CREATE TABLE IF NOT EXISTS attempts (
 request_id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES quiz_items(id),
 number INTEGER NOT NULL, answer TEXT NOT NULL, grade TEXT NOT NULL, UNIQUE(item_id,number));
CREATE TABLE IF NOT EXISTS mastery (
 concept_id TEXT PRIMARY KEY REFERENCES concepts(id), probability REAL NOT NULL,
 count INTEGER NOT NULL, successes INTEGER NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
 item_id TEXT NOT NULL, concept_id TEXT NOT NULL, correct INTEGER NOT NULL,
 created REAL NOT NULL, version TEXT NOT NULL, previous REAL NOT NULL, PRIMARY KEY(item_id,concept_id));
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, question_id TEXT NOT NULL, origin_item TEXT NOT NULL,
 interval INTEGER NOT NULL, due REAL NOT NULL, status TEXT NOT NULL,
 UNIQUE(origin_item,interval));
CREATE TABLE IF NOT EXISTS page_assets (
 page_id TEXT PRIMARY KEY, image TEXT NOT NULL, caption TEXT NOT NULL, cache_key TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS caption_cache (key TEXT PRIMARY KEY, caption TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS processing (
 id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS teaching_policy (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turn_context (
 turn_id TEXT PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
 concepts TEXT NOT NULL, candidates TEXT NOT NULL, policy_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS turn_details (
 turn_id TEXT PRIMARY KEY REFERENCES turns(id), reason TEXT NOT NULL DEFAULT '',
 closed INTEGER NOT NULL DEFAULT 0);
"""


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "sctutor.sqlite3"
        with self.connect() as c:
            c.executescript(SCHEMA)
            # Rebuild only the legacy CHECK constraint; keep FK targets and positional rows.
            sql = c.execute("SELECT sql FROM sqlite_master WHERE name='turns'").fetchone()[0]
            if 'BETWEEN 1 AND 4' in sql:
                c.commit()
                c.execute('PRAGMA foreign_keys=OFF')
                c.execute('BEGIN IMMEDIATE')
                c.execute(sql.replace('CREATE TABLE turns', 'CREATE TABLE turns_new').replace('BETWEEN 1 AND 4', 'BETWEEN 1 AND 5'))
                c.execute('INSERT INTO turns_new SELECT * FROM turns')
                c.execute('DROP TABLE turns')
                c.execute('ALTER TABLE turns_new RENAME TO turns')
                c.execute("CREATE UNIQUE INDEX one_running_turn ON turns(session_id) WHERE status='running'")
                c.commit()
                c.execute('PRAGMA foreign_keys=ON')
                if c.execute('PRAGMA foreign_key_check').fetchone():
                    raise UserError('旧库迁移外键校验失败，请保留数据并检查。')
            # Side tables preserve legacy positional schemas and all historical records.
            c.executescript("""
                CREATE TABLE IF NOT EXISTS turn_requests (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL, turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                    PRIMARY KEY(session_id,request_id));
                CREATE TABLE IF NOT EXISTS turn_options (
                    turn_id TEXT PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
                    force_l1 INTEGER NOT NULL DEFAULT 0, selected_doc TEXT);
                CREATE TABLE IF NOT EXISTS external_sources (
                    turn_id TEXT PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
                    sources TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS session_owners (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                    account TEXT NOT NULL, name TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS quiz_owners (
                    quiz_id TEXT PRIMARY KEY REFERENCES quizzes(id) ON DELETE CASCADE,
                    account TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS teacher_mastery (
                    concept_id TEXT PRIMARY KEY REFERENCES concepts(id), probability REAL NOT NULL,
                    count INTEGER NOT NULL, successes INTEGER NOT NULL, updated REAL NOT NULL);
                DROP INDEX IF EXISTS one_active_quiz;
                INSERT OR IGNORE INTO session_owners(session_id,account) SELECT id,'student' FROM sessions;
                INSERT OR IGNORE INTO quiz_owners SELECT id,'student' FROM quizzes;
            """)
            if 'selected_doc' not in {r['name'] for r in c.execute('PRAGMA table_info(turn_options)')}:
                c.execute('ALTER TABLE turn_options ADD COLUMN selected_doc TEXT')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    def query(self, sql, args=()):
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.connect() as c:
            c.execute(sql, args)

    def recover(self):
        self.execute("UPDATE turns SET status='error',answer=? WHERE status='running'",
                     ("服务已重启，上次请求未完成，请重试。",))

    @property
    def account(self):
        return IDENTITY.get()

    @property
    def mastery_table(self):
        return "teacher_mastery" if self.account == "teacher" else "mastery"

    def require_session(self, sid):
        if not self.query("SELECT 1 FROM session_owners WHERE session_id=? AND account=?", (sid, self.account)):
            raise UserError("会话不存在或无权访问。")

    def require_turn(self, tid):
        rows = self.query("SELECT session_id FROM turns WHERE id=?", (tid,))
        if not rows:
            raise UserError("轮次不存在或无权访问。")
        self.require_session(rows[0]["session_id"])

    def require_quiz(self, qid):
        if not self.query("SELECT 1 FROM quiz_owners WHERE quiz_id=? AND account=?", (qid, self.account)):
            raise UserError("小测不存在或无权访问。")

    def new_session(self):
        sid = uuid4().hex
        with self.connect() as c:
            c.execute("INSERT INTO sessions VALUES (?,?)", (sid, time.time()))
            c.execute("INSERT INTO session_owners(session_id,account) VALUES (?,?)", (sid, self.account))
        return {"id": sid}

    def sessions(self, search=""):
        return self.query("""SELECT s.*,o.name FROM sessions s JOIN session_owners o ON o.session_id=s.id
            WHERE o.account=? AND (?='' OR instr(lower(o.name),lower(?))>0 OR EXISTS
                (SELECT 1 FROM turns t WHERE t.session_id=s.id AND instr(lower(t.question),lower(?))>0))
            ORDER BY s.created DESC""", (self.account, search, search, search))

    def rename_session(self, sid, name):
        self.require_session(sid)
        self.execute("UPDATE session_owners SET name=? WHERE session_id=? AND account=?", (name, sid, self.account))
        return {"id": sid, "name": name}

    def delete_sessions(self, ids):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for sid in ids:
                if not c.execute("SELECT 1 FROM session_owners WHERE session_id=? AND account=?", (sid, self.account)).fetchone():
                    raise UserError("会话不存在或无权访问，未删除任何会话。")
                if c.execute("SELECT 1 FROM turns WHERE session_id=? AND status='running'", (sid,)).fetchone():
                    raise UserError("会话正在处理中，未删除任何会话。")
            for sid in ids:
                c.execute("DELETE FROM audit_times WHERE audit_id IN (SELECT a.id FROM audits a JOIN turns t ON t.id=a.turn_id WHERE t.session_id=?)", (sid,))
                for table in ("audits", "calls", "turn_details", "turn_context", "turn_options", "external_sources"):
                    c.execute(f"DELETE FROM {table} WHERE turn_id IN (SELECT id FROM turns WHERE session_id=?)", (sid,))
                c.execute("DELETE FROM turns WHERE session_id=?", (sid,))
                c.execute("DELETE FROM sessions WHERE id=?", (sid,))
        return {"deleted": ids}

    def history(self, sid):
        self.require_session(sid)
        if not self.query("SELECT id FROM sessions WHERE id=?", (sid,)):
            raise UserError("会话不存在。")
        return [self.public_turn(t) for t in self.query(
            "SELECT * FROM turns WHERE session_id=? ORDER BY created,rowid", (sid,))]

    def turn(self, tid):
        self.require_turn(tid)
        rows = self.query("SELECT * FROM turns WHERE id=?", (tid,))
        if not rows:
            raise UserError("轮次不存在。")
        return self.public_turn(rows[0])

    def public_turn(self, t):
        result = {k: (json.loads(t[k]) if k == "citations" else t[k])
                  for k in ["id", "question", "action", "level", "status", "answer",
                            "citations", "elapsed"]}
        details = self.query("SELECT reason,closed FROM turn_details WHERE turn_id=?", (t["id"],))
        result.update(details[0] if details else {"reason": "", "closed": False})
        requests = self.query('SELECT request_id FROM turn_requests WHERE turn_id=?', (t['id'],))
        result['request_id'] = requests[0]['request_id'] if requests else None
        result["rewrites"] = max(0, self.query("SELECT count(*) n FROM audits WHERE turn_id=?", (t["id"],))[0]["n"] - 1)
        from .policy import BLOCKED, Policy
        policy = Policy(self)
        state = policy.turn_state(t)
        result['l5_eligible'] = False
        result['l5_notice'] = ''
        if t['level'] == 4 and state['max_level'] == 5 and not state['blocked']:
            try:
                self.require_l5(t)
                result['l5_eligible'] = True
            except UserError as exc:
                result['l5_notice'] = str(exc)
        result.update(max_level=state["max_level"], policy_version=state["version"],
                      policy_blocked=state["blocked"] or t["level"] > state["max_level"],
                      concepts=[], candidates=[])
        if result["policy_blocked"]:
            result.update(answer=BLOCKED, citations=[], reason="教师策略已限制此轮内容。")
        else:
            from .knowledge import Knowledge
            from .learning import mastered
            context = self.query("SELECT * FROM turn_context WHERE turn_id=?", (t["id"],))
            if context:
                ids = set(json.loads(context[0]["concepts"]))
                states = {r["concept_id"]: r for r in self.query(f"SELECT * FROM {self.mastery_table}")}
                result["concepts"] = [{"id": x["id"], "name": x["name"],
                    "probability": states[x["id"]]["probability"] if x["id"] in states else None,
                    "mastered": mastered(self, [x["id"]])}
                    for x in Knowledge(self).concepts(approved=True) if x["id"] in ids]
                result["candidates"] = [x for x in json.loads(context[0]["candidates"])
                    if self.query("SELECT 1 FROM pages WHERE id=?", (x["id"],))]
        return result

    def active_quiz(self):
        rows = self.query("SELECT q.id FROM quizzes q JOIN quiz_owners o ON o.quiz_id=q.id WHERE q.status='active' AND o.account=?", (self.account,))
        return rows[0]["id"] if rows else None

    def require_study(self):
        if self.active_quiz():
            raise UserError("小测进行中，请先完成并交卷；答疑与通用课件预览暂不可用。")

    def close_question(self, sid, tid):
        self.require_session(sid)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            last = c.execute("SELECT id,status FROM turns WHERE session_id=? ORDER BY created DESC,rowid DESC LIMIT 1", (sid,)).fetchone()
            if not last or last["id"] != tid or last["status"] != "completed":
                raise UserError("只能结束最新已完成的问题。")
            c.execute("INSERT INTO turn_details(turn_id,closed) VALUES (?,1) ON CONFLICT(turn_id) DO UPDATE SET closed=1", (tid,))
        return {"closed": True, "notice": "已记录理解自述，不作为掌握证据。"}

    def reserve_turn(self, sid, action, message, parent, force_l1=False, course_id=None, request_id=None):
        self.require_session(sid)
        from .policy import BLOCKED, Policy
        policy = Policy(self)
        if policy.assess(message)["blocked"]:
            raise UserError(BLOCKED)
        if course_id and not self.query('SELECT 1 FROM documents WHERE id=?', (course_id,)):
            raise UserError('所选课程文件不存在或已失效。')
        # Transaction + unique partial index protects even separate HTTP requests.
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM quizzes q JOIN quiz_owners o ON o.quiz_id=q.id WHERE q.status='active' AND o.account=?", (self.account,)).fetchone():
                raise UserError("小测进行中，暂不可答疑。")
            if not c.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone():
                raise UserError("会话不存在。")
            if request_id and c.execute('SELECT 1 FROM turn_requests WHERE session_id=? AND request_id=?', (sid, request_id)).fetchone():
                raise UserError('该请求已受理，请刷新本轮状态，不会重复调用模型。')
            if c.execute("SELECT id FROM turns WHERE session_id=? AND status='running'",
                         (sid,)).fetchone():
                raise UserError("本会话正在处理其他请求，请稍候。")
            last = c.execute("SELECT * FROM turns WHERE session_id=? ORDER BY created DESC,rowid DESC LIMIT 1",
                             (sid,)).fetchone()
            question, level = message, 1
            if action in {"followup", "upgrade"}:
                if not last or last["status"] != "completed":
                    raise UserError("请先完成一次有依据的提问。")
                closed = c.execute("SELECT closed FROM turn_details WHERE turn_id=?", (last["id"],)).fetchone()
                if closed and closed["closed"]:
                    raise UserError("当前问题已结束，请发起新问题。")
                state = policy.require_turn(dict(last))
                level = last["level"]
                if action == "upgrade":
                    if last["id"] != parent or level >= state["max_level"]:
                        raise UserError("只能对最新完成轮次逐级升级，且不能超过教师上限。")
                    if level == 4:
                        self.require_l5(dict(last), c)
                    question, level = last["question"], level + 1
            tid = uuid4().hex
            c.execute("INSERT INTO turns(id,session_id,created,action,question,level,parent,status) "
                      "VALUES (?,?,?,?,?,?,?,'running')",
                      (tid, sid, time.time(), action, question, level, parent))
            if action != 'ask':
                options = c.execute('SELECT selected_doc FROM turn_options WHERE turn_id=?', (last['id'],)).fetchone()
                course_id = options['selected_doc'] if options else None
            if request_id:
                c.execute('INSERT INTO turn_requests VALUES (?,?,?)', (sid, request_id, tid))
            c.execute('INSERT INTO turn_options(turn_id,force_l1,selected_doc) VALUES (?,?,?)',
                      (tid, int(force_l1 and action == 'ask'), course_id))
        return {"id": tid, "session_id": sid, "question": question, "level": level,
                "action": action}

    def require_l5(self, turn, connection=None):
        from .policy import Policy
        p = Policy(self)
        state = p.turn_state(turn)
        if state['blocked'] or state['max_level'] < 5:
            raise UserError('完整解答未获教师允许或当前问题受限。')
        def check(c):
            rows = c.execute('SELECT * FROM turns WHERE session_id=? ORDER BY created,rowid', (turn['session_id'],)).fetchall()
            group = []
            for row in rows:
                if row['action'] == 'ask':
                    group = []
                group.append(row)
                if row['id'] == turn['id']:
                    break
            levels = [r['level'] for r in group if r['status'] == 'completed']
            expected = 1
            for level in levels:
                if level == expected:
                    expected += 1
            if expected < 5:
                raise UserError('完整解答须本问题依次完成L1–L4；可发起新问题选择从L1开始。')
        if connection is not None:
            check(connection)
        else:
            with self.connect() as c:
                check(c)

    def finish(self, tid, answer, citations, status, elapsed):
        self.execute("UPDATE turns SET answer=?,citations=?,status=?,elapsed=? WHERE id=?",
                     (answer, json.dumps(citations, ensure_ascii=False), status, elapsed, tid))

    def audit(self, tid, attempt, draft, verdict):
        with self.connect() as c:
            cursor = c.execute("INSERT INTO audits(turn_id,attempt,draft,verdict) VALUES (?,?,?,?)",
                               (tid, attempt, json.dumps(draft, ensure_ascii=False),
                                json.dumps(verdict, ensure_ascii=False)))
            c.execute("INSERT INTO audit_times VALUES (?,?)", (cursor.lastrowid, time.time()))

    def call(self, tid, kind, model, elapsed, usage, outcome):
        self.execute("INSERT INTO calls(turn_id,kind,model,elapsed,usage,outcome) VALUES (?,?,?,?,?,?)",
                     (tid, kind, model, elapsed,
                      json.dumps(usage) if usage is not None else None, outcome))

    def context(self, sid, tid):
        self.require_session(sid)
        turns = self.query("SELECT * FROM turns WHERE session_id=? AND id!=? "
                           "AND status='completed' ORDER BY created DESC,rowid DESC LIMIT 4", (sid, tid))
        public = [self.public_turn(t) for t in reversed(turns)]
        return [{"question": t["question"], "answer": t["answer"]} for t in public
                if not t["policy_blocked"]]

    def citation(self, tid, pid):
        self.require_turn(tid)
        self.require_study()
        turns = self.query("SELECT * FROM turns WHERE id=?", (tid,))
        if not turns:
            raise UserError("轮次不存在。")
        t = turns[0]
        from .policy import Policy
        Policy(self).require_turn(t)
        Policy(self).require_pages([pid])
        if t["level"] < 3 or t["status"] != "completed":
            raise UserError("当前级别不允许预览课件正文。")
        if pid not in {p["id"] for p in json.loads(t["citations"])}:
            raise UserError("该页面不是本轮引用。")
        external = self.query('SELECT sources FROM external_sources WHERE turn_id=?', (tid,))
        if external:
            for source in json.loads(external[0]['sources']):
                if source['id'] == pid:
                    return source
        pages = self.query("SELECT p.text,p.notes,p.title,a.caption FROM pages p LEFT JOIN page_assets a ON a.page_id=p.id WHERE p.id=?", (pid,))
        if not pages:
            raise UserError("来源已更新或不可用，请重新提问。")
        return pages[0]
