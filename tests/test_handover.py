import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from sctutor.app import create_app
from sctutor.config import Settings, UserError
from sctutor.models import Models

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("snapshot_export", ROOT / "scripts/export_handover.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def test_clean_start_no_private_requirements(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", course_dir=tmp_path / "course")
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/health").json()["pages"] == 0
        assert client.get("/").status_code == 200
        assert client.post("/api/auth/login", json={"account": "student"}).status_code == 200
        assert client.get("/api/admin/pages").status_code == 403
        assert client.get("/api/sessions", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
        assert client.post("/api/auth/login", json={"account": "teacher"}).status_code == 200
        assert client.post("/api/admin/enhance").status_code == 409


@pytest.mark.parametrize("address,allowed,key,success", [
    ("http://127.0.0.1:9000/v1", True, "", True),
    ("http://[::1]:9000/v1", True, "", True),
    ("http://localhost:9000/v1", False, "test", False),
    ("http://10.0.0.1/v1", True, "test", False),
    ("http://example.com/v1", True, "test", False),
    ("https://user:secret@example.com/v1", False, "test", False),
    ("https://example.com/v1?secret=hidden", False, "test", False),
    ("https://example.com/v1", False, "test", True),
])
def test_local_explicit_only(address, allowed, key, success):
    settings = Settings(llm_base_url=address, llm_api_key=key, llm_model="custom",
                        llm_allow_local=allowed)
    if success:
        settings.require("llm")
    else:
        with pytest.raises(UserError):
            settings.require("llm")


async def test_generic_local_and_split_services(store):
    settings = Settings(llm_base_url="http://localhost:9000/v1", llm_model="my-chat",
                        llm_allow_local=True, embedding_base_url="https://vectors.example/v2",
                        embedding_model="my-vector", embedding_api_key="test-vector")
    seen = []

    def respond(request):
        seen.append(request)
        body = json.loads(request.content)
        assert "thinking" not in body and "response_format" not in body
        assert "x-opencode-session" not in request.headers
        if request.url.host == "localhost":
            assert "Authorization" not in request.headers
            assert body["model"] == "my-chat"
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})
        assert request.headers["Authorization"] == "Bearer test-vector"
        assert body["model"] == "my-vector"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 3]}]})

    model = Models(settings, store, httpx.MockTransport(respond))
    assert await model.chat("JSON only", {}) == {"ok": True}
    assert await model.embed(["hello"]) == [[1, 3]]
    old_key = model.embedding_key
    settings.embedding_revision = "v2"
    assert model.embedding_key != old_key
    assert len(seen) == 2


async def test_custom_protocol_normalized_and_still_validated(settings, store, monkeypatch):
    module = types.ModuleType("custom_vector_fixture")

    class Adapter:
        def prepare(self, kind, endpoint, body, settings):
            return "/vectorize", {"texts": body["input"], "model": body["model"]}

        def normalize(self, kind, response):
            return {"data": [{"index": i, "embedding": v} for i, v in enumerate(response["vectors"])]}

    module.provider = Adapter()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    settings.embedding_provider = "python:custom_vector_fixture"
    vectors = [[1, 2]]

    def respond(request):
        assert request.url.path.endswith("/vectorize")
        assert json.loads(request.content)["texts"] == ["a"]
        return httpx.Response(200, json={"vectors": vectors})

    model = Models(settings, store, httpx.MockTransport(respond))
    assert await model.embed(["a"]) == [[1, 2]]
    vectors[:] = [[0, 0]]
    with pytest.raises(UserError, match="向量格式"):
        await model.embed(["a"])


def test_config_only_loads_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENABLE_ENHANCEMENT", "wrong")
    with pytest.raises(UserError, match="true"):
        Settings.load()
    monkeypatch.setenv("ENABLE_ENHANCEMENT", "false")
    monkeypatch.setenv("HOST", "evil.example")
    with pytest.raises(UserError, match="HOST"):
        Settings.load()


def test_export_current_untracked_but_no_private_assets(tmp_path):
    root = tmp_path.resolve()
    for name, data in {
        "README.md": "# demo", "src/sctutor/new_feature.py": "VALUE = 1\n",
        "tests/test_demo.py": "def test_ok(): pass\n", ".env": "secret",
        "data/sctutor.sqlite3": "private", "courses/lesson.pptx": "private",
        "tests/eval_questions.json": "private", "tests/live_smoke.py": "private",
        "src/sctutor/__pycache__/cached.py": "private", ".git/config": "private",
        "docs/comet/archive/old/brief.md": "private", ".pi/session": "private",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
    destination = root / "handover-export/one"
    exporter.export(root, destination)
    assert (destination / "src/sctutor/new_feature.py").is_file()
    assert not (destination / ".env").exists()
    assert not (destination / "data").exists()
    assert not (destination / "courses").exists()
    assert not (destination / ".git").exists()
    assert not (destination / "tests/eval_questions.json").exists()
    assert "PENDING" in (destination / "docs/handover/DELIVERY.md").read_text()
    with pytest.raises(ValueError, match="exists"):
        exporter.export(root, destination)
    assert (root / ".env").read_text() == "secret"


def test_export_refuses_secret_without_echo(tmp_path):
    root = tmp_path.resolve()
    path = root / "src/sctutor/leak.py"
    path.parent.mkdir(parents=True)
    token = "sk-" + "a" * 32
    path.write_text(f"KEY='{token}'")
    with pytest.raises(ValueError) as exc:
        exporter.export(root, root / "handover-export/leak")
    assert "leak.py:1" in str(exc.value) and token not in str(exc.value)
    assert not (root / "handover-export/leak").exists()


def test_export_refuses_symlink(tmp_path):
    root = tmp_path.resolve()
    (root / "src/sctutor").mkdir(parents=True)
    (root / "private").write_text("private")
    (root / "src/sctutor/hidden.py").symlink_to(root / "private")
    with pytest.raises(ValueError, match="symlink"):
        exporter.export(root, root / "handover-export/link")


def test_archive_existence_is_not_acceptance(tmp_path):
    root = tmp_path.resolve()
    archive = root / 'docs/comet/archive/2099-01-01-public-repository-handover'
    archive.mkdir(parents=True)
    (archive / 'verification.md').write_text('not sufficient by itself')
    files = {}
    exporter.add_spec(root, files)
    assert b'PENDING' in files['docs/handover/DELIVERY.md']
    (archive / 'comet-state.yaml').write_text('status: done\nverification_result: pass\narchived: true\n')
    files = {}
    exporter.add_spec(root, files)
    assert b'accepted archive available' in files['docs/handover/DELIVERY.md']
    assert 'docs/handover/accepted/verification.md' in files
    assert not any(name.endswith('comet-state.yaml') for name in files)


def test_formal_symlink_is_not_read(tmp_path):
    root = tmp_path.resolve()
    active = root / 'docs/comet/changes/public-repository-handover/specs/guided-tutoring'
    active.mkdir(parents=True)
    (root / 'private.txt').write_text('private')
    (active / 'spec.md').symlink_to(root / 'private.txt')
    with pytest.raises(ValueError, match='symlink'):
        exporter.add_spec(root, {})


def test_doc_links_and_readme_size():
    import re

    readme = (ROOT / "README.md").read_text()
    assert len(readme.splitlines()) < 90
    for doc in [ROOT / "README.md", *list((ROOT / "docs/handover").glob("*.md"))]:
        for link in re.findall(r"\]\(([^)]+)\)", doc.read_text()):
            if "://" not in link and not link.startswith("#"):
                assert (doc.parent / link.split("#")[0]).exists(), (doc, link)


def test_container_contract():
    docker = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "compose.yaml").read_text()
    assert "USER tutor" in docker and "--locked" in docker
    assert '127.0.0.1:${PORT:-8000}:8000' in compose
    assert 'ENABLE_ENHANCEMENT: "false"' in compose
    assert "COPY . " not in docker and "COPY .env" not in docker
