"""Real seven-day metrics; administrator output contains no raw learner text."""
import json
import time
from collections import Counter

from .knowledge import Knowledge
from .policy import Policy

WEEK = 7 * 86400


def problems(store, account, now, start=None):
    # Read preceding asks as well, so a followup cannot become a spurious new problem.
    rows = store.query("""SELECT t.*,COALESCE(d.closed,0) closed,tc.concepts
        FROM turns t JOIN session_owners o ON o.session_id=t.session_id
        LEFT JOIN turn_details d ON d.turn_id=t.id
        LEFT JOIN turn_context tc ON tc.turn_id=t.id
        WHERE o.account=? AND t.created<=? ORDER BY t.session_id,t.created,t.rowid""", (account, now))
    groups, current = [], {}
    for t in rows:
        sid = t["session_id"]
        if t["action"] == "ask":
            group = {"created": t["created"], "turns": [], "concepts": set()}
            current[sid] = group
            groups.append(group)
        group = current.get(sid)
        if group is not None:
            group["turns"].append(t)
            group["concepts"].update(json.loads(t["concepts"] or "[]"))
    return [g for g in groups if (now - WEEK if start is None else start) <= g["created"] < now]


def category(verdict):
    if verdict.get("reason") == "检查服务调用失败，草稿未发送。":
        return "检查失败", None
    if not all(type(verdict.get(k)) is bool for k in ("passed", "level_ok", "supported")):
        return "格式无效", False
    if not verdict["level_ok"]:
        return "级别越界", False
    if not verdict["supported"]:
        return "证据不足", False
    return ("检查通过", True) if verdict["passed"] else ("检查退回", False)


def overview(store, now=None, start=None):
    now = time.time() if now is None else now
    start = now-WEEK if start is None else start
    groups = problems(store, "student", now, start)
    ended, l4, unresolved, failed = [], 0, Counter(), 0
    known = {x["id"]: x for x in Knowledge(store).concepts(approved=True)}
    for group in groups:
        completed = [t for t in group["turns"] if t["status"] == "completed"]
        closed = any(t["closed"] for t in group["turns"])
        if closed and completed:
            ended.append(completed[-1]["level"])
        if any(t["level"] >= 4 for t in completed):
            l4 += 1
        if not closed and any(t["level"] >= 3 for t in completed):
            unresolved.update(group["concepts"] & known.keys())
        failed += not completed
    audits = store.query("""SELECT a.verdict,t.level,COALESCE(at.created,t.created) checked_at
        FROM audits a JOIN turns t ON t.id=a.turn_id
        JOIN session_owners o ON o.session_id=t.session_id
        LEFT JOIN audit_times at ON at.audit_id=a.id
        WHERE o.account='student' AND COALESCE(at.created,t.created)>=? AND COALESCE(at.created,t.created)<?
        ORDER BY checked_at DESC,a.id DESC""", (start, now))
    checks, rejected, errors, recent = 0, 0, 0, []
    for row in audits:
        reason, passed = category(json.loads(row["verdict"]))
        errors += passed is None
        checks += passed is not None
        rejected += passed is False
        if len(recent) < 20:
            recent.append({"created": row["checked_at"], "level": row["level"],
                           "passed": passed, "category": reason})
    count = len(groups)
    return {"start": start, "end": now,
            "notice": "student学生演示账号匿名聚合，非真实班级；所选期间统计，旧检查时间缺失时按轮次时间统计。", 
            "questions": count, "ended": len(ended), "unfinished": count-len(ended),
            "failed_questions": failed, "average_end_level": sum(ended)/len(ended) if ended else None,
            "l4_questions": l4, "l4_rate": l4/count if count else None,
            "checks": checks, "rejected": rejected, "check_errors": errors,
            "rejection_rate": rejected/checks if checks else None,
            "unattributed": sum(not g["concepts"] for g in groups),
            "difficulties": [{"id": cid, "name": known[cid]["name"], "count": n,
                "citations": [{k: p[k] for k in ("id", "file", "page")}
                    for p in Knowledge(store).sources(known[cid]["pages"])]}
                for cid, n in sorted(unresolved.items(), key=lambda x: (-x[1], known[x[0]]["name"]))],
            "recent_checks": recent}


def report_summary(store, concepts, now, start=None):
    start = now-WEEK if start is None else start
    valid = {x["id"] for x in concepts}
    # Graph activity is any actual in-window turn, not just newly created problems.
    # The overview's independent-question denominator intentionally uses a different rule.
    active = store.query("""SELECT tc.concepts FROM turn_context tc
        JOIN turns t ON t.id=tc.turn_id JOIN session_owners o ON o.session_id=t.session_id
        WHERE o.account=? AND t.created>=? AND t.created<?""", (store.account, start, now))
    involved = {cid for row in active for cid in json.loads(row["concepts"])} & valid
    observations = store.query("""SELECT ob.correct FROM observations ob
        JOIN quiz_items i ON i.id=ob.item_id JOIN quiz_owners o ON o.quiz_id=i.quiz_id
        WHERE o.account=? AND ob.created>=? AND ob.created<?""", (store.account, start, now))
    # One item may cover multiple concepts. Count independent first attempts, not labels.
    attempts = store.query("""SELECT i.first_correct FROM quiz_items i JOIN quizzes q ON q.id=i.quiz_id
        JOIN quiz_owners o ON o.quiz_id=q.id
        WHERE o.account=? AND q.status='completed' AND q.finished>=? AND q.finished<?""", (store.account, start, now))
    return {"start": start, "end": now, "mastered": sum(x["mastered"] for x in concepts),
            "total": len(concepts), "involved": sorted(involved),
            "first_correct": sum(bool(x["first_correct"]) for x in attempts), "attempted": len(attempts),
            "accuracy": sum(bool(x["first_correct"]) for x in attempts)/len(attempts) if attempts else None,
            "observations": len(observations), "review_concepts": sum(not x["mastered"] for x in concepts)}


def processing_summary(store):
    files = store.query("""SELECT d.id,d.path file,COUNT(p.id) pages
        FROM documents d LEFT JOIN pages p ON p.doc_id=d.id GROUP BY d.id ORDER BY d.path""")
    short = Counter(row["doc_id"] for row in store.query("SELECT doc_id,text,images FROM pages")
                    if row["images"] > 0 and len("".join(row["text"].split())) < 30)
    for item in files:
        item["caption_candidates"] = short[item.pop("id")]
    # Ingest records added in this iteration provide exact per-file change kinds.
    recent = store.query("SELECT * FROM processing WHERE kind='ingest' ORDER BY id DESC LIMIT 1")
    latest = None
    if recent:
        latest = {"created": recent[0]["created"], "status": recent[0]["status"],
                  "files": json.loads(recent[0]["detail"])}
    k = Knowledge(store)
    return {"files": files, "pages": sum(x["pages"] for x in files),
            "caption_candidates": sum(x["caption_candidates"] or 0 for x in files),
            "latest_ingest": latest,
            "pending": {name: sum(x["status"] == "pending" for x in getattr(k, name)())
                        for name in ("concepts", "aliases", "relations")},
            "policy_version": Policy(store).get()["version"]}
