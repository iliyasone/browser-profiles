"""Docker side of a profile: a Chromium container serving its screen over HTTP
plus, when the profile has a proxy, a forwarder sharing its network namespace
so Chromium can use an authenticated upstream through plain 127.0.0.1:18080."""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import docker
import docker.errors
import httpx
from docker.models.containers import Container

from app.store import ProfileSpec

CONTAINER_PREFIX = "bp"  # the gateway routes /b/<name>/ to bp-<name>:3000
FORWARDER_PORT = 18080
SCREEN_PORT = 3000
READY_TIMEOUT_SECONDS = 90

Status = Literal["running", "stopped", "absent"]


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    host_data_dir: Path
    public_url: str
    network: str
    browser_image: str
    proxy_image: str
    shm_size: str
    ui_user: str
    ui_password: str

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        return cls(
            data_dir=data_dir,
            host_data_dir=Path(os.environ.get("HOST_DATA_DIR", str(data_dir))),
            public_url=os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip(
                "/"
            ),
            network=os.environ.get("DOCKER_NETWORK", "browser-profiles"),
            browser_image=os.environ.get(
                "BROWSER_IMAGE", "lscr.io/linuxserver/chromium:latest"
            ),
            proxy_image=os.environ.get("PROXY_IMAGE", "gogost/gost:latest"),
            shm_size=os.environ.get("SHM_SIZE", "1g"),
            ui_user=os.environ.get("BROWSER_UI_USER", ""),
            ui_password=os.environ.get("BROWSER_UI_PASSWORD", ""),
        )


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = docker.from_env()

    def status(self, name: str) -> Status:
        browser = self._container(f"{CONTAINER_PREFIX}-{name}")
        if browser is None:
            return "absent"
        return "running" if browser.status == "running" else "stopped"

    def start(self, spec: ProfileSpec) -> None:
        """(Re)create the profile's containers. Idempotent: a healthy running
        pair is left alone; anything else is torn down and built again, so this
        is also the way to apply a changed proxy or timezone."""
        browser = self._container(f"{CONTAINER_PREFIX}-{spec.name}")
        forwarder = self._container(f"{CONTAINER_PREFIX}-{spec.name}-proxy")
        if browser is not None and browser.status == "running":
            if spec.proxy is None or (
                forwarder is not None and forwarder.status == "running"
            ):
                return
        self.remove(spec.name)
        s = self.settings
        try:
            self.client.networks.get(s.network)
        except docker.errors.NotFound:
            self.client.networks.create(s.network)

        chrome_flags = [spec.start_url]
        if spec.proxy is not None:
            chrome_flags.insert(0, f"--proxy-server=http://127.0.0.1:{FORWARDER_PORT}")
        environment = {
            "PUID": "1000",
            "PGID": "1000",
            "TZ": spec.timezone,
            "TITLE": spec.name,
            "SUBFOLDER": f"/b/{spec.name}/",
            "CHROME_CLI": " ".join(chrome_flags),
        }
        if s.ui_password:
            environment["CUSTOM_USER"] = s.ui_user or "user"
            environment["PASSWORD"] = s.ui_password
        browser = self.client.containers.run(
            s.browser_image,
            name=f"{CONTAINER_PREFIX}-{spec.name}",
            detach=True,
            environment=environment,
            volumes={
                str(s.host_data_dir / spec.name / "config"): {
                    "bind": "/config",
                    "mode": "rw",
                }
            },
            shm_size=s.shm_size,
            security_opt=["seccomp=unconfined"],
            network=s.network,
            # typeshed's stub predates "unless-stopped"; Docker accepts it.
            restart_policy={"Name": "unless-stopped"},  # pyright: ignore[reportArgumentType, reportCallIssue]
            labels={"browser-profiles.profile": spec.name},
        )
        if spec.proxy is not None:
            # Shares the browser's network namespace: Chromium talks to
            # 127.0.0.1:18080 without credentials, gost adds them upstream.
            self.client.containers.run(
                s.proxy_image,
                name=f"{CONTAINER_PREFIX}-{spec.name}-proxy",
                detach=True,
                command=["-L", f"http://:{FORWARDER_PORT}", "-F", spec.proxy.url()],
                network_mode=f"container:{browser.id}",
                restart_policy={"Name": "unless-stopped"},  # pyright: ignore[reportArgumentType, reportCallIssue]
                labels={"browser-profiles.profile": spec.name},
            )

        self.wait_until_ready(spec.name)

    def wait_until_ready(self, name: str) -> bool:
        """Poll the container's own web server until it serves the screen page.
        Chromium's image takes a while after `docker run` to bring nginx up;
        opening the URL before that shows a gateway error. False on timeout —
        the containers keep starting, the caller just cannot promise a screen."""
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        url = f"http://{CONTAINER_PREFIX}-{name}:{SCREEN_PORT}/b/{name}/"
        while time.monotonic() < deadline:
            try:
                if httpx.get(url, timeout=3).status_code < 500:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(1)
        return False

    def stop(self, name: str) -> None:
        for suffix in ("-proxy", ""):
            container = self._container(f"{CONTAINER_PREFIX}-{name}{suffix}")
            if container is not None and container.status == "running":
                container.stop(timeout=10)

    def remove(self, name: str) -> None:
        for suffix in ("-proxy", ""):
            container = self._container(f"{CONTAINER_PREFIX}-{name}{suffix}")
            if container is not None:
                container.remove(force=True)

    def _container(self, container_name: str) -> Container | None:
        try:
            return self.client.containers.get(container_name)
        except docker.errors.NotFound:
            return None
