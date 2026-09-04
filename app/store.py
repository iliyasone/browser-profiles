"""Profile specs on disk: one directory per profile holding `profile.json`
(what to run) and `config/` (the Chromium home the browser container mounts)."""

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,30}$"
_NAME_RE = re.compile(NAME_PATTERN)


class Proxy(BaseModel):
    scheme: Literal["http", "socks5"] = "http"
    host: str
    port: int = Field(ge=1, le=65535)
    username: str | None = None
    password: str | None = None

    def url(self) -> str:
        """The upstream URL as the in-container forwarder and egress check dial it."""
        if self.username:
            creds = (
                quote(self.username, safe="")
                + ":"
                + quote(self.password or "", safe="")
            )
            return f"{self.scheme}://{creds}@{self.host}:{self.port}"
        return f"{self.scheme}://{self.host}:{self.port}"

    def redacted(self) -> str:
        who = f"{self.username}:***@" if self.username else ""
        return f"{self.scheme}://{who}{self.host}:{self.port}"


class ProfileSpec(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    proxy: Proxy | None = None
    timezone: str = "UTC"
    start_url: str = "about:blank"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list(self) -> list[ProfileSpec]:
        if not self.root.is_dir():
            return []
        specs = [
            self.load(entry.name)
            for entry in sorted(self.root.iterdir())
            if _NAME_RE.match(entry.name) and (entry / "profile.json").is_file()
        ]
        return specs

    def load(self, name: str) -> ProfileSpec:
        path = self.root / name / "profile.json"
        if not _NAME_RE.match(name) or not path.is_file():
            raise KeyError(name)
        return ProfileSpec.model_validate_json(path.read_text())

    def exists(self, name: str) -> bool:
        return (
            bool(_NAME_RE.match(name)) and (self.root / name / "profile.json").is_file()
        )

    def save(self, spec: ProfileSpec) -> None:
        directory = self.root / spec.name
        (directory / "config").mkdir(parents=True, exist_ok=True)
        (directory / "profile.json").write_text(spec.model_dump_json(indent=2) + "\n")

    def delete(self, name: str, purge: bool) -> None:
        """Forget the profile; with `purge` also destroy its Chromium home."""
        if not _NAME_RE.match(name):
            raise KeyError(name)
        directory = self.root / name
        if purge:
            shutil.rmtree(directory, ignore_errors=True)
        else:
            (directory / "profile.json").unlink(missing_ok=True)
