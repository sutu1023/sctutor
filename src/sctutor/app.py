import asyncio
import json
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .analytics import overview, processing_summary
from .auth import ACCOUNTS, COOKIE, IDENTITY, TTL, Auth, Login
from .config import Settings, UserError
from .course_files import Materials, upload_files
from .knowledge import Knowledge, Review
from .learning import report
from .models import Models
from .policy import Policy, PolicyInput
from .processing import Options, Pipeline
from .quiz import Question, Quiz
from .rendering import Renderer
from .reporting import Window, csv_text, export_rows
from .retrieval import Retriever, ingest
from .store import Store
from .tutor import TurnRequest, Tutor
from .vision import enhance
from .websearch import WebSearch

STATIC = Path(__file__).parent / "static"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RenameSession(Input):
    name: str = Field(min_length=1, max_length=120)


class DeleteSessions(Input):
    ids: list[str] = Field(min_length=1, max_length=100)


class PageSelection(Input):
    page_ids: list[str] = Field(min_length=1, max_length=8)


class GenerateQuestion(Input):
    concept_id: str = Field(min_length=1, max_length=64)
    type: str = Field(default="choice", max_length=12)


class StartQuiz(Input):
    count: int = Field(default=3, ge=1, le=10)
    concept_id: str | None = Field(default=None, max_length=64)
    review_id: str | None = Field(default=None, max_length=64)


class SubmitAnswer(Input):
    answer: str = Field(min_length=1, max_length=6000)
    attempt: int = Field(ge=1, le=2)
    request_id: str = Field(min_length=8, max_length=80)


class QuestionStatus(Input):
    status: str = Field(max_length=12)


def create_app(settings=None, store=None, models=None, retriever=None):
    settings = settings or Settings.load()
    store = store or Store(settings.data_dir)
    models = models or Models(settings, store)
    tutor = Tutor(store, models, retriever or Retriever(store, models), WebSearch(settings, store))
    knowledge, quiz = Knowledge(store, models), Quiz(store, models)
    renderer = Renderer(settings.data_dir)
    materials = Materials(store, renderer)
    processing_lock = asyncio.Lock()
    auth = Auth(store)
    pipeline = Pipeline(store, models, settings, renderer, quiz, processing_lock)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        pipeline.recover()
        yield
        if pipeline.task and not pipeline.task.done():
            pipeline.task.cancel()
            with suppress(asyncio.CancelledError):
                await pipeline.task

    app = FastAPI(title="SC-Tutor", lifespan=lifespan)
    app.state.store = store
    app.state.tutor = tutor
    app.state.quiz = quiz
    app.state.pipeline = pipeline

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        # No CORS, and reject cross-origin requests even when sent to loopback.
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"message": "仅允许本地同源访问。"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"message": "拒绝跨站访问。"}, status_code=403)
        token = None
        upload_exceeded = False
        if request.url.path == '/api/admin/upload':
            try:
                if int(request.headers.get('content-length', '0')) > 101 * 1024 * 1024:
                    return JSONResponse({'message': '上传请求超过100MiB。'}, status_code=413)
            except ValueError:
                return JSONResponse({'message': '上传长度无效。'}, status_code=400)
            # Also bound chunked uploads before multipart parsing/spooling, not just Content-Length.
            receive = request._receive
            received = 0
            async def bounded_receive():
                nonlocal received, upload_exceeded
                message = await receive()
                received += len(message.get('body', b''))
                if received > 101 * 1024 * 1024:
                    upload_exceeded = True
                    raise HTTPException(413, '上传请求超过100MiB。')
                return message
            request._receive = bounded_receive
        try:
            if request.url.path.startswith('/api/') and request.url.path not in {'/api/health', '/api/auth/login'}:
                identity = auth.identity(request.cookies.get(COOKIE))
                token = IDENTITY.set(identity['id'])
                if request.url.path.startswith('/api/admin/') and identity['role'] != 'teacher':
                    raise HTTPException(403, '仅教师可使用管理功能。')
            response = await call_next(request)
            if upload_exceeded:  # Multipart parsers may translate receive errors to 400.
                response = JSONResponse({'message': '上传请求超过100MiB。'}, status_code=413)
        except HTTPException as exc:
            response = JSONResponse({'message': exc.detail}, status_code=exc.status_code)
        finally:
            if token is not None:
                IDENTITY.reset(token)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.exception_handler(UserError)
    async def safe_error(request, exc):
        return JSONResponse({"message": str(exc)}, status_code=409)

    @app.get("/api/health")
    async def health():
        configured = {}
        for kind in ("llm", "embedding"):
            try:
                settings.require(kind)
                configured[kind] = True
            except UserError:
                configured[kind] = False
        return {"status": "ok", "pages": store.query("SELECT count(*) n FROM pages")[0]["n"],
                "configured": configured}

    @app.post('/api/auth/login')
    async def login(body: Login, request: Request):
        auth.logout(request.cookies.get(COOKIE))
        token = auth.login(body.account)
        response = JSONResponse(ACCOUNTS[body.account])
        response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, samesite='strict', path='/')
        return response

    @app.get('/api/auth/me')
    async def me():
        return ACCOUNTS[store.account]

    @app.post('/api/auth/logout')
    async def logout(request: Request):
        auth.logout(request.cookies.get(COOKIE))
        response = JSONResponse({'logged_out': True})
        response.delete_cookie(COOKIE, path='/')
        return response

    @app.post("/api/sessions", status_code=201)
    async def new_session():
        return store.new_session()

    @app.get("/api/sessions")
    async def sessions(search: str = Query(default='', max_length=120)):
        return store.sessions(search)

    @app.patch('/api/sessions/{sid}')
    async def rename_session(sid: str, body: RenameSession):
        return store.rename_session(sid, body.name)

    @app.delete('/api/sessions/{sid}')
    async def delete_session(sid: str):
        return store.delete_sessions([sid])

    @app.post('/api/sessions/delete')
    async def delete_sessions(body: DeleteSessions):
        return store.delete_sessions(list(dict.fromkeys(body.ids)))

    @app.get("/api/sessions/{sid}")
    async def history(sid: str):
        store.require_study()
        return {"id": sid, "turns": store.history(sid)}

    @app.get("/api/turns/{tid}/citations/{pid}")
    async def citation(tid: str, pid: str):
        return store.citation(tid, pid)

    @app.post("/api/sessions/{sid}/turns")
    async def turns(sid: str, body: TurnRequest):
        store.require_session(sid)
        store.require_study()
        body.validate_action()
        settings.require("llm")
        settings.require("embedding")
        turn = store.reserve_turn(sid, body.action, body.message, body.parent_turn_id, body.force_l1, body.course_id, body.request_id)
        queue = asyncio.Queue()
        task = asyncio.create_task(tutor.run(turn, queue.put))

        async def events():
            try:
                while True:
                    event = await queue.get()
                    yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                    if event["event"] == "done":
                        break
            finally:
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.post("/api/sessions/{sid}/close/{tid}")
    async def close_question(sid: str, tid: str):
        store.require_study()
        return store.close_question(sid, tid)

    @app.get("/api/turns/{tid}/citations/{pid}/image")
    async def citation_image(tid: str, pid: str):
        store.citation(tid, pid)
        return FileResponse(renderer.asset(store, pid), media_type="image/png")

    @app.get("/api/report")
    async def learning_report(start: float | None = None, end: float | None = None):
        store.require_study()
        w = Window.make(start, end)
        return report(store, knowledge, now=w.end, start=w.start)

    def export_response(data, kind):
        return Response(csv_text(export_rows(data, kind)), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="sctutor-{kind}.csv"'})

    @app.get('/api/report/export')
    async def export_report(start: float | None = None, end: float | None = None):
        return export_response(await learning_report(start, end), 'report')

    @app.get('/api/courses')
    async def courses():
        return materials.catalog()

    @app.get('/api/materials/{anchor}')
    async def material_page(anchor: str, origin: str = Query(max_length=180), page: int | None = Query(default=None, ge=1), snippet: str = Query(default='', max_length=300)):
        return materials.page(anchor, origin, page, snippet)

    @app.get('/api/materials/{anchor}/image')
    async def material_image(anchor: str, origin: str = Query(max_length=180), page: int = Query(ge=1)):
        return FileResponse(materials.image(anchor, origin, page), media_type='image/png')

    @app.get('/api/materials/{anchor}/file')
    async def material_file(anchor: str, origin: str = Query(max_length=180)):
        return FileResponse(materials.pdf(anchor, origin), media_type='application/pdf', filename='course-slides.pdf')

    @app.get("/api/knowledge/{cid}/pages/{pid}")
    async def study_page(cid: str, pid: str):
        store.require_study()
        if not any(x["id"] == cid and pid in x["pages"] for x in knowledge.concepts(approved=True)):
            raise UserError("该页不属于审核通过的现行知识点。")
        Policy(store).require_pages([pid], concept_ids=[cid])
        data = knowledge.sources([pid])[0]
        assets = store.query("SELECT caption FROM page_assets WHERE page_id=?", (pid,))
        return {**data, "caption": assets[0]["caption"] if assets else ""}

    @app.get("/api/knowledge/{cid}/pages/{pid}/image")
    async def study_image(cid: str, pid: str):
        await study_page(cid, pid)
        return FileResponse(renderer.asset(store, pid), media_type="image/png")

    @app.get("/api/quizzes/active")
    async def active_quiz():
        qid = store.active_quiz()
        return quiz.view(qid) if qid else None

    @app.post("/api/quizzes", status_code=201)
    async def start_quiz(body: StartQuiz):
        return await quiz.start(body.count, body.concept_id, body.review_id)

    @app.get("/api/quizzes/{qid}")
    async def get_quiz(qid: str):
        return quiz.view(qid)

    @app.post("/api/quiz-items/{iid}/answers")
    async def submit_answer(iid: str, body: SubmitAnswer):
        return await quiz.submit(iid, body.answer, body.attempt, body.request_id)

    @app.post("/api/quizzes/{qid}/finish")
    async def finish_quiz(qid: str):
        return quiz.finish(qid)

    @app.post("/api/quizzes/{qid}/cancel")
    async def cancel_quiz(qid: str):
        return quiz.cancel(qid)

    @app.get("/api/quiz-items/{iid}/citations/{pid}")
    async def quiz_citation(iid: str, pid: str):
        return quiz.citation(iid, pid)

    @app.get("/api/quiz-items/{iid}/citations/{pid}/image")
    async def quiz_image(iid: str, pid: str):
        quiz.citation(iid, pid)
        return FileResponse(renderer.asset(store, pid), media_type="image/png")

    @app.get("/api/reviews")
    async def review_tasks():
        return quiz.reviews()

    @app.get('/api/policy')
    async def capabilities():
        p = Policy(store).get()
        return {"max_level": p["max_level"], "homework_level": min(p["max_level"], p["homework_level"]),
                "version": p["version"], "l5": p['allow_full_answer'] and p['max_level'] == 5,
                "web_search": p['web_search']} 

    @app.get('/api/admin/policy')
    async def get_policy():
        return Policy(store).get()

    @app.put('/api/admin/policy')
    async def save_policy(body: PolicyInput):
        return Policy(store).save(body, settings)

    @app.get('/api/admin/overview')
    async def teaching_overview(start: float | None = None, end: float | None = None):
        w = Window.make(start, end)
        return overview(store, now=w.end, start=w.start)

    @app.get('/api/admin/overview/export')
    async def export_overview(start: float | None = None, end: float | None = None):
        return export_response(await teaching_overview(start, end), 'overview')

    @app.post('/api/admin/upload')
    async def upload_course(files: list[UploadFile] = File()):
        if processing_lock.locked():
            raise UserError('课件正在处理中，请稍候。')
        async with processing_lock:
            results = await upload_files(files, settings.course_dir)
            import time
            store.execute('INSERT INTO processing(created,kind,status,detail) VALUES (?,?,?,?)',
                          (time.time(), 'upload', 'error' if any(x['status'] == 'error' for x in results) else 'completed', json.dumps(results, ensure_ascii=False)))
            return {'files': results, 'notice': '上传未触发云端处理，请显式同步课程目录。'}

    # Every administrator route is protected by server-side teacher authorization.
    @app.get("/api/admin/knowledge")
    async def admin_knowledge():
        return {"concepts": knowledge.concepts(), "aliases": knowledge.aliases(), "relations": knowledge.relations()}

    @app.get("/api/admin/pages")
    async def admin_pages():
        return store.query("SELECT p.*,d.path file,a.caption FROM pages p JOIN documents d ON p.doc_id=d.id LEFT JOIN page_assets a ON a.page_id=p.id ORDER BY d.path,p.page")

    @app.post("/api/admin/extract")
    async def extract_knowledge(body: PageSelection):
        return await knowledge.extract(body.page_ids)

    @app.post("/api/admin/{kind}/{ident}/review")
    async def review_knowledge(kind: str, ident: str, body: Review):
        return knowledge.review(kind, ident, body)

    @app.get("/api/admin/questions")
    async def admin_questions():
        return quiz.questions()

    @app.post("/api/admin/questions/generate")
    async def generate_question(body: GenerateQuestion):
        return await quiz.generate(body.concept_id, body.type)

    @app.post("/api/admin/questions")
    async def add_question(body: Question):
        return quiz.add_question(body.model_dump())

    @app.put("/api/admin/questions/{qid}")
    async def edit_question(qid: str, body: Question):
        return quiz.add_question(body.model_dump(), previous=qid)

    @app.post("/api/admin/questions/{qid}/status")
    async def review_question(qid: str, body: QuestionStatus):
        return quiz.review_question(qid, body.status)

    @app.get("/api/admin/processing")
    async def processing():
        return {"summary": processing_summary(store), "running": processing_lock.locked(), "records": store.query("SELECT * FROM processing ORDER BY id DESC LIMIT 20"),
                "jobs": pipeline.jobs(), "reviews": store.query("SELECT * FROM auto_reviews ORDER BY id DESC LIMIT 100")}

    @app.post('/api/admin/pipeline', status_code=202)
    async def process_course(body: Options):
        return await pipeline.start(body)

    @app.post("/api/admin/ingest")
    async def ingest_course():
        if processing_lock.locked():
            raise UserError("课件正在处理中，请稍候。")
        async with processing_lock:
            return await ingest(store, models, settings.course_dir)

    @app.post("/api/admin/enhance")
    async def enhance_course():
        if not settings.enable_enhancement:
            raise UserError("原页/视觉增强未启用；仅macOS PowerPoint可用，请先核对交接指南。")
        if processing_lock.locked():
            raise UserError("课件正在处理中，请稍候。")
        async with processing_lock:
            return await enhance(store, models, settings.course_dir, renderer)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    return app
