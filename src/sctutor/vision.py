import asyncio
import hashlib
import json
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from .config import UserError
from .pptx import chunks
from .prompts import rule
from .rendering import Renderer, digest
from .retrieval import fingerprint


class Caption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=5000)
    uncertain: StrictBool


async def enhance(store, models, directory, renderer=None):
    renderer = renderer or Renderer(store.path.parent)
    report = []
    for doc in store.query("SELECT * FROM documents WHERE root=? ORDER BY path", (str(directory.resolve()),)):
        try:
            source = directory / doc["path"]
            if digest(source) != doc["hash"]:
                raise UserError("课件版本已变化，请先增量入库。")
            rendered = await asyncio.to_thread(renderer.render, source)
            pages = store.query("SELECT * FROM pages WHERE doc_id=? ORDER BY page", (doc["id"],))
            if rendered["pages"] != len(pages):
                raise UserError("索引与导出页数不一致，请重新入库。")
            prepared, descriptions = [], 0
            for page in pages:
                image = rendered["directory"] / f"page-{page['page']}.png"
                caption = ""
                key = ""
                if len("".join(page["text"].split())) < 30 and page["images"]:
                    key = hashlib.sha256((digest(image) + models.settings.llm_base_url + models.settings.llm_model + rule("vision")).encode()).hexdigest()
                    cached = store.query("SELECT caption FROM caption_cache WHERE key=?", (key,))
                    if cached:
                        caption = cached[0]["caption"]
                    else:
                        raw = await models.chat(rule("vision"), {"page": page["page"], "existing_text": page["text"]}, purpose="vision", image=image.read_bytes())
                        try:
                            result = Caption.model_validate(raw)
                        except ValidationError:
                            raise UserError("图片说明格式无效，未发布该文件的新增强版本。") from None
                        caption = "【机器生成图片说明" + ("，含不确定信息" if result.uncertain else "") + "】\n" + result.description
                        store.execute("INSERT OR REPLACE INTO caption_cache VALUES (?,?)", (key, caption))
                    descriptions += 1
                text = "\n".join(x for x in (page["text"], page["notes"], caption) if x)
                prepared.append((page, image, caption, key, chunks(text)))
            texts = list(dict.fromkeys(t for _, _, _, _, cs in prepared for t in cs))
            vectors = {}
            for text in texts:
                cached = store.query("SELECT vector FROM embedding_cache WHERE key=?", (fingerprint(models.embedding_key, text),))
                if cached:
                    vectors[text] = json.loads(cached[0]["vector"])
            missing = [t for t in texts if t not in vectors]
            for offset in range(0, len(missing), 8):
                batch = missing[offset:offset + 8]
                new = await models.embed(batch)
                if len(new) != len(batch):
                    raise UserError("嵌入数量不一致。")
                for text, vector in zip(batch, new, strict=True):
                    vectors[text] = vector
                    store.execute("INSERT OR REPLACE INTO embedding_cache VALUES (?,?)", (fingerprint(models.embedding_key, text), json.dumps(vector)))
            if len({len(v) for v in vectors.values()}) > 1:
                raise UserError("嵌入维度不一致，请重建索引。")
            if digest(source) != doc["hash"]:
                raise UserError("处理期间源版本变化，保留此前索引。")
            with store.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                if not c.execute("SELECT 1 FROM documents WHERE id=? AND hash=? AND embedding_key=?", (doc["id"], doc["hash"], models.embedding_key)).fetchone():
                    raise UserError("处理期间索引版本变化，未发布增强。")
                for page, image, caption, key, cs in prepared:
                    c.execute("DELETE FROM chunks WHERE page_id=?", (page["id"],))
                    c.executemany("INSERT INTO chunks VALUES (?,?,?,?)", [(uuid4().hex, page["id"], t, json.dumps(vectors[t])) for t in cs])
                    c.execute("UPDATE pages SET indexed=? WHERE id=?", (bool(cs), page["id"]))
                    c.execute("INSERT OR REPLACE INTO page_assets VALUES (?,?,?,?)", (page["id"], str(image), caption, key or rendered["key"]))
            report.append({"file": doc["path"], "status": "enhanced", "pages": len(pages), "described_pages": descriptions})
        except Exception as e:
            report.append({"file": doc["path"], "status": "blocked", "message": str(e) if isinstance(e, UserError) else "增强失败，保留此前可用索引。"})
    store.execute("INSERT INTO processing(created,kind,status,detail) VALUES (?,?,?,?)", (time.time(), "enhance", "blocked" if any(r["status"] == "blocked" for r in report) else "completed", json.dumps(report, ensure_ascii=False)))
    return report
