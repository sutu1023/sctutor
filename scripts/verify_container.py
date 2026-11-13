"""Isolated Compose smoke. Uses synthetic assets; leaves volumes intact.

Run only against a disposable exported snapshot with no .env. Builds/recreates its
own named project. Never calls AI, never deletes volumes or a user's database.
"""
import argparse
import io
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    root = args.directory.resolve()
    if (root / ".env").exists():
        parser.error("Use a clean exported snapshot without .env")
    project = "sctutor-check-" + uuid4().hex[:8]
    env = {**os.environ, "PORT": str(args.port)}
    for key in list(env):
        if key.startswith(("LLM_", "EMBEDDING_", "TAVILY_")):
            del env[key]
    command = ["docker", "compose", "--env-file", os.devnull, "-p", project]

    def compose(*arguments, capture=False):
        return subprocess.run([*command, *arguments], cwd=root, env=env, check=True,
                              capture_output=capture, text=True)

    base = f"http://127.0.0.1:{args.port}"

    def ready():
        for _ in range(60):
            try:
                if httpx.get(base + "/api/health", timeout=2, trust_env=False).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise AssertionError("Container readiness timed out")

    try:
        compose("up", "--build", "-d")
        ready()
        image = compose('images', '-q', 'tutor', capture=True).stdout.strip()
        subprocess.run(['docker', 'run', '--rm', '--entrypoint', 'python', image, '-c',
                        "import pathlib; p=pathlib.Path('/app'); assert not (p/'.env').exists(); assert not (p/'.git').exists(); assert not list((p/'courses').iterdir()); assert not list((p/'data').iterdir()); print('clean image assets: passed')"], check=True)
        cid = compose('ps', '-q', 'tutor', capture=True).stdout.strip()
        ports = json.loads(subprocess.run(['docker', 'inspect', '--format', '{{json .NetworkSettings.Ports}}', cid],
                                         check=True, capture_output=True, text=True).stdout)
        assert ports['8000/tcp'][0]['HostIp'] == '127.0.0.1'
        with httpx.Client(base_url=base, trust_env=False) as client:
            assert client.get("/api/health").json()["pages"] == 0
            assert client.get("/").status_code == 200
            assert client.get("/api/sessions").status_code == 401
            assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
            assert client.post("/api/auth/login", json={"account": "student"}).status_code == 200
            assert client.get("/api/admin/pages").status_code == 403
            assert client.get("/api/sessions", headers={"Origin": "https://evil.example"}).status_code == 403
            session = client.post("/api/sessions").json()["id"]
            assert client.patch("/api/sessions/" + session, json={"name": "synthetic persistence"}).status_code == 200
            assert client.post("/api/auth/login", json={"account": "teacher"}).status_code == 200
            assert client.post("/api/admin/enhance").status_code == 409
            buf = io.BytesIO()
            with ZipFile(buf, "w") as z:
                z.writestr("[Content_Types].xml", '<Types>application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml</Types>')
                z.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>')
                z.writestr("ppt/_rels/presentation.xml.rels", '<Relationships><Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/></Relationships>')
                z.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>Synthetic handover fixture</a:t></p:sld>')
            response = client.post("/api/admin/upload", files={"files": ("synthetic.pptx", buf.getvalue())})
            assert response.status_code == 200 and response.json()["files"][0]["status"] == "uploaded", response.status_code
        compose("up", "-d", "--force-recreate")
        ready()
        with httpx.Client(base_url=base, trust_env=False) as client:
            client.post("/api/auth/login", json={"account": "student"})
            assert any(s['id'] == session and s['name'] == 'synthetic persistence'
                       for s in client.get('/api/sessions').json())
        result = compose("exec", "-T", "tutor", "python", "-c",
                         "import os,pathlib; assert os.getuid()==10001; assert pathlib.Path('/app/courses/uploads/synthetic.pptx').is_file(); assert not pathlib.Path('/app/.env').exists(); print('nonroot and persisted synthetic upload: passed')", capture=True)
        print(result.stdout.strip())
        print(json.dumps({"status": "passed", "project": project, "cloud_calls": 0,
                          "checks": ["clean build/start/login", "Host/Origin/role rejection",
                                     "Office disabled", "nonroot", "session and upload survive recreate"]}))
    finally:
        compose("down")  # Never -v: do not make destructive volume deletion a normal step.


if __name__ == "__main__":
    main()
