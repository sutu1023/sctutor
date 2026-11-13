import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


class UserError(Exception):
    """Only deliberately safe messages may cross the API boundary."""


def flag(value):
    if value.lower() not in {"true", "false"}:
        raise UserError("布尔配置只能填写 true 或 false。")
    return value.lower() == "true"


@dataclass
class Settings:
    course_dir: Path = Path("courses")
    data_dir: Path = Path("data")
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = field(default="", repr=False)
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_api_key: str = field(default="", repr=False)
    tavily_api_key: str = field(default="", repr=False)
    llm_provider: str = "openai"
    embedding_provider: str = "openai"
    embedding_revision: str = ""
    llm_profile: str = "standard"
    llm_allow_local: bool = False
    embedding_allow_local: bool = False
    enable_enhancement: bool = False
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def load(cls):
        # Explicit cwd only: do not discover another project's/parent's secrets.
        load_dotenv(Path.cwd() / ".env")
        defaults = cls()
        kwargs = {}
        for name in cls.__dataclass_fields__:
            value = os.getenv(name.upper())
            if value is not None:
                try:
                    kwargs[name] = (Path(value) if name.endswith("_dir") else
                                    flag(value.strip()) if isinstance(getattr(defaults, name), bool)
                                    else int(value) if name == "port" else value.strip())
                except ValueError:
                    raise UserError("PORT 必须是整数。") from None
        result = cls(**kwargs)
        if result.host not in {"127.0.0.1", "0.0.0.0"} or not 1 <= result.port <= 65535:
            raise UserError("HOST 仅允许127.0.0.1或容器使用的0.0.0.0，PORT须为1至65535。")
        return result

    def require(self, kind: str):
        key = getattr(self, f"{kind}_api_key")
        try:
            url = urlparse(getattr(self, f"{kind}_base_url"))
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError()
            hostname = url.hostname
        except ValueError:
            raise UserError(f"{kind.upper()}_BASE_URL 地址格式无效。") from None
        local = hostname == "localhost"
        try:
            local = local or ipaddress.ip_address(url.hostname or "").is_loopback
        except ValueError:
            pass
        local_opt_in = local and getattr(self, f"{kind}_allow_local")
        if not key and not local_opt_in:
            raise UserError(f"请在本地 .env 配置 {kind.upper()}_API_KEY。")
        if (not url.hostname or url.username or url.password or url.query or url.fragment
                or (url.scheme != "https" and not (url.scheme == "http" and local_opt_in))):
            raise UserError(f"{kind.upper()}_BASE_URL 必须是无凭据的 HTTPS 地址；本机HTTP需显式允许。")
        if not getattr(self, f"{kind}_model"):
            raise UserError(f"请配置 {kind.upper()}_MODEL。")
        if self.llm_profile not in {"standard", "deepseek-json"}:
            raise UserError("LLM_PROFILE 仅允许 standard 或 deepseek-json。")
