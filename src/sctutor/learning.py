"""Account-local BKT observations committed with quiz submission."""
import time

PARAMETERS = {"initial": 0.2, "guess": 0.2, "slip": 0.1, "learn": 0.1, "version": "bkt-v1"}


def update_probability(p, correct):
    likelihood = (1 - PARAMETERS["slip"]) if correct else PARAMETERS["slip"]
    unlearned = PARAMETERS["guess"] if correct else 1 - PARAMETERS["guess"]
    posterior = p * likelihood / (p * likelihood + (1 - p) * unlearned)
    return min(1.0, max(0.0, posterior + (1 - posterior) * PARAMETERS["learn"]))


def observe(c, concept_id, item_id, correct, now, table="mastery"):
    if table not in {"mastery", "teacher_mastery"}:
        raise ValueError("invalid mastery table")
    if c.execute("SELECT 1 FROM observations WHERE item_id=? AND concept_id=?",
                 (item_id, concept_id)).fetchone():
        return
    row = c.execute(f"SELECT * FROM {table} WHERE concept_id=?", (concept_id,)).fetchone()
    p, count, successes = (row["probability"], row["count"], row["successes"]) if row else (0.2, 0, 0)
    c.execute("INSERT INTO observations VALUES (?,?,?,?,?,?)",
              (item_id, concept_id, int(correct), now, PARAMETERS["version"], p))
    c.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?)",
              (concept_id, update_probability(p, correct), count + 1,
               successes + int(correct), now))


def mastered(store, ids):
    if not ids:
        return False
    for cid in set(ids):
        rows = store.query(f"SELECT * FROM {store.mastery_table} WHERE concept_id=?", (cid,))
        if not rows or rows[0]["probability"] < 0.8 or rows[0]["successes"] < 2:
            return False
    return True


def report(store, knowledge, now=None, start=None):
    now = time.time() if now is None else now
    from .analytics import report_summary
    from .policy import Policy
    policy = Policy(store)
    concepts = [x for x in knowledge.concepts(approved=True)
                if not policy.assess(concept_ids=[x["id"]], page_ids=x["pages"])["blocked"]]
    states = {r["concept_id"]: r for r in store.query(f"SELECT * FROM {store.mastery_table}")}
    for concept in concepts:
        state = states.get(concept["id"])
        concept.update(probability=state["probability"] if state else None,
                       observations=state["count"] if state else 0,
                       last_practiced=state["updated"] if state else None,
                       mastered=mastered(store, [concept["id"]]))
    concepts.sort(key=lambda x: (x["probability"] if x["probability"] is not None else -1, x["name"]))
    by_id = {x["id"]: x for x in concepts}
    relations = [r for r in knowledge.relations(approved=True)
                 if {r["prerequisite"], r["target"]} <= by_id.keys()]
    for x in concepts:
        prereqs = [by_id[r["prerequisite"]] for r in relations
                   if r["target"] == x["id"] and r["prerequisite"] in by_id
                   and not by_id[r["prerequisite"]]["mastered"]]
        x["prerequisites"] = [{"id": p["id"], "name": p["name"]} for p in prereqs]
        x["reason"] = ("建议先复习前置知识：" + "、".join(p["name"] for p in prereqs)
                       if prereqs else "尚未评估，建议先小测" if x["probability"] is None
                       else "按本机练习记录估计，优先复习薄弱知识点")
    summary = report_summary(store, concepts, now, start)
    for x in concepts:
        x["asked_this_week"] = x["id"] in summary["involved"]
    return {"concepts": concepts, "relations": relations, "summary": summary,
            "due": store.query("""SELECT r.* FROM reviews r JOIN quiz_items i ON i.id=r.origin_item
                JOIN quiz_owners o ON o.quiz_id=i.quiz_id
                WHERE o.account=? AND r.status='pending' AND r.due<=? ORDER BY r.due,r.id""", (store.account, now)),
            "parameters": PARAMETERS,
            "notice": "仅本机学习记录；掌握与复习建议为当前快照，提问/正确率为所选期间。概率不是可靠教育诊断，模型评分需人工抽查。"}
