import io
import json
from email.message import Message
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from urllib.request import HTTPCookieProcessor, HTTPSHandler, build_opener
from urllib.response import addinfourl

import pytest
from rich.console import Console
from typer.testing import CliRunner

from b2t.cli import app
from b2t.config import Settings
from b2t.downloaders.ytdlp import YtDlpDownloader
from b2t.inputs import parse_source
from b2t.login import BilibiliLoginClient, LoginError, check_login, login_with_qr, render_qr, save_cookie_file


VALUES = {"SESSDATA": "FAKE_SESSION%2Cencoded", "bili_jct": "FAKE_CSRF", "DedeUserID": "12345"}


def cookie(name, value, domain=".bilibili.com"):
    return Cookie(version=0, name=name, value=value, port=None, port_specified=False,
                  domain=domain, domain_specified=True, domain_initial_dot=domain.startswith("."),
                  path="/", path_specified=True, secure=True, expires=None, discard=True,
                  comment=None, comment_url=None, rest={}, rfc2109=False)


def valid_jar():
    jar = CookieJar()
    for name, value in VALUES.items():
        jar.set_cookie(cookie(name, value))
    return jar


def client_with_responses(responses):
    """Drive real urllib CookieJar processing with synthetic HTTP responses."""
    pending = iter(responses)
    requests = []

    class Transport(HTTPSHandler):
        def https_open(self, request):
            requests.append(request)
            payload, set_cookies = next(pending)
            headers = Message()
            for value in set_cookies:
                headers.add_header("Set-Cookie", value)
            response = addinfourl(io.BytesIO(json.dumps(payload).encode()), headers, request.full_url, 200)
            response.msg = "OK"
            return response

    client = BilibiliLoginClient()
    client.opener = build_opener(Transport(), HTTPCookieProcessor(client.jar))
    return client, requests


def test_qr_protocol_reads_set_cookie_preserves_encoding_and_validates_session(tmp_path):
    client, requests = client_with_responses([
        ({"code": 0, "data": {"qrcode_key": "FAKE_QR_KEY", "url": "https://account.bilibili.com/qrcode/h5/login?key=FAKE"}}, []),
        ({"code": 0, "data": {"code": 86101}}, []),
        ({"code": 0, "data": {"code": 86090}}, []),
        ({"code": 0, "data": {"code": 0, "url": "https://passport.biligame.com/crossDomain?ticket=FAKE"}},
         [f"{name}={value}; Domain=.bilibili.com; Path=/; Secure; HttpOnly" for name, value in VALUES.items()]),
        ({"code": 0, "data": {"isLogin": True}}, []),
    ])
    assert client.generate().startswith("https://account.bilibili.com/")
    assert [client.poll(), client.poll(), client.poll()] == ["scan", "confirm", "done"]
    assert client.verify() is True
    assert "SESSDATA=FAKE_SESSION%2Cencoded" in requests[-1].get_header("Cookie")
    target = tmp_path / "cookies.txt"
    save_cookie_file(target, client.jar)
    reloaded = MozillaCookieJar(str(target))
    reloaded.load(ignore_discard=True)
    assert {c.name: c.value for c in reloaded} == VALUES
    assert target.stat().st_mode & 0o777 == 0o600


def test_legacy_callback_cookies_and_response_headers_take_precedence():
    client, requests = client_with_responses([
        ({"code": 0, "data": {"code": 0, "url":
          "https://passport.bilibili.com/callback?SESSDATA=old%2Cencoded&bili_jct=FAKE_CSRF&DedeUserID=12345"}},
         ["SESSDATA=new%2Cencoded; Domain=.bilibili.com; Path=/"]),
    ])
    client.key = "FAKE"
    assert client.poll() == "done"
    assert {c.name: c.value for c in client.jar}["SESSDATA"] == "new%2Cencoded"
    assert len(requests) == 1  # no request to the credential-bearing callback


def test_empty_or_foreign_cookies_are_rejected_in_success_response():
    client, _ = client_with_responses([
        ({"code": 0, "data": {"code": 0, "url": "https://passport.biligame.com/crossDomain?ticket=FAKE"}},
         ["SESSDATA=FOREIGN; Domain=.example.test; Path=/"]),
    ])
    with pytest.raises(LoginError) as error:
        client.poll()
    assert error.value.key == "login_missing_cookies"
    assert "SESSDATA" in error.value.details["fields"]


@pytest.mark.parametrize("payload,key", [
    ({"code": -400, "data": {}}, "login_api_error"),
    ({"code": 0, "data": None}, "login_invalid_response"),
    ({"code": 0, "data": {"qrcode_key": "FAKE", "url": "https://foreign.example/qr"}}, "login_invalid_response"),
])
def test_generate_rejects_invalid_api_responses(payload, key):
    client, _ = client_with_responses([(payload, [])])
    with pytest.raises(LoginError) as error:
        client.generate()
    assert error.value.key == key


class FakeClient:
    def __init__(self, states=("scan", "confirm", "done"), authenticated=True):
        self.jar = valid_jar()
        self.states = iter(states)
        self.authenticated = authenticated
        self.polls = 0
        self.verifications = 0

    def generate(self):
        return "https://passport.bilibili.com/FAKE_QR"

    def poll(self):
        self.polls += 1
        return next(self.states)

    def verify(self):
        self.verifications += 1
        return self.authenticated


def run_fake_login(tmp_path, monkeypatch, client, target=None, timeout=180):
    monkeypatch.setattr("b2t.login.render_qr", lambda url: "FAKE QR MATRIX")
    output = io.StringIO()
    elapsed = [0.0]

    def sleep(seconds):
        elapsed[0] += seconds

    login_with_qr(target=target or tmp_path / "cookies.txt", console=Console(file=output), language="zh-CN",
                  client=client, timeout=timeout, clock=lambda: elapsed[0], sleep=sleep)
    return output.getvalue()


def test_login_saves_only_after_confirmation_and_verified_nav(tmp_path, monkeypatch):
    target = tmp_path / "cookies.txt"
    target.write_text("OLD COOKIE")
    client = FakeClient()
    original_verify = client.verify

    def verify():
        assert target.read_text() == "OLD COOKIE"
        return original_verify()

    client.verify = verify
    output = run_fake_login(tmp_path, monkeypatch, client, target)
    assert "已扫码" in output and "登录成功" in output
    assert all(value not in output for value in VALUES.values())
    assert target.read_text().startswith("# Netscape HTTP Cookie File")
    assert not list(tmp_path.glob(".bilibili-login-*"))


@pytest.mark.parametrize("scenario,key", [("timeout", "login_expired"), ("invalid", "login_invalid_session")])
def test_failed_login_preserves_existing_cookie_file(tmp_path, monkeypatch, scenario, key):
    target = tmp_path / "cookies.txt"
    target.write_text("OLD COOKIE")
    client = FakeClient(states=("timeout",) if scenario == "timeout" else ("done",), authenticated=False)
    with pytest.raises(LoginError) as error:
        run_fake_login(tmp_path, monkeypatch, client, target)
    assert error.value.key == key
    assert target.read_text() == "OLD COOKIE"


def test_verification_network_retry_does_not_repoll_consumed_qr(tmp_path, monkeypatch):
    client = FakeClient(states=("done",))

    def verify():
        client.verifications += 1
        if client.verifications == 1:
            raise LoginError("login_request_failed", kind="TimeoutError")
        return True

    client.verify = verify
    run_fake_login(tmp_path, monkeypatch, client)
    assert client.polls == 1
    assert client.verifications == 2


def test_cookie_save_is_atomic_and_filters_foreign_credentials(tmp_path, monkeypatch):
    target = tmp_path / "cookies.txt"
    target.write_text("OLD COOKIE")
    jar = valid_jar()
    jar.set_cookie(cookie("SESSDATA", "FOREIGN", ".foreign.example"))
    jar.set_cookie(cookie("unrelated", "OTHER"))

    def interrupted_replace(*args):
        raise OSError("FAKE write error")

    with monkeypatch.context() as patch:
        patch.setattr("b2t.login.os.replace", interrupted_replace)
        with pytest.raises(OSError):
            save_cookie_file(target, jar)
    assert target.read_text() == "OLD COOKIE"
    assert not list(tmp_path.glob(".bilibili-login-*"))
    save_cookie_file(target, jar)
    assert "FOREIGN" not in target.read_text() and "OTHER" not in target.read_text()


def test_status_missing_cookie_does_not_access_network(tmp_path, monkeypatch):
    monkeypatch.setattr("b2t.login.BilibiliLoginClient", lambda *a: (_ for _ in ()).throw(AssertionError("must not use network")))
    assert check_login(tmp_path / "cookies.txt") is False
    result = CliRunner().invoke(app, ["login", "--status", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert "没有有效" in result.stdout


def test_login_cli_honors_cookie_override_and_downloader_uses_saved_file(tmp_path, monkeypatch):
    target = tmp_path / "private" / "login.txt"
    monkeypatch.setenv("B2T_COOKIE_FILE", str(target))
    monkeypatch.setattr("b2t.login.render_qr", lambda url: "FAKE QR MATRIX")
    monkeypatch.setattr("b2t.login.BilibiliLoginClient", lambda: FakeClient(states=("done",)))
    result = CliRunner().invoke(app, ["login", "--workspace", str(tmp_path / ".b2t")])
    assert result.exit_code == 0, result.output
    assert all(value not in result.output for value in VALUES.values())
    assert target.is_file()
    options = YtDlpDownloader()._build_ydl_opts(parse_source("BV19Eaa6wESG"), Settings.from_workspace(tmp_path / ".b2t"))
    assert options["cookiefile"] == str(target)
    assert not (tmp_path / ".b2t" / "cookies.txt").exists()


def test_login_cli_interrupt_preserves_cookie_file(tmp_path, monkeypatch):
    target = tmp_path / "cookies.txt"
    target.write_text("OLD COOKIE")

    def interrupted(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("b2t.login.login_with_qr", interrupted)
    result = CliRunner().invoke(app, ["login", "--workspace", str(tmp_path)])
    assert result.exit_code == 130
    assert target.read_text() == "OLD COOKIE"


def test_login_errors_do_not_print_session_or_qr_urls(tmp_path, monkeypatch):
    client = BilibiliLoginClient()

    def failed(*args, **kwargs):
        raise OSError("https://passport.bilibili.com/poll?SESSDATA=FAKE_SECRET")

    monkeypatch.setattr(client.opener, "open", failed)
    monkeypatch.setattr("b2t.login.BilibiliLoginClient", lambda: client)
    result = CliRunner().invoke(app, ["login", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert "OSError" in result.output
    assert "FAKE_SECRET" not in result.output and "SESSDATA=" not in result.output
    assert not (tmp_path / "cookies.txt").exists()


def test_rendered_qr_preserves_all_modules_and_quiet_zone():
    import qrcode

    url = "https://passport.bilibili.com/qrcode/h5/login?key=FAKE_TEST"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    expected = qr.get_matrix()
    rows = render_qr(url).splitlines()
    actual = []
    for row in rows:
        actual.extend([[char in {"▀", "█"} for char in row], [char in {"▄", "█"} for char in row]])
    assert actual[:len(expected)] == expected
    assert len(actual) <= len(expected) + 1
