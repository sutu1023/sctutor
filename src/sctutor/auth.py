"""Local demo identities, not a production identity provider."""
import hashlib
import secrets
import time
from contextvars import ContextVar
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

IDENTITY = ContextVar("sctutor_identity", default="student")
ACCOUNTS = {
    "teacher": {"id": "teacher", "name": "教师演示", "role": "teacher"},
    "student": {"id": "student", "name": "学生演示", "role": "student"},
}
COOKIE = "sctutor_login"
TTL = 86400


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: Literal["teacher", "student"]


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, store):
        self.store = store
        store.execute("CREATE TABLE IF NOT EXISTS logins (token TEXT PRIMARY KEY, account TEXT NOT NULL, expires REAL NOT NULL)")

    def login(self, account):
        token = secrets.token_urlsafe(32)
        self.store.execute("DELETE FROM logins WHERE expires<=?", (time.time(),))
        self.store.execute("INSERT INTO logins VALUES (?,?,?)", (digest(token), account, time.time() + TTL))
        return token

    def identity(self, token):
        rows = self.store.query("SELECT account FROM logins WHERE token=? AND expires>?", (digest(token or ""), time.time()))
        if not rows or rows[0]["account"] not in ACCOUNTS:
            raise HTTPException(401, "请先选择演示账号登录。")
        return ACCOUNTS[rows[0]["account"]]

    def logout(self, token):
        self.store.execute("DELETE FROM logins WHERE token=?", (digest(token or ""),))
