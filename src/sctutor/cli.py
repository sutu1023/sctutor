import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import Settings, UserError
from .knowledge import Knowledge
from .models import Models
from .pptx import parse_pptx
from .retrieval import Retriever, ingest
from .store import Store
from .tutor import Tutor
from .vision import enhance


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


async def run(args, settings):
    if args.command == "parse":
        rows = []
        if not settings.course_dir.is_dir():
            raise UserError("课件目录不存在。")
        for f in sorted(settings.course_dir.rglob("*.pptx")):
            try:
                slides = parse_pptx(f)
                rows.append({"file": f.name, "pages": len(slides),
                             "unindexed_pages": sum(not s.content for s in slides)})
            except Exception:
                rows.append({"file": f.name, "error": "无法解析"})
        output({"files": rows, "total_pages": sum(r.get("pages", 0) for r in rows)})
        return int(any("error" in r for r in rows))
    if args.command == "doctor":
        import platform

        from .providers import provider_for
        checks = {}
        for kind in ("llm", "embedding"):
            try:
                settings.require(kind)
                provider_for(getattr(settings, f"{kind}_provider"))
                checks[kind] = "配置可用；连接及模型能力尚未验证"
            except UserError as exc:
                checks[kind] = str(exc)
        checks["course"] = "目录存在" if settings.course_dir.is_dir() else "尚无课件；请自行准备PPTX并配置COURSE_DIR或在教师页上传"
        checks["enhancement"] = ("未启用（文本模式）" if not settings.enable_enhancement else
                                 "需本机PowerPoint及自动化权限；未执行导出" if platform.system() == "Darwin" else
                                 "阻塞：原页增强仅支持macOS PowerPoint")
        output({"network_calls": 0, "checks": checks, "next": "填写.env后显式运行smoke可验证连接（可能收费）"})
        return 0
    store = Store(settings.data_dir)
    models = Models(settings, store)
    if args.command == "ingest":
        settings.require("embedding")
        report = await ingest(store, models, settings.course_dir)
        output(report)
        return int(any(r["status"] == "error" for r in report))
    if args.command == "enhance":
        if not settings.enable_enhancement:
            raise UserError("请先核对macOS PowerPoint前提并显式设置ENABLE_ENHANCEMENT=true。")
        settings.require("llm")
        settings.require("embedding")
        report = await enhance(store, models, settings.course_dir)
        output(report)
        return int(not report or any(r["status"] != "enhanced" for r in report))
    if args.command == "extract":
        output(await Knowledge(store, models).extract(args.pages))
        return 0
    if args.command == "search":
        results = await Retriever(store, models).search(args.question)
        output([{k: v for k, v in r.items() if k != "text"} for r in results])
        return 0
    if args.command == "smoke":
        status = {"llm": "not-run", "embedding": "not-run", "dialogue": "not-run"}
        for kind in ("llm", "embedding"):
            try:
                if kind == "llm":
                    result = await models.chat('只输出JSON：{"ok":true}', {"test": "connection"},
                                               purpose="smoke")
                    if result.get("ok") is not True:
                        raise UserError("生成服务未返回预期JSON。")
                else:
                    await models.embed(["课程课件检索连接测试"])
                status[kind] = "passed"
            except UserError as e:
                status[kind] = "blocked: " + str(e)
        if args.question and all(status[k] == "passed" for k in ("llm", "embedding")):
            sid = store.new_session()["id"]
            turn = store.reserve_turn(sid, "ask", args.question, None)
            async def collect(event):
                pass  # Never print draft/provider bodies; final record remains local.
            await Tutor(store, models, Retriever(store, models)).run(turn, collect)
            last = store.history(sid)[-1]
            status["dialogue"] = "passed" if last["status"] == "completed" else "blocked: " + last["answer"]
            status["session_id"] = sid
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "smoke.json").write_text(json.dumps(status, ensure_ascii=False, indent=2))
        output(status)
        needed = ("llm", "embedding", "dialogue") if args.question else ("llm", "embedding")
        return int(any(status[k] != "passed" for k in needed))
    if args.command == "evaluate":
        questions = json.loads(args.questions.read_text())
        results = []
        for item in questions:
            try:
                found = await Retriever(store, models).search(item["question"])
                hit = any(r["file"] == p["file"] and r["page"] in p["pages"]
                          for r in found for p in item["expected"])
                results.append({**item, "hit_at_5": hit,
                                "found": [{k: r[k] for k in ("file", "page")} for r in found]})
            except UserError as e:
                results.append({**item, "blocked": str(e)})
        completed = [r for r in results if "hit_at_5" in r]
        report = {"total": len(results), "completed": len(completed),
                  "hit_at_5": sum(r["hit_at_5"] for r in completed) / len(completed) if completed else None,
                  "results": results}
        (settings.data_dir / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        output(report)
        return int(len(completed) != len(results))
    return 0


def main():
    parser = argparse.ArgumentParser(description="SC-Tutor 本地引导辅导")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("parse", "ingest", "serve", "enhance", "doctor"):
        sub.add_parser(name)
    sub.add_parser("extract").add_argument("--pages", nargs="+", required=True, help="1至8个已入库页ID；生成待审知识候选")
    sub.add_parser("search").add_argument("question")
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--question", help="同时验证已入库真实课件的完整对话，会产生云端调用费用")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--questions", type=Path, required=True, help="自备JSON评测集路径；示例见交接验证指南")
    args = parser.parse_args()
    try:
        settings = Settings.load()
        if args.command == "serve":
            import uvicorn

            from .app import create_app
            uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                        workers=1, log_level="warning", access_log=False)
        else:
            sys.exit(asyncio.run(run(args, settings)))
    except UserError as e:
        output({"status": "blocked", "message": str(e)})
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        output({"status": "error", "message": "处理失败，请检查输入文件、配置及本地数据目录。"})
        sys.exit(1)


if __name__ == "__main__":
    main()
