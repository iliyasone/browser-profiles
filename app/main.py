import os
import secrets
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.idle import IdleStop
from app.runtime import Runtime, Settings, Status
from app.store import NAME_PATTERN, ProfileSpec, Proxy, Store

EGRESS_URL = os.environ.get("EGRESS_URL", "https://ipinfo.io/json")
# A running profile with no open screen for this long is stopped (0 = never).
IDLE_STOP_MINUTES = float(os.environ.get("IDLE_STOP_MINUTES", "15"))
IDLE_CHECK_SECONDS = 30


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    halt = threading.Event()
    if IDLE_STOP_MINUTES > 0:
        idle = IdleStop(store(), runtime(), timedelta(minutes=IDLE_STOP_MINUTES))
        threading.Thread(
            target=idle.run_forever, args=(IDLE_CHECK_SECONDS, halt), daemon=True
        ).start()
    try:
        yield
    finally:
        halt.set()


app = FastAPI(title="browser-profiles", version="0.1.0", lifespan=lifespan)


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.environ.get("API_TOKEN", "")
    given = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected or not secrets.compare_digest(given, expected):
        raise HTTPException(401, "missing or wrong bearer token")


def runtime() -> Runtime:
    if not hasattr(app.state, "runtime"):
        app.state.runtime = Runtime(Settings.from_env())
    return app.state.runtime


def store() -> Store:
    if not hasattr(app.state, "store"):
        app.state.store = Store(Settings.from_env().data_dir)
    return app.state.store


class ProfileView(BaseModel):
    name: str
    proxy: str | None
    timezone: str
    start_url: str
    status: Status
    ui_url: str
    data_dir: str
    created_at: datetime


def view(spec: ProfileSpec) -> ProfileView:
    s = runtime().settings
    return ProfileView(
        name=spec.name,
        proxy=spec.proxy.redacted() if spec.proxy else None,
        timezone=spec.timezone,
        start_url=spec.start_url,
        status=runtime().status(spec.name),
        ui_url=f"{s.public_url}/b/{spec.name}/",
        data_dir=str(s.host_data_dir / spec.name),
        created_at=spec.created_at,
    )


def load_or_404(name: str) -> ProfileSpec:
    try:
        return store().load(name)
    except KeyError:
        raise HTTPException(404, f"no profile {name!r}") from None


class CreateProfile(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    proxy: Proxy | None = None
    timezone: str = "UTC"
    start_url: str = "about:blank"
    start: bool = True


class UpdateProfile(BaseModel):
    proxy: Proxy | None = None
    clear_proxy: bool = False
    timezone: str | None = None
    start_url: str | None = None


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "web" / "index.html")


@app.get("/profiles", dependencies=[Depends(require_token)])
def list_profiles() -> list[ProfileView]:
    return [view(spec) for spec in store().list()]


@app.post("/profiles", status_code=201, dependencies=[Depends(require_token)])
def create_profile(body: CreateProfile) -> ProfileView:
    if store().exists(body.name):
        raise HTTPException(409, f"profile {body.name!r} already exists")
    spec = ProfileSpec(
        name=body.name,
        proxy=body.proxy,
        timezone=body.timezone,
        start_url=body.start_url,
    )
    store().save(spec)
    if body.start:
        runtime().start(spec)
    return view(spec)


@app.get("/profiles/{name}", dependencies=[Depends(require_token)])
def get_profile(name: str) -> ProfileView:
    return view(load_or_404(name))


@app.patch("/profiles/{name}", dependencies=[Depends(require_token)])
def update_profile(name: str, body: UpdateProfile) -> ProfileView:
    """Change the proxy, timezone or start URL. A running profile is recreated
    so the change takes effect; its Chromium home is untouched."""
    spec = load_or_404(name)
    changes = body.model_dump(exclude_none=True, exclude={"clear_proxy"})
    if body.clear_proxy:
        changes["proxy"] = None
    spec = spec.model_copy(update=changes)
    store().save(spec)
    if runtime().status(name) == "running":
        runtime().remove(name)
        runtime().start(spec)
    return view(spec)


@app.post("/profiles/{name}/start", dependencies=[Depends(require_token)])
def start_profile(name: str) -> ProfileView:
    spec = load_or_404(name)
    runtime().start(spec)
    return view(spec)


@app.post("/profiles/{name}/stop", dependencies=[Depends(require_token)])
def stop_profile(name: str) -> ProfileView:
    spec = load_or_404(name)
    runtime().stop(name)
    return view(spec)


@app.delete("/profiles/{name}", status_code=204, dependencies=[Depends(require_token)])
def delete_profile(name: str, purge: bool = False) -> Response:
    """Remove the containers and forget the profile. `purge=true` also deletes
    its directory (cookies, logins, downloads); otherwise the Chromium home
    stays on disk and a new profile of the same name picks it up."""
    load_or_404(name)
    runtime().remove(name)
    store().delete(name, purge=purge)
    return Response(status_code=204)


@app.post("/profiles/{name}/egress", dependencies=[Depends(require_token)])
def check_egress(name: str) -> dict[str, object]:
    """Dial the profile's proxy from the API and report what the internet sees
    (exit IP, country) — works whether or not the profile is running."""
    spec = load_or_404(name)
    try:
        with httpx.Client(
            proxy=spec.proxy.url() if spec.proxy else None, timeout=20
        ) as http:
            response = http.get(EGRESS_URL)
            response.raise_for_status()
            return {
                "ok": True,
                "via": spec.proxy.redacted() if spec.proxy else None,
                "egress": response.json(),
            }
    except (httpx.HTTPError, ValueError) as error:
        return {
            "ok": False,
            "via": spec.proxy.redacted() if spec.proxy else None,
            "error": str(error),
        }
