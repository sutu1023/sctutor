import asyncio
import json

import pytest
from conftest import FakeModels

from sctutor.config import UserError
from sctutor.knowledge import Knowledge, Review
from sctutor.learning import mastered, observe, report, update_probability
from sctutor.quiz import Quiz
from sctutor.tutor import Tutor


@pytest.fixture
async def learning_data(store, corpus):
    _, model = corpus
    k = Knowledge(store, model)
    pages = store.query("SELECT id FROM pages WHERE indexed=1 ORDER BY rowid")
    k.add_extraction({"concepts": [
        {"name": "Copilot", "page_ids": [pages[0]["id"]], "aliases": ["微软补全插件"]},
        {"name": "Python", "page_ids": [pages[1]["id"]], "aliases": []}],
        "relations": [{"prerequisite": "Python", "target": "Copilot", "page_ids": [pages[1]["id"]]}]}, [p["id"] for p in pages])
    for x in k.concepts():
        k.review("concepts", x["id"], Review(status="approved"))
    concepts = {x["name"]: x for x in k.concepts(approved=True)}
    return k, concepts, model


def question_data(concept, kind="choice"):
    return {"type": kind, "prompt": "哪一个是课程中的编程助手？", "options": ["Copilot", "其他"] if kind == "choice" else [], "answer": "Copilot", "rubric": "识别课件中的编程助手", "hint": "思考编程辅助工具的概念。", "concept_ids": [concept["id"]], "page_ids": concept["pages"]}


async def test_knowledge_review_alias_rejection_and_invalid_sources(store, learning_data):
    k, concepts, model = learning_data
    assert k.expand("微软补全插件") == "微软补全插件"
    alias = k.aliases()[0]
    k.review("aliases", alias["id"], Review(status="approved"))
    assert "Copilot" in k.expand("微软补全插件")
    k.review("aliases", alias["id"], Review(status="rejected"))
    k.add_extraction({"concepts": [{"name": "Copilot", "page_ids": concepts["Copilot"]["pages"], "aliases": [alias["name"]]}]}, concepts["Copilot"]["pages"])
    assert k.aliases()[0]["status"] == "rejected"
    with pytest.raises(UserError):
        k.add_extraction({"concepts": [{"name": "伪造", "page_ids": ["forged"]}]}, ["forged"])
    store.execute("DELETE FROM documents")
    assert not k.concepts(approved=True)
    assert k.expand("微软补全插件") == "微软补全插件"
    with pytest.raises(UserError):
        k.review("concepts", concepts["Copilot"]["id"], Review(status="approved"))


async def test_relation_cycle_and_prerequisite_report(store, learning_data):
    k, cs, _ = learning_data
    r = k.relations()[0]
    k.review("relations", r["id"], Review(status="approved"))
    store.execute("INSERT INTO relations VALUES ('cycle',?,?,?,'pending')", (cs["Copilot"]["id"], cs["Python"]["id"], json.dumps(cs["Python"]["pages"])))
    with pytest.raises(UserError, match="环"):
        k.review("relations", "cycle", Review(status="approved"))
    data = report(store, k)
    copilot = next(x for x in data["concepts"] if x["name"] == "Copilot")
    assert copilot["probability"] is None
    assert copilot["prerequisites"][0]["name"] == "Python"
    assert "前置" in copilot["reason"]


async def test_bkt_math_idempotency_and_threshold(store, learning_data):
    _, cs, _ = learning_data
    cid = cs["Copilot"]["id"]
    assert update_probability(.2, True) == pytest.approx(.5764705882352942)
    assert update_probability(.2, False) == pytest.approx(.1272727272727273)
    with store.connect() as c:
        observe(c, cid, "one", True, 1)
        observe(c, cid, "one", True, 1)
    assert store.query("SELECT count FROM mastery")[0]["count"] == 1
    assert not mastered(store, [cid])
    with store.connect() as c:
        observe(c, cid, "two", True, 2)
    assert mastered(store, [cid])
    assert not mastered(store, [])
    assert not mastered(store, [cid, "unknown"])


async def test_adaptive_definition_mastery_and_closed_question(store, corpus, learning_data):
    k, cs, _ = learning_data
    class Classified(FakeModels):
        classification = {"type": "definition", "concept_ids": []}
        async def chat(self, system, payload, tid=None, purpose="generate"):
            if purpose == "classify":
                return self.classification
            return await super().chat(system, payload, tid, purpose)
    model = Classified()
    from sctutor.retrieval import Retriever
    tutor = Tutor(store, model, Retriever(store, model))
    sid = store.new_session()["id"]
    async def emit(_):
        pass
    async def ask():
        turn = store.reserve_turn(sid, "ask", "Copilot是什么？", None)
        await tutor.run(turn, emit)
        return store.turn(turn["id"])
    t = await ask()
    assert t["level"] == 2 and "定义" in t["reason"]
    store.close_question(sid, t["id"])
    with pytest.raises(UserError, match="结束"):
        store.reserve_turn(sid, "followup", "继续", None)
    assert not store.query("SELECT * FROM observations")
    model.classification = {"type": "concept", "concept_ids": [cs["Copilot"]["id"]]}
    assert (await ask())["level"] == 1
    with store.connect() as c:
        observe(c, cs["Copilot"]["id"], "a", True, 1)
        observe(c, cs["Copilot"]["id"], "b", True, 2)
    assert (await ask())["level"] == 2
    model.classification = {"type": "definition", "concept_ids": ["forged"]}
    assert (await ask())["level"] == 1


async def test_quiz_first_failure_hint_submission_and_scheduling(store, learning_data):
    k, cs, model = learning_data
    now = [1000.0]
    quiz = Quiz(store, model, lambda: now[0])
    qid = quiz.add_question(question_data(cs["Copilot"]))["id"]
    quiz.review_question(qid, "approved")
    q = await quiz.start(1)
    item = q["items"][0]
    public = json.dumps(q)
    for forbidden in ['"answer":', '"rubric":', '"page_ids":', '"citations":']:
        assert forbidden not in public
    with pytest.raises(UserError):
        store.require_study()
    with pytest.raises(UserError):
        quiz.citation(item["id"], cs["Copilot"]["pages"][0])
    with pytest.raises(UserError):
        await quiz.start(1)
    q = await quiz.submit(item["id"], "其他", 1, "request-one")
    assert not q["items"][0]["done"] and q["items"][0]["hint"]
    assert "answer" not in q["items"][0]
    assert (await quiz.submit(item["id"], "其他", 1, "request-one"))["items"][0]["attempts"] == 1
    with pytest.raises(UserError):
        await quiz.submit(item["id"], "Copilot", 1, "request-one")
    q = await quiz.submit(item["id"], "Copilot", 2, "request-two")
    assert q["items"][0]["done"] and q["items"][0]["answer"] == "Copilot"
    assert not q["items"][0]["first_correct"]
    quiz.finish(q["id"])
    quiz.finish(q["id"])
    state = store.query("SELECT * FROM mastery")[0]
    assert state["count"] == 1 and state["successes"] == 0
    assert sorted(r["interval"] for r in quiz.reviews()) == [1, 3, 7]
    assert not store.active_quiz()
    task = quiz.reviews()[0]
    with pytest.raises(UserError):
        await quiz.start(1, review_id=task["id"])
    now[0] += 86401
    review = await quiz.start(1, review_id=task["id"])
    await quiz.submit(review["items"][0]["id"], "Copilot", 1, "review-one")
    quiz.finish(review["id"])
    assert len(quiz.reviews()) == 3
    assert sum(x["status"] == "completed" for x in quiz.reviews()) == 1
    assert store.query("SELECT count FROM mastery")[0]["count"] == 2


async def test_quiz_correct_weekly_and_stale_question(store, learning_data):
    k, cs, model = learning_data
    quiz = Quiz(store, model)
    raw = question_data(cs["Copilot"], "blank")
    qid = quiz.add_question(raw)["id"]
    quiz.review_question(qid, "approved")
    started = await quiz.start(1)
    await quiz.submit(started["items"][0]["id"], " Ｃｏｐｉｌｏｔ ", 1, "correct-one")
    quiz.finish(started["id"])
    assert [r["interval"] for r in quiz.reviews()] == [7]
    updated = quiz.add_question({**raw, "answer": "新答案"}, previous=qid)
    assert updated["status"] == "pending" and updated["version"] == 2
    assert not quiz.reviews()[0]["available"]
    quiz.review_question(updated["id"], "approved")
    q = await quiz.start(1)
    k.review("concepts", cs["Copilot"]["id"], Review(status="approved", restricted=True))
    assert not quiz.view(q["id"])["items"][0]["available"]
    with pytest.raises(UserError):
        await quiz.submit(q["items"][0]["id"], "anything", 1, "stale-one")
    quiz.cancel(q["id"])


async def test_subjective_grade_failure_not_consumed_and_checked_hint(store, learning_data):
    k, cs, _ = learning_data
    class Grader(FakeModels):
        valid = False
        async def chat(self, system, payload, tid=None, purpose="generate"):
            if purpose == "quiz-grade":
                return {"correct": True, "score": .9, "reason": "matches rubric"} if self.valid else {"correct": "true"}
            return await super().chat(system, payload, tid, purpose)
    model = Grader()
    quiz = Quiz(store, model)
    qid = quiz.add_question(question_data(cs["Copilot"], "code"))["id"]
    quiz.review_question(qid, "approved")
    q = await quiz.start(1)
    with pytest.raises(UserError, match="未扣除"):
        await quiz.submit(q["items"][0]["id"], "print('safe, not executed')", 1, "grade-one")
    assert quiz.view(q["id"])["items"][0]["attempts"] == 0
    model.valid = True
    q = await quiz.submit(q["items"][0]["id"], "student code", 1, "grade-one")
    assert "待人工抽查" in q["items"][0]["feedback"]


async def test_concurrent_submits_are_idempotent(store, learning_data):
    _, cs, model = learning_data
    quiz = Quiz(store, model)
    qid = quiz.add_question(question_data(cs["Copilot"]))["id"]
    quiz.review_question(qid, "approved")
    q = await quiz.start(1)
    iid = q["items"][0]["id"]
    results = await asyncio.gather(quiz.submit(iid, "Copilot", 1, "same-request"), quiz.submit(iid, "Copilot", 1, "same-request"))
    assert all(r["items"][0]["attempts"] == 1 for r in results)
    assert len(store.query("SELECT * FROM attempts")) == 1


async def test_rejected_or_failed_hint_never_escapes(store, learning_data):
    _, cs, _ = learning_data
    class RejectHint(FakeModels):
        fail_check = False
        async def chat(self, system, payload, tid=None, purpose="generate"):
            if purpose == "quiz-hint":
                return {"text": "SECRET_UNCHECKED"}
            if purpose == "quiz-hint-check":
                if self.fail_check:
                    raise UserError("服务失败")
                return {"passed": False, "level_ok": False, "supported": True, "reason": "泄露答案"}
            return await super().chat(system, payload, tid, purpose)
    model = RejectHint()
    quiz = Quiz(store, model)
    qid = quiz.add_question(question_data(cs["Copilot"]))["id"]
    quiz.review_question(qid, "approved")
    for fail in [False, True]:
        model.fail_check = fail
        q = await quiz.start(1)
        q = await quiz.submit(q["items"][0]["id"], "其他", 1, "hint-failure-" + str(fail))
        assert "SECRET_UNCHECKED" not in json.dumps(q)
        assert q["items"][0]["attempts"] == 1 and not q["items"][0]["done"]
        quiz.cancel(q["id"])
