import asyncio
import base64
import json
import math
import time
from uuid import uuid4

import httpx

from .config import Settings, UserError
from .providers import provider_for, session_headers


class Models:
    def __init__(self, settings: Settings, store, transport=None):
        self.settings, self.store = settings, store
        self.transport = transport
        # Stable for utility calls (smoke); real tutoring requests use the persisted conversation.
        self.utility_session_id = uuid4().hex

    @property
    def embedding_key(self):
        base = self.settings.embedding_base_url.rstrip("/") + "/" + self.settings.embedding_model
        suffix = self.settings.embedding_revision
        if self.settings.embedding_provider != "openai":
            suffix += ":" + self.settings.embedding_provider
        return base + ("#" + suffix if suffix else "")

    async def request(self, kind, endpoint, body, tid=None, purpose=None):
        self.settings.require(kind)
        base = getattr(self.settings, f"{kind}_base_url").rstrip("/")
        key = getattr(self.settings, f"{kind}_api_key")
        model = getattr(self.settings, f"{kind}_model")
        adapter = provider_for(getattr(self.settings, f"{kind}_provider"))
        try:
            endpoint, body = adapter.prepare(kind, endpoint, {"model": model, **body}, self.settings)
            if not endpoint.startswith("/") or endpoint.startswith("//") or ":" in endpoint:
                raise ValueError()
        except Exception:
            raise UserError("模型适配器请求转换失败。") from None
        headers = {"User-Agent": "SC-Tutor/0.1"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if kind == "llm":
            rows = self.store.query("SELECT session_id FROM turns WHERE id=?", (tid,)) if tid else []
            headers.update(session_headers(self.settings, rows[0]["session_id"] if rows else self.utility_session_id))
        for attempt in range(2):
            start = time.monotonic()
            outcome, usage = "error", None
            try:
                async with httpx.AsyncClient(timeout=60, transport=self.transport,
                                             follow_redirects=False) as client:
                    r = await client.post(base + endpoint, json=body,
                                          headers=headers)
                if r.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    await asyncio.sleep(1)
                    continue
                if r.status_code >= 300:
                    raise UserError(f"{kind.upper()} 服务请求失败（HTTP {r.status_code}），请检查配置、权限或额度。")
                try:
                    data = adapter.normalize(kind, r.json())
                except Exception:
                    raise UserError("模型适配器响应转换失败。") from None
                if not isinstance(data, dict):
                    raise ValueError("invalid response")
                raw_usage = data.get("usage")
                if isinstance(raw_usage, dict):
                    usage = {k: v for k, v in raw_usage.items()
                             if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                             and type(v) is int and v >= 0} or None
                outcome = "ok"
                return data
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                raise UserError(f"{kind.upper()} 服务网络失败或超时，请重试。") from None
            except (ValueError, KeyError, TypeError):
                raise UserError(f"{kind.upper()} 服务返回了不兼容的数据格式。") from None
            finally:
                self.store.call(tid, purpose or kind, model,
                                time.monotonic() - start, usage, outcome)

    async def embed(self, texts, tid=None):
        vectors = []
        for offset in range(0, len(texts), 8):
            batch = texts[offset:offset + 8]
            data = await self.request("embedding", "/embeddings",
                                      {"input": batch, "encoding_format": "float"}, tid)
            try:
                rows = sorted(data["data"], key=lambda x: x["index"])
                if [r["index"] for r in rows] != list(range(len(batch))):
                    raise ValueError()
                for row in rows:
                    vector = row["embedding"]
                    if not isinstance(vector, list) or not vector or any(
                        type(x) not in (int, float) or not math.isfinite(x) for x in vector
                    ) or sum(x*x for x in vector) == 0:
                        raise ValueError()
                    vectors.append(vector)
            except (KeyError, TypeError, ValueError):
                raise UserError("嵌入服务返回了不兼容的向量格式。") from None
        if vectors and len({len(v) for v in vectors}) != 1:
            raise UserError("嵌入向量维度不一致，请检查模型配置。")
        return vectors

    async def chat(self, system, payload, tid=None, purpose="generate", image=None):
        content = json.dumps(payload, ensure_ascii=False)
        if image is not None:
            if len(image) > 12 * 1024 * 1024 or not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise UserError("视觉输入必须为不超过12MB的PNG页图。")
            content = [{"type": "text", "text": content},
                       {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}}]
        data = await self.request("llm", "/chat/completions", {
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": content}],
            "temperature": 0.2, "max_tokens": 1800,
        }, tid, purpose)
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise UserError("模型输出被长度限制截断，本轮已安全停止，未发送草稿。")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ValueError()
            text = content.strip()
            if text.startswith("```json\n") and text.endswith("```"):
                text = text[8:-3].strip()
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (KeyError, IndexError, TypeError, ValueError):
            raise UserError("模型未返回要求的 JSON 格式，本轮已安全停止。") from None
