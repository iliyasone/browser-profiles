from pathlib import Path

import pytest
from pydantic import ValidationError

from app.store import ProfileSpec, Proxy, Store


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

    store.delete("ie-1", purge=False)
    assert not store.exists("ie-1")
    assert (tmp_path / "ie-1" / "config").is_dir()  # data kept

    store.save(spec)
    store.delete("ie-1", purge=True)
    assert not (tmp_path / "ie-1").exists()


def test_names_are_restricted(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ProfileSpec(name="Bad Name")
    with pytest.raises(KeyError):
        Store(tmp_path).load("../etc")
