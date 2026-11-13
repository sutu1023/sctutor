"""PowerPoint is used only on private copies; cached assets are never mounted statically."""
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

import pymupdf
from defusedxml import ElementTree as ET

from .config import UserError
from .pptx import parse_pptx

SCRIPT = '''on run argv
set sourcePath to item 1 of argv
set outputFile to POSIX file (item 2 of argv)
set deckName to item 3 of argv
with timeout of 100 seconds
 tell application "Microsoft PowerPoint"
  set oldSecurity to automation security
  if oldSecurity is not missing value then set automation security to msoAutomationSecurityForceDisable
  try
   my launchCopy(sourcePath)
   repeat 50 times
    if exists presentation deckName then exit repeat
    delay 0.2
   end repeat
   set deck to presentation deckName
   save deck in outputFile as save as PDF
   close deck saving no
   if oldSecurity is not missing value then set automation security to oldSecurity
  on error errorText number errorNumber
   try
    close presentation deckName saving no
   end try
   if oldSecurity is not missing value then set automation security to oldSecurity
   error "PowerPoint export failed" number errorNumber
  end try
 end tell
end timeout
end run
on launchCopy(sourcePath)
 do shell script "/usr/bin/open -a 'Microsoft PowerPoint' " & quoted form of sourcePath
end launchCopy
'''


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_safe(path):
    with ZipFile(path) as z:
        if any("vbaproject" in x.lower() for x in z.namelist()):
            raise UserError("含宏的文件不允许自动导出。")
        for name in z.namelist():
            if name.endswith(".rels"):
                for rel in ET.fromstring(z.read(name)):
                    if rel.attrib.get("TargetMode") == "External" and not rel.attrib.get("Type", "").endswith("/hyperlink"):
                        raise UserError("存在外部链接资源，拒绝自动打开以免更新外部内容。")


class Renderer:
    def __init__(self, data_dir):
        self.root = (Path(data_dir) / "rendered").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def render(self, path, exporter=None):
        path = Path(path).resolve()
        before = digest(path)
        count = len(parse_pptx(path))
        key = hashlib.sha256((before + ":powerpoint-pdf-v1:150dpi").encode()).hexdigest()
        destination = self.root / key
        manifest = destination / "manifest.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text())
            if data.get("pages") == count and all((destination / f"page-{i}.png").is_file() for i in range(1, count + 1)):
                return {"hash": before, "pages": count, "key": key, "directory": destination}
        check_safe(path)
        with tempfile.TemporaryDirectory(prefix="sctutor-export-", dir=self.root) as work:
            work = Path(work)
            # Unique per invocation: never mistake an orphaned/exported user window for ours.
            copy = work / (work.name + ".pptx")
            shutil.copyfile(path, copy)
            pdf = work / "slides.pdf"
            try:
                if exporter:
                    exporter(copy, pdf)
                else:
                    subprocess.run(["/usr/bin/osascript", "-", str(copy), str(pdf), copy.name], input=SCRIPT, text=True, capture_output=True, timeout=115, check=True)
                with pymupdf.open(pdf) as doc:
                    if len(doc) != count:
                        raise UserError("导出PDF页数与PPTX不符，未发布原页。")
                    for i, page in enumerate(doc, 1):
                        page.get_pixmap(dpi=120).save(work / f"page-{i}.png")
            except UserError:
                raise
            except subprocess.CalledProcessError as e:
                code = re.search(r"\((-?\d+)\)\s*$", e.stderr or "")
                detail = f"（Office错误码{code.group(1)}）" if code else ""
                raise UserError("PowerPoint导出失败" + detail + "；请检查Office许可、文件访问和macOS自动化授权，未发布原页。") from None
            except Exception:
                raise UserError("PowerPoint导出失败或权限/许可不可用；请检查macOS自动化授权，未伪造原页。") from None
            if digest(path) != before:
                raise UserError("导出期间课件版本变化，未发布原页。")
            copy.unlink()
            (work / "manifest.json").write_text(json.dumps({"source_hash": before, "pages": count}))
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(work), destination)
        return {"hash": before, "pages": count, "key": key, "directory": destination}

    def asset(self, store, page_id):
        rows = store.query("SELECT a.image FROM page_assets a JOIN pages p ON a.page_id=p.id WHERE p.id=?", (page_id,))
        if not rows:
            raise UserError("此页尚无原页图，请先处理课件。")
        path = Path(rows[0]["image"]).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise UserError("原页缓存不可用，请重新处理。")
        return path
