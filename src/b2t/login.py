from __future__ import annotations

import copy
import json
import os
import tempfile
import time
import warnings
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from rich.console import Console

from b2t.i18n import tr


COOKIE_NAMES = {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "buvid3", "buvid4"}
REQUIRED_COOKIES = {"SESSDATA", "bili_jct", "DedeUserID"}
PASSPORT_BASE = "https://passport.bilibili.com/x/passport-login/web/qrcode"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


class LoginError(RuntimeError):
    """Public errors contain only translation keys and non-secret details."""

    def __init__(self, key: str, **details):
        self.key = key
        self.details = details
        super().__init__(key)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _bilibili_cookie(cookie: Cookie) -> bool:
    domain = cookie.domain.lstrip(".").lower()
    return cookie.name in COOKIE_NAMES and bool(cookie.value) and (
        domain == "bilibili.com" or domain.endswith(".bilibili.com")
    )


def _cookie_values(jar: CookieJar) -> dict[str, str]:
    return {cookie.name: cookie.value for cookie in jar if _bilibili_cookie(cookie) and not cookie.is_expired()}


def _require_cookies(jar: CookieJar) -> None:
    missing = REQUIRED_COOKIES - _cookie_values(jar).keys()
    if missing:
        raise LoginError("login_missing_cookies", fields=", ".join(sorted(missing)))


class BilibiliLoginClient:
    """Web QR login with a session cookie jar; no browser credential access."""

    def __init__(self, jar: CookieJar | None = None):
        self.jar = jar if jar is not None else CookieJar()
        handlers = [HTTPCookieProcessor(self.jar), _NoRedirect()]
        if os.getenv("B2T_USE_PROXY", "").strip().lower() not in {"1", "true", "yes", "on"}:
            handlers.append(ProxyHandler({}))
        self.opener = build_opener(*handlers)
        self.key = ""

    def _request(self, url: str, params: dict[str, str] | None = None) -> dict:
        if params:
            url += "?" + urlencode(params)
        try:
            with self.opener.open(Request(url, headers=HEADERS), timeout=15) as response:
                payload = json.load(response)
        except Exception as error:
            # Exception text and callback URLs can contain session credentials.
            raise LoginError("login_request_failed", kind=type(error).__name__) from None
        if not isinstance(payload, dict):
            raise LoginError("login_invalid_response")
        return payload

    def _passport_data(self, action: str, params: dict[str, str]) -> dict:
        payload = self._request(f"{PASSPORT_BASE}/{action}", params)
        if payload.get("code") != 0:
            code = payload.get("code")
            raise LoginError("login_api_error", code=code if type(code) is int else "invalid")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LoginError("login_invalid_response")
        return data

    def generate(self) -> str:
        data = self._passport_data("generate", {"source": "main-fe-header"})
        key, url = data.get("qrcode_key"), data.get("url")
        if not isinstance(key, str) or not key or not isinstance(url, str) or not url:
            raise LoginError("login_invalid_response")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"passport.bilibili.com", "account.bilibili.com"}:
            raise LoginError("login_invalid_response")
        self.key = key
        return url

    def poll(self) -> str:
        data = self._passport_data("poll", {"qrcode_key": self.key, "source": "main-fe-header"})
        code = data.get("code")
        states = {86101: "scan", 86090: "confirm", 86038: "timeout"}
        if code in states:
            return states[code]
        if code != 0:
            raise LoginError("login_api_error", code=code if type(code) is int else "invalid")

        # Set-Cookie is captured by HTTPCookieProcessor. Older responses instead
        # carried credentials in callback query parameters. Never follow this URL
        # or decode the values: SESSDATA may intentionally be percent-encoded.
        callback = data.get("url", "")
        if isinstance(callback, str):
            existing = _cookie_values(self.jar)
            for part in urlsplit(callback).query.split("&"):
                name, _, value = part.partition("=")
                name = unquote(name)
                if name in COOKIE_NAMES and value and name not in existing:
                    self.jar.set_cookie(Cookie(
                        version=0, name=name, value=value, port=None, port_specified=False,
                        domain=".bilibili.com", domain_specified=True, domain_initial_dot=True,
                        path="/", path_specified=True, secure=True, expires=None, discard=True,
                        comment=None, comment_url=None, rest={}, rfc2109=False,
                    ))
        _require_cookies(self.jar)
        return "done"

    def verify(self) -> bool:
        data = self._request(NAV_URL)
        return data.get("code") == 0 and isinstance(data.get("data"), dict) and data["data"].get("isLogin") is True


def save_cookie_file(target: Path, jar: CookieJar) -> None:
    """Replace an existing Netscape file only after obtaining valid credentials."""
    _require_cookies(jar)
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".bilibili-login-", dir=target.parent)
    os.close(fd)
    try:
        exported = MozillaCookieJar(temporary)
        for cookie in jar:
            if _bilibili_cookie(cookie) and not cookie.is_expired():
                exported.set_cookie(copy.copy(cookie))
        exported.save(ignore_discard=True)
        os.chmod(temporary, 0o600)
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def render_qr(url: str) -> str:
    try:
        import qrcode
    except ImportError:
        raise LoginError("login_qr_dependency_missing") from None
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    if len(matrix) % 2:
        matrix.append([False] * len(matrix[0]))
    blocks = (" ", "▀", "▄", "█")
    return "\n".join(
        "".join(blocks[int(top) + 2 * int(bottom)] for top, bottom in zip(matrix[row], matrix[row + 1]))
        for row in range(0, len(matrix), 2)
    )


def _verify_with_retries(client: BilibiliLoginClient, console: Console, language: str, sleep: Callable) -> None:
    for attempt in range(3):
        try:
            if not client.verify():
                raise LoginError("login_invalid_session")
            return
        except LoginError as error:
            if error.key != "login_request_failed" or attempt == 2:
                raise
            console.print(tr(language, "login_retry"), markup=False)
            sleep(2.5)


def login_with_qr(
    *, target: Path, console: Console, language: str, timeout: int = 180,
    client: BilibiliLoginClient | None = None,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> None:
    client = client or BilibiliLoginClient()
    console.print(tr(language, "login_generating"), markup=False)
    url = client.generate()
    # Bilibili's QR lifetime starts at generation, before terminal rendering.
    deadline = clock() + timeout
    rendered = render_qr(url)
    console.print(tr(language, "login_scan", seconds=timeout), markup=False)
    console.print(rendered, style="black on white", markup=False, highlight=False, soft_wrap=True)
    previous = ""
    failures = 0
    while clock() < deadline:
        try:
            state = client.poll()
        except LoginError as error:
            if error.key != "login_request_failed":
                raise
            failures += 1
            if failures >= 3:
                raise
            console.print(tr(language, "login_retry"), markup=False)
            sleep(2.5)
            continue
        failures = 0
        if state == "done":
            # Once DONE is returned the QR key may become invalid: retry only
            # verification, never poll the consumed key again.
            _verify_with_retries(client, console, language, sleep)
            save_cookie_file(target, client.jar)
            console.print(tr(language, "login_saved", path=str(target.expanduser().resolve())), markup=False)
            return
        if state == "timeout":
            break
        if state != previous:
            console.print(tr(language, "login_confirm" if state == "confirm" else "login_waiting"), markup=False)
            previous = state
        sleep(min(2.5, max(0.0, deadline - clock())))
    raise LoginError("login_expired")


def check_login(target: Path) -> bool:
    jar = MozillaCookieJar(str(target.expanduser()))
    try:
        with warnings.catch_warnings():
            # Malformed Netscape files can trigger cookiejar warnings containing
            # raw input lines. Report only the safe public error below.
            warnings.simplefilter("ignore", UserWarning)
            jar.load(ignore_discard=True)
    except FileNotFoundError:
        return False
    except Exception:
        raise LoginError("login_cookie_file_invalid") from None
    if "SESSDATA" not in _cookie_values(jar):
        return False
    return BilibiliLoginClient(jar).verify()
