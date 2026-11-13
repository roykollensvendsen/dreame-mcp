"""How the cloud client logs in: loading the reference code, the login, picking the robot, the settings.

Offline: the reference protocol, the Dreame cloud and .env are fakes; no robot,
cloud or real login involved.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import dreame_mcp.client as client_mod
from dreame_mcp.client import DreameHomeClient, _classify_cloud_failure, client_from_env

# -- the fake reference classes ----------------------------------------------------


def _cloud_class(*, login_ok=True, devices=None, devices_error=False, info_error=False, old_style=False):
    """A stand-in for the reference's Dreame cloud class; its instances are kept in .made."""
    made: list = []

    class Cloud:
        def __init__(self, username, password, **kw):
            if old_style and "account_type" in kw:
                raise TypeError("unexpected keyword argument 'account_type'")
            self.args = (username, password)
            self.kw = kw
            self._logged_in = False
            self._did = None
            self._host = "api.example"
            self.info_calls = 0
            made.append(self)

        def login(self):
            self._logged_in = login_ok
            return login_ok

        def get_devices(self):
            if devices_error:
                raise RuntimeError("device list down")
            return devices

        def get_device_info(self):
            self.info_calls += 1
            if info_error:
                raise RuntimeError("no device info")

    Cloud.made = made  # type: ignore[attr-defined]
    return Cloud


class LegacyCloud:
    """The protocol's own (MiHome) cloud."""

    def __init__(self, login_ok=False, last_code=None):
        self.login_ok = login_ok
        self.last_code = last_code
        self.logins = 0

    def login(self):
        self.logins += 1
        return self.login_ok

    def get_devices(self):
        return None


@pytest.fixture
def wire(monkeypatch):
    """Point the client at fake reference classes; returns what the protocol was built with."""

    def _wire(*, connected=False, cloud_cls=None, legacy_cloud=None, manager_cls=None, renderer_cls=None):
        built: dict = {}

        class Protocol:
            def __init__(self, **kw):
                built["kw"] = kw
                self.connected = connected
                self.cloud = legacy_cloud
                self.device = None

        monkeypatch.setattr(client_mod, "_bootstrap_protocol", lambda ref: None)
        monkeypatch.setattr(client_mod, "_protocol_cls", Protocol)
        monkeypatch.setattr(client_mod, "_cloud_cls", cloud_cls)
        monkeypatch.setattr(client_mod, "_map_manager_cls", manager_cls)
        monkeypatch.setattr(client_mod, "_map_renderer_cls", renderer_cls)
        return built

    return _wire


def _records(*records):
    return {"page": {"records": list(records)}}


# -- _connect_sync -------------------------------------------------------------------


def test_a_failed_bootstrap_means_not_connected(wire, monkeypatch):
    def broken(ref):
        raise RuntimeError("no reference clone")

    wire(connected=True)
    monkeypatch.setattr(client_mod, "_bootstrap_protocol", broken)
    assert DreameHomeClient(ip="10.0.0.2")._connect_sync() is False


def test_no_protocol_class_means_not_connected(wire, monkeypatch):
    wire(connected=True)
    monkeypatch.setattr(client_mod, "_protocol_cls", None)
    assert DreameHomeClient(ip="10.0.0.2")._connect_sync() is False


def test_the_local_protocol_is_built_for_udp_with_the_null_token(wire):
    built = wire(connected=True)
    c = DreameHomeClient(username="u", password="p", country="de", ip="10.0.0.2", did=42, auth_key="AK")
    assert c._connect_sync() is True
    assert built["kw"] == {
        "ip": "10.0.0.2",
        "token": "0" * 32,
        "username": "u",
        "password": "p",
        "country": "de",
        "auth_key": "AK",
        "device_id": "42",
        "prefer_cloud": False,
    }


def test_the_local_protocol_gets_no_device_id_when_none_is_set(wire):
    built = wire(connected=True)
    DreameHomeClient(ip="10.0.0.2")._connect_sync()
    assert built["kw"]["device_id"] is None


def test_without_a_local_link_or_a_cloud_login_it_is_not_connected(wire):
    wire(connected=False)
    assert DreameHomeClient(ip="10.0.0.2")._connect_sync() is False


def test_a_local_link_alone_connects_without_trying_the_cloud(wire):
    cloud = _cloud_class()
    wire(connected=True, cloud_cls=cloud)
    c = DreameHomeClient(password="p", ip="10.0.0.2")
    assert c._connect_sync() is True
    assert cloud.made == []  # type: ignore[attr-defined]
    assert c._dreame_cloud is None


def test_the_dreame_cloud_is_made_for_a_dreame_account(wire):
    cloud = _cloud_class(devices=None)
    wire(cloud_cls=cloud)
    c = DreameHomeClient(username="u", password="p", country="de", did="42", auth_key="AK")
    assert c._connect_sync() is True
    (made,) = cloud.made  # type: ignore[attr-defined]
    assert made.args == ("u", "p")
    assert made.kw == {"account_type": "dreame", "country": "de", "auth_key": "AK", "did": "42"}
    assert c._dreame_cloud is made


def test_the_dreame_cloud_needs_a_password(wire):
    cloud = _cloud_class()
    wire(connected=True, cloud_cls=cloud)
    DreameHomeClient(username="u")._connect_sync()
    assert cloud.made == []  # type: ignore[attr-defined]


def test_an_old_reference_cloud_is_made_with_its_own_arguments(wire):
    cloud = _cloud_class(old_style=True)
    wire(cloud_cls=cloud)
    c = DreameHomeClient(username="u", password="p", country="de", did="42", auth_key="AK")
    assert c._connect_sync() is True
    (made,) = cloud.made  # type: ignore[attr-defined]
    assert made.kw == {"country": "de", "auth_key": "AK", "device_id": "42"}


def test_the_robot_is_picked_by_its_ip_address(wire):
    cloud = _cloud_class(
        devices=_records(
            {"did": "111", "localip": "10.0.0.9", "model": "dreame.vacuum.other", "bindDomain": "a:1"},
            {"did": "222", "localip": "10.0.0.2", "model": "dreame.vacuum.mine", "bindDomain": "b:2"},
        )
    )
    wire(cloud_cls=cloud)
    c = DreameHomeClient(username="u", password="p", ip="10.0.0.2")
    assert c._connect_sync() is True
    made = cloud.made[0]  # type: ignore[attr-defined]
    assert c._did == "222"
    assert c._model == "dreame.vacuum.mine"
    assert made._did == "222"
    assert made._host == "b:2"
    assert made.info_calls == 1


def test_the_robot_is_picked_by_its_device_id_when_the_ip_is_unknown(wire):
    cloud = _cloud_class(
        devices=_records({"did": "111", "localip": "10.0.0.9"}, {"did": "222", "localip": "10.0.0.8", "model": "m2"})
    )
    wire(cloud_cls=cloud)
    c = DreameHomeClient(username="u", password="p", ip="10.0.0.2", did="222")
    c._connect_sync()
    assert c._model == "m2"


def test_the_first_robot_is_used_when_nothing_matches(wire):
    cloud = _cloud_class(devices=_records({"did": "111", "model": "m1"}, {"did": "222", "model": "m2"}))
    wire(cloud_cls=cloud)
    c = DreameHomeClient(username="u", password="p", ip="10.0.0.2", did="999")
    c._connect_sync()
    assert (c._did, c._model) == ("111", "m1")


def test_a_plain_list_of_devices_is_accepted(wire):
    cloud = _cloud_class(devices=[{"did": "333", "model": "m3"}])
    wire(cloud_cls=cloud)
    c = DreameHomeClient(username="u", password="p")
    c._connect_sync()
    assert (c._did, c._model) == ("333", "m3")


def test_an_empty_device_page_leaves_the_robot_unpicked(wire):
    cloud = _cloud_class(devices={"page": {"records": None}})
    wire(cloud_cls=cloud)
    c = DreameHomeClient(username="u", password="p", did="42")
    assert c._connect_sync() is True
    assert c._did == "42"
    assert cloud.made[0].info_calls == 0  # type: ignore[attr-defined]


def test_a_device_record_without_a_bind_domain_keeps_the_default_host(wire):
    cloud = _cloud_class(devices=[{"did": "333"}])
    wire(cloud_cls=cloud)
    DreameHomeClient(username="u", password="p")._connect_sync()
    assert cloud.made[0]._host == "api.example"  # type: ignore[attr-defined]


def test_a_device_record_without_a_did_keeps_the_configured_one(wire):
    cloud = _cloud_class(devices=[{"model": "m4"}])
    wire(cloud_cls=cloud)
    c = DreameHomeClient(username="u", password="p", did="42")
    c._connect_sync()
    assert (c._did, c._model) == ("42", "m4")
    assert cloud.made[0]._did == "42"  # type: ignore[attr-defined]


def test_a_failing_device_list_still_connects_through_the_cloud(wire):
    cloud = _cloud_class(devices_error=True)
    wire(cloud_cls=cloud)
    c = DreameHomeClient(username="u", password="p", did="42")
    assert c._connect_sync() is True
    assert c._did == "42"
    assert cloud.made[0].info_calls == 0  # type: ignore[attr-defined]


def test_a_failing_device_info_call_is_ignored(wire):
    cloud = _cloud_class(devices=[{"did": "333"}], info_error=True)
    wire(cloud_cls=cloud)
    assert DreameHomeClient(username="u", password="p")._connect_sync() is True


def test_a_successful_login_clears_an_earlier_login_error(wire):
    wire(cloud_cls=_cloud_class())
    c = DreameHomeClient(username="u", password="p")
    c._cloud_error = "old reason"
    c._connect_sync()
    assert c.cloud_error is None


def test_a_failed_cloud_login_is_explained_and_not_connected(wire):
    wire(cloud_cls=_cloud_class(login_ok=False))
    c = DreameHomeClient(username="u", password="p")
    assert c._connect_sync() is False
    assert c.cloud_error is not None
    assert c.cloud_error.startswith("cloud login failed")


def test_a_failed_cloud_login_still_connects_over_the_local_link(wire):
    wire(connected=True, cloud_cls=_cloud_class(login_ok=False))
    c = DreameHomeClient(username="u", password="p", ip="10.0.0.2")
    assert c._connect_sync() is True
    assert c.cloud_error is not None


def test_the_legacy_cloud_is_tried_when_there_is_no_dreame_cloud_class(wire):
    legacy = LegacyCloud(login_ok=False, last_code="70016")
    wire(legacy_cloud=legacy)
    c = DreameHomeClient(username="u", password="p")
    assert c._connect_sync() is False
    assert legacy.logins == 1
    assert c.cloud_error is not None and "70016" in c.cloud_error


def test_a_legacy_cloud_login_alone_does_not_count_as_connected(wire):
    legacy = LegacyCloud(login_ok=True)
    wire(legacy_cloud=legacy)
    assert DreameHomeClient(username="u", password="p")._connect_sync() is False
    assert legacy.logins == 1


def test_without_a_username_the_legacy_cloud_is_not_tried(wire):
    legacy = LegacyCloud(login_ok=True)
    wire(connected=True, legacy_cloud=legacy)
    DreameHomeClient(ip="10.0.0.2")._connect_sync()
    assert legacy.logins == 0


def test_the_map_manager_is_built_on_the_protocol(wire):
    class Manager:
        def __init__(self, protocol):
            self.protocol = protocol

    wire(connected=True, manager_cls=Manager)
    c = DreameHomeClient(ip="10.0.0.2")
    c._connect_sync()
    assert isinstance(c._map_manager, Manager)
    assert c._map_manager.protocol is c._protocol


def test_a_failing_map_manager_does_not_stop_the_connection(wire):
    def broken(protocol):
        raise RuntimeError("no map")

    wire(connected=True, manager_cls=broken)
    c = DreameHomeClient(ip="10.0.0.2")
    assert c._connect_sync() is True
    assert c._map_manager is None


def test_the_map_renderer_is_built_small_and_cached(wire):
    class Renderer:
        def __init__(self, **kw):
            self.kw = kw

    wire(connected=True, renderer_cls=Renderer)
    c = DreameHomeClient(ip="10.0.0.2")
    c._connect_sync()
    assert c._map_renderer.kw == {"low_resolution": True, "cache": True}  # type: ignore[union-attr]


def test_an_old_map_renderer_is_built_without_arguments(wire):
    class Renderer:
        def __init__(self):
            self.plain = True

    wire(connected=True, renderer_cls=Renderer)
    c = DreameHomeClient(ip="10.0.0.2")
    c._connect_sync()
    assert c._map_renderer.plain  # type: ignore[union-attr]


def test_a_failing_map_renderer_does_not_stop_the_connection(wire):
    def broken(**kw):
        raise RuntimeError("no renderer")

    wire(connected=True, renderer_cls=broken)
    c = DreameHomeClient(ip="10.0.0.2")
    assert c._connect_sync() is True
    assert c._map_renderer is None


def test_an_existing_map_renderer_is_kept(wire):
    wire(connected=True, renderer_cls=lambda **kw: "new")
    c = DreameHomeClient(ip="10.0.0.2")
    c._map_renderer = "kept"
    c._connect_sync()
    assert c._map_renderer == "kept"


# -- the local socket timeout ----------------------------------------------------------


def _with_device(timeout, ip="10.0.0.2"):
    c = DreameHomeClient(ip=ip)
    c._protocol = SimpleNamespace(device=SimpleNamespace(_timeout=timeout))
    return c


def test_a_short_local_timeout_is_raised_to_ten_seconds():
    c = _with_device(2)
    c._apply_local_miot_tuning()
    assert c._protocol.device._timeout == 10  # type: ignore[union-attr]


def test_a_long_local_timeout_is_kept():
    c = _with_device(30)
    c._apply_local_miot_tuning()
    assert c._protocol.device._timeout == 30  # type: ignore[union-attr]


def test_an_unreadable_local_timeout_becomes_ten_seconds():
    c = _with_device("slow")
    c._apply_local_miot_tuning()
    assert c._protocol.device._timeout == 10  # type: ignore[union-attr]


def test_the_local_timeout_is_left_alone_without_an_ip():
    c = _with_device(2, ip=None)
    c._apply_local_miot_tuning()
    assert c._protocol.device._timeout == 2  # type: ignore[union-attr]


# -- why a login failed ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("cloud", "expected"),
    [
        (None, "no cloud protocol available"),
        (SimpleNamespace(verification_url="https://verify"), "2FA/verification required"),
        (SimpleNamespace(captcha_img="img"), "captcha required"),
        (SimpleNamespace(last_code=70016), "70016 = wrong username/password"),
        (SimpleNamespace(_last_code="70002"), "does not exist (70002)"),
        (SimpleNamespace(last_code="87001"), "rate-limited (87001)"),
        (SimpleNamespace(last_code=87001), "rate-limited (87001)"),
        (SimpleNamespace(last_code="12345"), "Xiaomi error code 12345"),
        (SimpleNamespace(), "unknown Xiaomi auth error"),
    ],
    ids=[
        "no-cloud",
        "2fa",
        "captcha",
        "wrong-password",
        "no-account",
        "rate-limited",
        "rate-limited-int",
        "other-code",
        "no-code",
    ],
)
def test_a_failed_login_is_explained(cloud, expected):
    assert expected in _classify_cloud_failure(cloud)


# -- client_from_env ------------------------------------------------------------------

_SETTINGS = (
    "DREAME_USER",
    "DREAME_PASSWORD",
    "DREAME_COUNTRY",
    "DREAME_IP",
    "DREAME_TOKEN",
    "DREAME_DID",
    "DREAME_AUTH_KEY",
    "DREAME_REF_PATH",
)


@pytest.fixture
def env(monkeypatch):
    """An environment with no Dreame settings, and a .env loader that reads nothing."""
    for name in _SETTINGS:
        monkeypatch.delenv(name, raising=False)
    loaded: list = []
    monkeypatch.setattr(client_mod, "load_dotenv", lambda *a, **k: loaded.append(a))
    return loaded


def test_no_login_and_no_ip_gives_no_client(env):
    assert client_from_env() is None


def test_a_user_without_a_password_gives_no_client(env, monkeypatch):
    monkeypatch.setenv("DREAME_USER", "me@example.com")
    assert client_from_env() is None


def test_an_ip_alone_gives_a_local_client(env, monkeypatch):
    monkeypatch.setenv("DREAME_IP", " 10.0.0.2 ")
    c = client_from_env()
    assert c is not None
    assert (c._ip, c._username, c._password) == ("10.0.0.2", None, None)
    assert c._token == "0" * 32
    assert c._country == "eu"
    assert (c._did, c._auth_key) == (None, None)
    assert c._ref_path == client_mod._REF_DEFAULT


def test_every_setting_is_read_and_trimmed(env, monkeypatch, tmp_path):
    for name, value in {
        "DREAME_USER": " me@example.com ",
        "DREAME_PASSWORD": " pw ",
        "DREAME_COUNTRY": " de ",
        "DREAME_TOKEN": " " + "ab" * 16 + " ",
        "DREAME_DID": " 42 ",
        "DREAME_AUTH_KEY": " AK ",
        "DREAME_REF_PATH": f" {tmp_path} ",
    }.items():
        monkeypatch.setenv(name, value)
    c = client_from_env()
    assert c is not None
    assert (c._username, c._password, c._country) == ("me@example.com", "pw", "de")
    assert (c._token, c._did, c._auth_key, c._ip) == ("ab" * 16, "42", "AK", None)
    assert c._ref_path == tmp_path


def test_the_repo_env_file_is_read_when_the_environment_has_no_login(env, monkeypatch):
    monkeypatch.setattr(client_mod.Path, "is_file", lambda self: True)
    client_from_env()
    assert len(env) == 2
    (repo_env,) = env[1]
    assert repo_env == Path(client_mod.__file__).resolve().parent.parent.parent / ".env"


def test_the_repo_env_file_is_not_read_when_the_environment_has_a_login(env, monkeypatch):
    monkeypatch.setattr(client_mod.Path, "is_file", lambda self: True)
    monkeypatch.setenv("DREAME_USER", "me@example.com")
    client_from_env()
    assert env == [()]


def test_a_password_file_alone_is_used_with_the_environment_user(env, monkeypatch, tmp_path):
    (tmp_path / "password").write_text("filepw\n", encoding="utf-8")
    (tmp_path / "password").chmod(0o600)
    monkeypatch.setenv("DREAME_USER", "me@example.com")
    monkeypatch.setenv("DREAME_PASSWORD", "envpw")
    c = client_from_env(tmp_path)
    assert c is not None
    assert (c._username, c._password) == ("me@example.com", "filepw")


# -- loading the reference code -------------------------------------------------------


def _ref_tree(root: Path, *, protocol: str, map_py: str) -> Path:
    dreame = root / "custom_components" / "dreame_vacuum" / "dreame"
    dreame.mkdir(parents=True)
    (dreame / "exceptions.py").write_text("class DeviceException(Exception):\n    pass\n")
    for name in ("types.py", "const.py", "resources.py"):
        (dreame / name).write_text("")
    (dreame / "protocol.py").write_text(protocol)
    (dreame / "map.py").write_text(map_py)
    return root


_NEW_PROTOCOL = "class DreameVacuumProtocol:\n    pass\n\nclass DreameVacuumDreameHomeCloudProtocol:\n    pass\n"
_OLD_PROTOCOL = "class DreameVacuumProtocol:\n    pass\n\nclass DreameVacuumCloudProtocol:\n    pass\n"
_NEW_MAP = (
    "class DreameMapVacuumMapManager:\n    pass\n\n"
    "class DreameVacuumMapDecoder:\n    pass\n\n"
    "class DreameVacuumMapRenderer:\n    pass\n"
)


@pytest.fixture
def fresh_bootstrap(monkeypatch):
    """No reference loaded yet; the reference modules are put back afterwards."""
    for name in ("_protocol_cls", "_cloud_cls", "_map_manager_cls", "_map_decoder_cls", "_map_renderer_cls"):
        monkeypatch.setattr(client_mod, name, None)
    monkeypatch.setattr(client_mod, "_REF_CANDIDATES", [])
    saved = {k: v for k, v in sys.modules.items() if k.startswith("custom_components")}
    for k in saved:
        del sys.modules[k]
    yield
    for k in [k for k in sys.modules if k.startswith("custom_components")]:
        del sys.modules[k]
    sys.modules.update(saved)


def test_bootstrap_loads_the_protocol_cloud_and_map_classes(fresh_bootstrap, tmp_path):
    ref = _ref_tree(tmp_path, protocol=_NEW_PROTOCOL, map_py=_NEW_MAP)
    client_mod._bootstrap_protocol(ref)
    assert client_mod._protocol_cls.__name__ == "DreameVacuumProtocol"  # type: ignore[union-attr]
    assert client_mod._cloud_cls.__name__ == "DreameVacuumDreameHomeCloudProtocol"  # type: ignore[union-attr]
    assert client_mod._map_manager_cls.__name__ == "DreameMapVacuumMapManager"  # type: ignore[union-attr]
    assert client_mod._map_decoder_cls.__name__ == "DreameVacuumMapDecoder"  # type: ignore[union-attr]
    assert client_mod._map_renderer_cls.__name__ == "DreameVacuumMapRenderer"  # type: ignore[union-attr]
    pkg = sys.modules["custom_components.dreame_vacuum.dreame"]
    assert pkg.VERSION == "dreame-mcp-adapter"  # type: ignore[attr-defined]
    assert pkg.DeviceException.__name__ == "DeviceException"  # type: ignore[attr-defined]


def test_an_old_reference_gives_its_older_class_names(fresh_bootstrap, tmp_path):
    ref = _ref_tree(tmp_path, protocol=_OLD_PROTOCOL, map_py="class DreameVacuumMapManager:\n    pass\n")
    client_mod._bootstrap_protocol(ref)
    assert client_mod._cloud_cls.__name__ == "DreameVacuumCloudProtocol"  # type: ignore[union-attr]
    assert client_mod._map_manager_cls.__name__ == "DreameVacuumMapManager"  # type: ignore[union-attr]
    assert client_mod._map_decoder_cls is None


def test_a_broken_map_module_leaves_map_rendering_off(fresh_bootstrap, tmp_path):
    ref = _ref_tree(tmp_path, protocol=_NEW_PROTOCOL, map_py="raise ImportError('no py_mini_racer')\n")
    client_mod._bootstrap_protocol(ref)
    assert client_mod._protocol_cls is not None
    assert (client_mod._map_manager_cls, client_mod._map_decoder_cls, client_mod._map_renderer_cls) == (
        None,
        None,
        None,
    )


def test_bootstrap_is_done_only_once(fresh_bootstrap, monkeypatch):
    monkeypatch.setattr(client_mod, "_protocol_cls", object)

    def never(ref):
        raise AssertionError("looked for the reference again")

    monkeypatch.setattr(client_mod, "_resolve_ref_dir", never)
    client_mod._bootstrap_protocol(None)


def test_a_reference_with_the_dreame_cloud_is_preferred(monkeypatch, tmp_path):
    old = _ref_tree(tmp_path / "old", protocol=_OLD_PROTOCOL, map_py="")
    new = _ref_tree(tmp_path / "new", protocol=_NEW_PROTOCOL, map_py="")
    monkeypatch.setattr(client_mod, "_REF_CANDIDATES", [new])
    assert client_mod._resolve_ref_dir(old) == new


def test_an_old_reference_is_used_when_it_is_the_only_one(monkeypatch, tmp_path):
    old = _ref_tree(tmp_path / "old", protocol=_OLD_PROTOCOL, map_py="")
    monkeypatch.setattr(client_mod, "_REF_CANDIDATES", [tmp_path / "missing"])
    assert client_mod._resolve_ref_dir(old) == old


def test_no_reference_anywhere_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(client_mod, "_REF_CANDIDATES", [tmp_path / "missing"])
    with pytest.raises(RuntimeError, match="DREAME_REF_PATH"):
        client_mod._resolve_ref_dir(None)


def test_a_reference_without_a_protocol_file_has_no_dreame_cloud(tmp_path):
    assert client_mod._ref_has_dreamehome(tmp_path) is False


def test_an_unreadable_protocol_file_counts_as_no_dreame_cloud(monkeypatch, tmp_path):
    ref = _ref_tree(tmp_path, protocol=_NEW_PROTOCOL, map_py="")

    def unreadable(self, *a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(client_mod.Path, "read_text", unreadable)
    assert client_mod._ref_has_dreamehome(ref) is False


def test_a_file_python_cannot_load_is_an_error(tmp_path):
    odd = tmp_path / "notes.txt"
    odd.write_text("")
    with pytest.raises(RuntimeError, match="Could not load module"):
        client_mod._load_module("dreame_mcp_test_notes", odd)


# -- the miio stand-in ----------------------------------------------------------------


@pytest.fixture
def miio_modules():
    saved = {k: v for k, v in sys.modules.items() if k.startswith("miio")}
    yield
    for k in [k for k in sys.modules if k.startswith("miio")]:
        del sys.modules[k]
    sys.modules.update(saved)


def test_a_loaded_miio_is_left_alone(miio_modules):
    marker = object()
    sys.modules["miio"] = marker  # type: ignore[assignment]
    client_mod._stub_miio()
    assert sys.modules["miio"] is marker


def test_a_broken_miio_is_replaced_by_a_stand_in_that_refuses_to_send(miio_modules, caplog):
    for k in [k for k in sys.modules if k.startswith("miio")]:
        del sys.modules[k]
    sys.modules["miio.miioprotocol"] = None  # type: ignore[assignment]  # import of it fails
    with caplog.at_level(logging.WARNING, logger="dreame-mcp.client"):
        client_mod._stub_miio()
    proto = sys.modules["miio.miioprotocol"].MiIOProtocol("10.0.0.2", "0" * 32)  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="python-miio"):
        proto.send("get_properties")
    assert "using stubs" in caplog.text
