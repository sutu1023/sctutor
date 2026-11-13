"""Bounded Tavily search. No arbitrary URL fetching or provider response passthrough."""
import asyncio
import hashlib
import ipaddress
import time
from urllib.parse import urlsplit

import httpx

from .config import UserError


def public_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        return False
    try:
        u = urlsplit(value)
        host = (u.hostname or '').lower().rstrip('.')
        if u.scheme != 'https' or not host or u.username or u.password or u.port not in (None, 443):
            return False
        if host in {'localhost', 'localhost.localdomain'} or host.endswith(('.local', '.localhost', '.internal', '.test', '.invalid')):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return '.' in host and not host.replace('.', '').isdigit() and ':' not in host
    except ValueError:
        return False


class WebSearch:
    def __init__(self, settings, store, transport=None):
        self.settings, self.store, self.transport = settings, store, transport

    async def search(self, question, tid):
        if not self.settings.tavily_api_key:
            raise UserError('Tavily未配置，联网检索不可用。')
        start = time.monotonic()
        outcome = 'error'
        try:
            async with asyncio.timeout(30):
                async with httpx.AsyncClient(timeout=12, transport=self.transport, follow_redirects=False) as client:
                    for attempt in range(2):
                        try:
                            r = await client.post('https://api.tavily.com/search',
                                headers={'Authorization': 'Bearer '+self.settings.tavily_api_key},
                                json={'query': question[:4000], 'max_results': 5, 'search_depth': 'basic',
                                      'include_answer': False, 'include_raw_content': False})
                            if (r.status_code == 429 or r.status_code >= 500) and attempt == 0:
                                await asyncio.sleep(.2)
                                continue
                            if r.status_code != 200 or len(r.content) > 1_000_000:
                                raise UserError('联网搜索服务失败或额度不足，未使用模型记忆补齐。')
                            raw = r.json()
                            break
                        except httpx.TransportError:
                            if attempt:
                                raise UserError('联网搜索连接失败，请稍后重试。') from None
                    if not isinstance(raw, dict) or not isinstance(raw.get('results'), list):
                        raise ValueError()
                    sources = []
                    for x in raw['results'][:5]:
                        if not isinstance(x, dict) or not public_url(x.get('url')):
                            continue
                        title, text = x.get('title'), x.get('content')
                        if not isinstance(title, str) or not isinstance(text, str) or not text.strip():
                            continue
                        sources.append({'id': 'web-'+hashlib.sha256((tid+x['url']).encode()).hexdigest()[:24],
                                        'type': 'web', 'url': x['url'], 'title': title[:300],
                                        'text': text[:4000], 'retrieved': time.time()})
                    outcome = 'ok'
                    return sources
        except UserError:
            raise
        except Exception:
            raise UserError('联网搜索超时或结果格式无效，未发送未经检查内容。') from None
        finally:
            self.store.call(tid, 'web-search', 'tavily', time.monotonic()-start, None, outcome)
