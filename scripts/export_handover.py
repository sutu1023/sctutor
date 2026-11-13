"""Conservative snapshot export, never Git operations or secret-value output.

Stdlib only. Run from any cwd. Private runtime/course assets are never traversed.
A scanner is a safety net, not proof that all sensitive content was recognized.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT_FILES = {"README.md", "AGENTS.md", ".env.example", ".gitignore", "pyproject.toml",
              "uv.lock", "Dockerfile", "compose.yaml", ".dockerignore"}
BROWSER_FILES = {"conftest.py", "browser_path.py", "browser_smoke.py", "prototype_smoke.py",
                 "experience_smoke.py"}
SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|(?:api[_-]?key|secret|password)\s*[=:]\s*['\"]?(?!test|fake|example|true|false)[A-Za-z0-9_-]{28,})",
    re.I,
)
PRIVATE_PATH = re.compile(r"/(?:Users|home)/[^\s`\"']+")
CHANGE = "public-repository-handover"


def clean_document(text):
    # Only documentation is transformed; source and tests must remain executable bytes.
    return PRIVATE_PATH.sub("<local-private-path>", text)


def allowed(path):
    parts = path.parts
    if len(parts) == 1:
        return path.name in ROOT_FILES
    if any(p.startswith(".") or p == "__pycache__" for p in parts[1:]):
        return False
    if parts[:2] == ("src", "sctutor"):
        return path.suffix in {".py", ".js", ".css", ".html", ".md"}
    if parts[0] == "tests":
        return len(parts) == 2 and path.suffix == ".py" and (
            path.name.startswith("test_") or path.name in BROWSER_FILES)
    if parts[0] == "scripts":
        return len(parts) == 2 and path.suffix == ".py"
    if parts[:2] == ("docs", "handover"):
        return path.suffix == ".md"
    return str(path) in {"docs/prototype-alignment.md", ".comet/config.yaml",
                         ".github/workflows/offline.yml"}


def scan_text(text, path):
    findings = []
    for n, line in enumerate(text.splitlines(), 1):
        if SECRET.search(line) or PRIVATE_PATH.search(line):
            findings.append(f"{path}:{n}: potential secret/private path (value withheld)")
    return findings


def candidates(root):
    # Do not walk .env, data, courses, old docs archives, Agent directories or .git.
    paths = [root / name for name in ROOT_FILES]
    for folder in ("src/sctutor", "tests", "scripts", "docs/handover"):
        base = root / folder
        if base.is_symlink():
            raise ValueError("Refusing symlinked export directory")
        paths.extend(base.rglob("*") if base.exists() else [])
    paths.extend(root / name for name in ("docs/prototype-alignment.md", ".comet/config.yaml",
                                         ".github/workflows/offline.yml"))
    result = {}
    for path in sorted(set(paths)):
        relative = path.relative_to(root)
        if not allowed(relative):
            continue
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
            raise ValueError("Refusing symlink in export candidates")
        if not path.is_file():
            continue
        data = path.read_bytes()
        if path.suffix == ".md":
            data = clean_document(data.decode()).encode()
        result[str(relative)] = data
    return result


def safe_document(path, root):
    if (not path.resolve().is_relative_to(root)
            or any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root))):
        raise ValueError('Refusing symlinked formal document')
    return path.read_text()


def add_spec(root, files):
    current = root / "docs/comet/specs/guided-tutoring/spec.md"
    active = root / f"docs/comet/changes/{CHANGE}/specs/guided-tutoring/spec.md"
    source = active if active.is_file() else current
    if source.is_file():
        text = safe_document(source, root)
        # Legacy MVP header/scenarios contain old acceptance identities and private corpus.
        at = text.find("## Requirement:")
        if at >= 0:
            text = "# SC-Tutor 当前完整目标规格\n\n历史MVP专属语料与旧场景不随交接分发。\n\n" + text[at:]
        files["docs/comet/specs/guided-tutoring/spec.md"] = clean_document(text).encode()
    archives = sorted((root / "docs/comet/archive").glob(f"*-{CHANGE}"))
    archive = archives[-1] if archives else None
    if archive:
        state = archive / 'comet-state.yaml'
        state_text = safe_document(state, root) if state.is_file() else ''
        if not all(re.search(r'^' + value + r'\s*$', state_text, re.M)
                   for value in ('status: done', 'verification_result: pass', 'archived: true')):
            archive = None
        elif not (archive / 'verification.md').is_file():
            archive = None
    # Portable archive documents only: no state/hash bindings altered and passed off as runnable.
    if archive:
        for name in ("brief.md", "verification.md"):
            path = archive / name
            if path.is_file() and not path.is_symlink():
                files[f"docs/handover/accepted/{name}"] = clean_document(safe_document(path, root)).encode()
    status = "accepted archive available; see accepted/verification.md" if archive else "PENDING user acceptance; not a final accepted delivery"
    files["docs/handover/DELIVERY.md"] = (
        "# Snapshot status\n\n" + status + "\n\n"
        "Sanitized documentation snapshot; no local Comet Runtime or active Run is copied.\n"
        "Regenerate after final acceptance/archive before publishing.\n"
    ).encode()


def export(root, destination):
    root, destination = root.resolve(), destination.absolute()
    # Fixed ignored namespace, fresh destination, never replace an existing snapshot.
    target_root = root / "handover-export"
    if destination.parent != target_root or target_root.is_symlink() or destination.is_symlink():
        raise ValueError("Destination must be a NEW direct child of handover-export/")
    if destination.exists():
        raise ValueError("Destination exists; choose a new snapshot name")
    files = candidates(root)
    add_spec(root, files)
    findings = [item for name, data in files.items()
                for item in scan_text(data.decode("utf-8"), name)]
    if findings:
        raise ValueError("Export refused:\n" + "\n".join(findings))
    destination.mkdir(parents=True)
    for name, data in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    (destination / "SNAPSHOT.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return len(files)


def history_scan(root):
    commits = subprocess.run(["git", "rev-list", "--all"], cwd=root, check=True,
                             capture_output=True, text=True).stdout.splitlines()
    # Inspect all reachable blobs, once. No secret bytes or raw file names printed.
    seen, findings = set(), []
    for commit in commits:
        rows = subprocess.run(["git", "ls-tree", "-r", commit], cwd=root, check=True,
                              capture_output=True, text=True).stdout.splitlines()
        for row in rows:
            meta, path = row.split("\t", 1)
            mode, kind, oid = meta.split()
            if kind != "blob" or oid in seen:
                continue
            seen.add(oid)
            label = f"commit {commit[:12]} blob {oid[:12]}"
            if Path(path).suffix.lower() in {".ppt", ".pptx", ".pdf", ".sqlite3"} or Path(path).name == ".env":
                findings.append(label + ": private asset filename (withheld)")
            raw = subprocess.run(["git", "cat-file", "blob", oid], cwd=root, check=True,
                                 capture_output=True).stdout
            findings.extend(scan_text(raw.decode("utf-8", errors="replace"), label))
    return {"reachable_commits": len(commits), "unique_blobs": len(seen), "findings": findings,
            "limit": "Heuristic only; old history is not included in the new snapshot."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Fresh snapshot path under handover-export/")
    parser.add_argument("--history", action="store_true", help="Read-only reachable Git history scan")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.history:
            print(json.dumps(history_scan(root), ensure_ascii=False, indent=2))
        elif args.output:
            count = export(root, Path(args.output))
            print(f"Exported {count} files. Review DELIVERY.md and publication checklist before publishing.")
        else:
            parser.error("choose --output or --history")
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
