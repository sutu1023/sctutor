"""Wire adapter contract. Custom trusted Python modules expose `provider`.

prepare(kind, endpoint, body, settings) -> (endpoint, body)
normalize(kind, response) -> OpenAI-shaped dict consumed by Models' strict validators.
Adapters never control pedagogy, retries, credentials, metrics or UI publication.
"""
import importlib
from urllib.parse import urlparse

from .config import UserError


class OpenAIProvider:
    def prepare(self, kind, endpoint, body, settings):
        body = dict(body)
        if kind == "llm" and settings.llm_profile == "deepseek-json":
            body.update(thinking={"type": "disabled"}, response_format={"type": "json_object"})
        return endpoint, body

    def normalize(self, kind, response):
        return response


def session_headers(settings, session_id):
    """Only this known service requires its own stable conversation header."""
    if urlparse(settings.llm_base_url).hostname == "opencode.ai":
        return {"x-opencode-session": session_id}
    return {}


def provider_for(name):
    if name == "openai":
        return OpenAIProvider()
    # Opt-in trusted code, never accepted from web requests/model output.
    try:
        if not name.startswith("python:"):
            raise ValueError()
        provider = importlib.import_module(name[7:]).provider
        if not callable(provider.prepare) or not callable(provider.normalize):
            raise ValueError()
        return provider
    except (ImportError, AttributeError, ValueError):
        raise UserError("模型适配器不可用，请检查 PROVIDER 配置及本机可信模块。") from None
