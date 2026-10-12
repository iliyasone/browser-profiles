import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import extension
from app.store import Login, ProfileSpec, Proxy, Store


def test_proxy_url_quotes_credentials() -> None:
    proxy = Proxy(
        host="proxy.example", port=8080, username="us er", password="p@ss:w/rd"
    )
    assert proxy.url() == "http://us%20er:p%40ss%3Aw%2Frd@proxy.example:8080"
    assert proxy.redacted() == "http://us er:***@proxy.example:8080"
    assert Proxy(scheme="socks5", host="h", port=1).url() == "socks5://h:1"


def test_store_roundtrip_and_delete(tmp_path: Path) -> None:
    store = Store(tmp_path)
    spec = ProfileSpec(
        name="ie-1", proxy=Proxy(host="h", port=1), timezone="Europe/Dublin"
    )
    store.save(spec)
    assert (tmp_path / "ie-1" / "config").is_dir()
    assert store.load("ie-1") == spec
    assert [s.name for s in store.list()] == ["ie-1"]

    (tmp_path / "ie-1" / "extension").mkdir()
    store.delete("ie-1", purge=False)
    assert not store.exists("ie-1")
    assert not (tmp_path / "ie-1" / "extension").exists()  # saved logins gone
    assert (tmp_path / "ie-1" / "config").is_dir()  # data kept

    store.save(spec)
    store.delete("ie-1", purge=True)
    assert not (tmp_path / "ie-1").exists()


def test_names_are_restricted(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ProfileSpec(name="Bad Name")
    with pytest.raises(KeyError):
        Store(tmp_path).load("../etc")


def test_extension_matches_login_sites_only(tmp_path: Path) -> None:
    extension.write(
        tmp_path / "ext",
        [
            Login(url="https://www.example.com/en/", username="a", password="p"),
            Login(url="https://other.example.org", username="b", password="q"),
        ],
    )
    manifest = json.loads((tmp_path / "ext" / "manifest.json").read_text())
    assert manifest["content_scripts"][0]["matches"] == [
        "https://example.com/*",
        "https://*.example.com/*",
        "https://other.example.org/*",
        "https://*.other.example.org/*",
    ]
    assert '"host": "example.com"' in (tmp_path / "ext" / "logins.js").read_text()
    assert (tmp_path / "ext" / "content.js").is_file()
