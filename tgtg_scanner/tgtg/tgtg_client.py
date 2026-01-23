# Copied and modified from https://github.com/ahivert/tgtg-python

import html
import json
import logging
import random
import re
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urljoin, urlparse, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from tgtg_scanner.errors import (
    TgtgAPIError,
    TgtgConfigurationError,
    TgtgLoginError,
    TgtgPollingError,
)

log = logging.getLogger("tgtg")
BASE_URL = "https://apptoogoodtogo.com/api/"
API_ITEM_ENDPOINT = "item/v8/"
FAVORITE_ITEM_ENDPOINT = "user/favorite/v1/{}/update"
AUTH_BY_EMAIL_ENDPOINT = "auth/v5/authByEmail"
AUTH_BY_REQUEST_PIN_ENDPOINT = "auth/v5/authByRequestPin"
AUTH_POLLING_ENDPOINT = "auth/v5/authByRequestPollingId"
SIGNUP_BY_EMAIL_ENDPOINT = "auth/v5/signUpByEmail"
REFRESH_ENDPOINT = "token/v1/refresh"
ACTIVE_ORDER_ENDPOINT = "order/v8/active"
INACTIVE_ORDER_ENDPOINT = "order/v8/inactive"
CREATE_ORDER_ENDPOINT = "order/v8/create/"
ABORT_ORDER_ENDPOINT = "order/v8/{}/abort"
ORDER_STATUS_ENDPOINT = "order/v8/{}/status"
API_BUCKET_ENDPOINT = "discover/v1/bucket"
MANUFACTURERITEM_ENDPOINT = "manufactureritem/v2/"
USER_AGENTS = [
    "TGTG/{} Dalvik/2.1.0 (Linux; U; Android 9; Nexus 5 Build/M4B30Z)",
    "TGTG/{} Dalvik/2.1.0 (Linux; U; Android 10; SM-G935F Build/NRD90M)",
    "TGTG/{} Dalvik/2.1.0 (Linux; Android 12; SM-G920V Build/MMB29K)",
]
DEFAULT_ACCESS_TOKEN_LIFETIME = 3600 * 4  # 4 hours
DEFAULT_MAX_POLLING_TRIES = 24  # 24 * POLLING_WAIT_TIME = 2 minutes
DEFAULT_POLLING_WAIT_TIME = 5  # Seconds
DEFAULT_MIN_TIME_BETWEEN_REQUESTS = 15  # Seconds
DEFAULT_APK_VERSION = "24.11.0"

APK_RE_SCRIPT = re.compile(r"AF_initDataCallback\({key:\s*'ds:5'.*?data:([\s\S]*?), sideChannel:.+<\/script")


class TgtgSession(requests.Session):
    http_adapter = HTTPAdapter(
        max_retries=Retry(
            total=5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            backoff_factor=1,
        )
    )

    correlation_id = str(uuid.uuid4())

    last_api_request: datetime | None = None

    # DataDome cookie cache
    _datadome_cache_cookie: str | None = None
    _datadome_cache_expires_at: float | None = None
    _datadome_cache_duration_s: int = 5 * 60  # 5 minutes

    def __init__(
        self,
        user_agent: str | None = None,
        apk_version: str | None = None,
        language: str = "en-UK",
        timeout: int | None = None,
        proxies: dict | None = None,
        base_url: str = BASE_URL,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.mount("https://", self.http_adapter)
        self.mount("http://", self.http_adapter)
        self.headers = {
            "Accept-Language": language,
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Accept-Encoding": "gzip",
            "x-correlation-id": self.correlation_id,
        }
        if user_agent:
            self.headers["User-Agent"] = user_agent
        self.timeout = timeout
        self.apk_version = apk_version
        self.user_agent = user_agent
        if proxies:
            self.proxies = proxies
        self._base_url = base_url

    def send(self, request: requests.PreparedRequest, *args, **kwargs) -> requests.Response:
        if self.last_api_request:
            wait = max(0, DEFAULT_MIN_TIME_BETWEEN_REQUESTS - (datetime.now() - self.last_api_request).seconds)
            log.debug(f"Waiting {wait} seconds.")
            time.sleep(wait)

        response = super().send(request, *args, **kwargs)
        self.last_api_request = datetime.now()
        return response

    def post(self, *args, access_token: str | None = None, **kwargs) -> requests.Response:
        if "headers" not in kwargs:
            kwargs["headers"] = self.headers
        if access_token:
            kwargs["headers"]["authorization"] = f"Bearer {access_token}"
        return super().post(*args, **kwargs)

    def request(self, method, url, **kwargs):
        time.sleep(1)
        for key in ["timeout", "proxies"]:
            val = kwargs.get(key)
            if val is None and hasattr(self, key):
                kwargs[key] = getattr(self, key)
        # Ensure DataDome cookie exists BEFORE request gets prepared (so Cookie header includes it)
        try:
            self._ensure_datadome_cookie_for_url(url, headers=kwargs.get("headers"))
        except Exception as e:
            log.debug("DataDome auto-fetch failed (continuing without): %s", e)
        return super().request(method, url, **kwargs)

    def _ensure_datadome_cookie_for_url(self, url: str, headers: dict | None = None) -> None:
        # If caller already set Cookie header with datadome, do nothing
        if headers:
            ch = headers.get("Cookie") or headers.get("cookie")
            if ch and "datadome=" in ch:
                return
        if self._datadome_cache_valid() or ("datadome" in self.cookies):
            return

        cid = self._generate_datadome_cid()
        dd = self._fetch_datadome_cookie(request_url=url, cid=cid)
        if dd:
            self._set_datadome_cookie_value(dd)

    def _datadome_cache_valid(self) -> bool:
        if not self._datadome_cache_cookie or not self._datadome_cache_expires_at:
            return False
        return time.time() < self._datadome_cache_expires_at

    @staticmethod
    def _generate_datadome_cid() -> str:
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789~_"
        return "".join(random.choice(chars) for _ in range(120))

    def _set_datadome_cookie_value(self, cookie_value: str) -> None:
        domain = urlsplit(self._base_url).hostname
        domain = f".{'local' if domain == 'localhost' else domain}"
        self.cookies.set("datadome", cookie_value, domain=domain, path="/", secure=True)
        self._datadome_cache_cookie = cookie_value
        self._datadome_cache_expires_at = time.time() + self._datadome_cache_duration_s

    def invalidate_datadome_cache(self) -> None:
        self._datadome_cache_cookie = None
        self._datadome_cache_expires_at = None
        # also remove cookie from jar (best-effort)
        try:
            if "datadome" in self.cookies:
                del self.cookies["datadome"]
        except Exception:
            pass

    def _ensure_datadome_cookie(self, request) -> None:
        # If request already has a Cookie header containing datadome, do nothing
        cookie_header = request.headers.get("Cookie") or request.headers.get("cookie")
        if cookie_header and "datadome=" in cookie_header:
            return
        # If cookie jar already contains datadome and it's fresh enough, do nothing
        if self._datadome_cache_valid():
            if "datadome" not in self.cookies and self._datadome_cache_cookie:
                self._set_datadome_cookie_value(self._datadome_cache_cookie)
            return
        if "datadome" in self.cookies:
            # cache it (even if we didn't fetch it ourselves)
            self._datadome_cache_cookie = self.cookies.get("datadome")
            self._datadome_cache_expires_at = time.time() + self._datadome_cache_duration_s
            return

        # Fetch a new DataDome cookie from the SDK endpoint
        request_url = request.url
        cid = self._generate_datadome_cid()
        datadome_cookie_value = self._fetch_datadome_cookie(
            request_url=str(request_url),
            cid=cid,
        )
        if datadome_cookie_value:
            self._set_datadome_cookie_value(datadome_cookie_value)

    def _fetch_datadome_cookie(self, request_url: str, cid: str) -> str | None:
        params = {
            "camera": '{"auth":"true", "info":"{\\"front\\":\\"2000x1500\\",\\"back\\":\\"5472x3648\\"}"}',
            "cid": cid,
            "ddk": "1D42C2CA6131C526E09F294FE96F94",
            "ddv": "3.0.4",
            "ddvc": self.apk_version,
            "events": '[{"id":1,"message":"response validation","source":"sdk","date":' + str(int(time.time() * 1000)) + "}]",
            "inte": "android-java-okhttp",
            "mdl": "Pixel 7 Pro",
            "os": "Android",
            "osn": "UPSIDE_DOWN_CAKE",
            "osr": "14",
            "osv": "34",
            "request": request_url,
            "screen_d": "3.5",
            "screen_x": "1440",
            "screen_y": "3120",
            "ua": self.user_agent,
        }
        url = "https://api-sdk.datadome.co/sdk/"
        try:
            r = requests.post(
                url,
                data=params,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "*/*",
                    "User-Agent": self.user_agent,
                    "Accept-Encoding": "gzip, deflate, br",
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") == 200 and data.get("cookie"):
                m = re.search(r"datadome=([^;]+)", data["cookie"])
                if m:
                    return m.group(1)  # store raw value; cookie jar will format header
        except Exception as e:
            log.debug("Error fetching DataDome cookie: %s", e)
        return None


class TgtgClient:
    def __init__(
        self,
        base_url=BASE_URL,
        email=None,
        access_token=None,
        refresh_token=None,
        datadome_cookie=None,
        apk_version=None,
        user_agent=None,
        language="en-GB",
        proxies=None,
        timeout=None,
        port=0,
        access_token_lifetime=DEFAULT_ACCESS_TOKEN_LIFETIME,
        max_polling_tries=DEFAULT_MAX_POLLING_TRIES,
        polling_wait_time=DEFAULT_POLLING_WAIT_TIME,
        device_type="ANDROID",
    ):
        if base_url != BASE_URL:
            log.warning("Using custom tgtg base url: %s", base_url)

        self.base_url = base_url

        self.email = email
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.datadome_cookie = datadome_cookie

        self.last_time_token_refreshed = None
        self.access_token_lifetime = access_token_lifetime
        self.max_polling_tries = max_polling_tries
        self.polling_wait_time = polling_wait_time

        self.device_type = device_type
        self.apk_version = apk_version
        self.fixed_user_agent = user_agent
        self.user_agent = user_agent
        self.language = language
        self.proxies = proxies
        self.timeout = timeout
        self.session = None
        self.port = port

        self.captcha_error_count = 0

    def __del__(self) -> None:
        if self.session:
            self.session.close()

    def _get_url(self, path) -> str:
        return urljoin(self.base_url, path)

    def _create_session(self) -> TgtgSession:
        if not self.user_agent:
            self.user_agent = self._get_user_agent()
        return TgtgSession(
            self.user_agent,
            self.apk_version,
            self.language,
            self.timeout,
            self.proxies,
            self.base_url,
        )

    def get_credentials(self) -> dict:
        """Returns current tgtg api credentials.

        Returns:
            dict: Dictionary containing access token, refresh token and user id

        """
        self.login()
        return {
            "email": self.email,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "datadome_cookie": self.datadome_cookie,
        }

    def _post(self, path, **kwargs) -> requests.Response:
        if not self.session:
            self.session = self._create_session()
        response = self.session.post(
            self._get_url(path),
            access_token=self.access_token,
            **kwargs,
        )
        self.datadome_cookie = self.session.cookies.get("datadome")
        if response.status_code in (HTTPStatus.OK, HTTPStatus.ACCEPTED):
            self.captcha_error_count = 0
            return response
        # Status Code == 403
        # --> Blocked due to rate limit / wrong user_agent.
        # 1. Try: Get latest APK Version from google
        # 2. Try: Reset session
        # 3. Try: Delete datadome cookie and reset session
        # 10.Try: Sleep 10 minutes, and reset session
        if response.status_code == 403:
            log.debug("Captcha Error 403!")
            self.captcha_error_count += 1
            # If we had a DataDome cookie, invalidate it so the next retry fetches a new one
            if self.session:
                self.session.invalidate_datadome_cache()
                time.sleep(0.5)
            if self.captcha_error_count == 1:
                self.user_agent = self._get_user_agent()
            elif self.captcha_error_count == 2:
                self.session = self._create_session()
            elif self.captcha_error_count == 4:
                self.datadome_cookie = None
                self.session = self._create_session()
            elif self.captcha_error_count >= 10:
                log.warning("Too many captcha Errors! Sleeping for 10 minutes...")
                time.sleep(10 * 60)
                log.info("Retrying ...")
                self.captcha_error_count = 0
                self.session = self._create_session()
            time.sleep(3)
            return self._post(path, **kwargs)
        raise TgtgAPIError(response.status_code, response.content)

    def _get_user_agent(self) -> str:
        if self.fixed_user_agent:
            return self.fixed_user_agent
        version = DEFAULT_APK_VERSION
        if self.apk_version is None:
            try:
                version = self.get_latest_apk_version()
            except Exception:
                log.warning("Failed to get latest APK version!")
        else:
            version = self.apk_version
        log.debug("Using APK version %s.", version)
        return random.choice(USER_AGENTS).format(version)

    @staticmethod
    def get_latest_apk_version() -> str:
        """Returns latest APK version of the official Android TGTG App.

        Returns:
            str: APK Version string

        """
        response = requests.get(
            "https://play.google.com/store/apps/details?id=com.app.tgtg&hl=en&gl=US",
            timeout=30,
        )
        match = APK_RE_SCRIPT.search(response.text)
        if not match:
            raise TgtgAPIError("Failed to get latest APK version from Google Play Store.")
        data = json.loads(match.group(1))
        return data[1][2][140][0][0][0]

    @property
    def _already_logged(self) -> bool:
        return bool(self.access_token and self.refresh_token)

    def _refresh_token(self) -> None:
        if (
            self.last_time_token_refreshed
            and (datetime.now() - self.last_time_token_refreshed).seconds <= self.access_token_lifetime
        ):
            return
        response = self._post(REFRESH_ENDPOINT, json={"refresh_token": self.refresh_token})
        self.access_token = response.json().get("access_token")
        self.refresh_token = response.json().get("refresh_token")
        self.last_time_token_refreshed = datetime.now()

    def login(self) -> None:
        if not (self.email or self.access_token and self.refresh_token):
            raise TgtgConfigurationError("You must provide at least email or access_token and refresh_token")
        if self._already_logged:
            self._refresh_token()
        else:
            log.info("Starting login process ...")
            response = self._post(
                AUTH_BY_EMAIL_ENDPOINT,
                json={
                    "device_type": self.device_type,
                    "email": self.email,
                },
            )
            first_login_response = response.json()
            if first_login_response["state"] == "TERMS":
                raise TgtgPollingError(
                    f"This email {self.email} is not linked to a tgtg account. Please signup with this email first."
                )
            if first_login_response.get("state") == "WAIT":
                pin = prompt_via_browser("Paste your pin:", title="Pin Input", port=self.port)
                self.start_polling(first_login_response.get("polling_id"), pin)
            else:
                raise TgtgLoginError(response.status_code, response.content)

    def auth_by_request_pin(self, polling_id: str, pin: str) -> None:
        """Finish login using numeric code (PIN) from email, via authByRequestPin.

        Mirrors node-toogoodtogo-watcher PR #282 behavior. :contentReference[oaicite:11]{index=11}
        """
        response = self._post(
            AUTH_BY_REQUEST_PIN_ENDPOINT,
            json={
                "device_type": self.device_type,
                "email": self.email,
                "request_pin": pin,
                "request_polling_id": polling_id,
            },
        )
        if response.status_code == HTTPStatus.OK:
            log.info("Logged in (PIN)!")
            login_response = response.json()
            self.access_token = login_response.get("access_token")
            self.refresh_token = login_response.get("refresh_token")
            self.last_time_token_refreshed = datetime.now()
            return
        raise TgtgLoginError(response.status_code, response.content)

    def start_polling(self, polling_id: str, request_pin: str | None = None) -> None:
        # If a pin is provided, do a single authByRequestPin call instead of polling.
        if request_pin:
            return self.auth_by_request_pin(polling_id, request_pin)
        for _ in range(self.max_polling_tries):
            response = self._post(
                AUTH_POLLING_ENDPOINT,
                json={
                    "device_type": self.device_type,
                    "email": self.email,
                    "request_polling_id": polling_id,
                },
            )
            if response.status_code == HTTPStatus.ACCEPTED:
                log.warning(
                    "Check your mailbox on PC to continue... (Mailbox on mobile won't work, if you have installed tgtg app.)"
                )
                time.sleep(self.polling_wait_time)
                continue
            if response.status_code == HTTPStatus.OK:
                log.info("Logged in!")
                login_response = response.json()
                self.access_token = login_response.get("access_token")
                self.refresh_token = login_response.get("refresh_token")
                self.last_time_token_refreshed = datetime.now()
                return
        raise TgtgPollingError("Max polling retries reached. Try again.")

    def get_items(
        self,
        *,
        latitude=0.0,
        longitude=0.0,
        radius=21,
        page_size=20,
        page=1,
        discover=False,
        favorites_only=True,
        item_categories=None,
        diet_categories=None,
        pickup_earliest=None,
        pickup_latest=None,
        search_phrase=None,
        with_stock_only=False,
        hidden_only=False,
        we_care_only=False,
    ) -> list[dict]:
        self.login()
        # fields are sorted like in the app
        data = {
            "origin": {"latitude": latitude, "longitude": longitude},
            "radius": radius,
            "page_size": page_size,
            "page": page,
            "discover": discover,
            "favorites_only": favorites_only,
            "item_categories": item_categories if item_categories else [],
            "diet_categories": diet_categories if diet_categories else [],
            "pickup_earliest": pickup_earliest,
            "pickup_latest": pickup_latest,
            "search_phrase": search_phrase if search_phrase else None,
            "with_stock_only": with_stock_only,
            "hidden_only": hidden_only,
            "we_care_only": we_care_only,
        }
        response = self._post(API_ITEM_ENDPOINT, json=data)
        return response.json().get("items", [])

    def get_item(self, item_id: str) -> dict:
        self.login()
        response = self._post(
            f"{API_ITEM_ENDPOINT}/{item_id}",
            json={"origin": None},
        )
        return response.json()

    def get_favorites(self) -> list[dict]:
        """Returns favorites of the current tgtg account.

        Returns:
            List: List of items

        """
        items = []
        page = 1
        page_size = 100
        while True:
            new_items = self.get_items(favorites_only=True, page_size=page_size, page=page)
            items += new_items
            if len(new_items) < page_size:
                break
            page += 1
        return items

    def set_favorite(self, item_id: str, is_favorite: bool) -> None:
        self.login()
        self._post(
            FAVORITE_ITEM_ENDPOINT.format(item_id),
            json={"is_favorite": is_favorite},
        )

    def create_order(self, item_id: str, item_count: int) -> dict[str, str]:
        self.login()
        response = self._post(f"{CREATE_ORDER_ENDPOINT}/{item_id}", json={"item_count": item_count})
        if response.json().get("state") != "SUCCESS":
            raise TgtgAPIError(response.status_code, response.content)
        return response.json().get("order", {})

    def get_order_status(self, order_id: str) -> dict[str, str]:
        self.login()
        response = self._post(ORDER_STATUS_ENDPOINT.format(order_id))
        return response.json()

    def abort_order(self, order_id: str) -> None:
        """Use this when your order is not yet paid."""
        self.login()
        response = self._post(ABORT_ORDER_ENDPOINT.format(order_id), json={"cancel_reason_id": 1})
        if response.json().get("state") != "SUCCESS":
            raise TgtgAPIError(response.status_code, response.content)

    def get_manufactureritems(self) -> dict:
        self.login()
        response = self._post(
            MANUFACTURERITEM_ENDPOINT,
            json={
                "action_types_accepted": ["QUERY"],
                "display_types_accepted": ["LIST", "FILL"],
                "element_types_accepted": [
                    "ITEM",
                    "HIGHLIGHTED_ITEM",
                    "MANUFACTURER_STORY_CARD",
                    "DUO_ITEMS",
                    "DUO_ITEMS_V2",
                    "TEXT",
                    "PARCEL_TEXT",
                    "NPS",
                    "SMALL_CARDS_CAROUSEL",
                    "ITEM_CARDS_CAROUSEL",
                ],
            },
        )
        return response.json()


def prompt_via_browser(prompt="Enter value:", title="Input required", port=0):
    done = threading.Event()
    result = {"value": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs):
            # silence default logging
            pass

        def _send(self, code, body, content_type="text/html; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/submit"):
                qs = parse_qs(urlparse(self.path).query)
                result["value"] = (qs.get("value", [""])[0]).strip()
                self._send(200, "<h3>You can close this tab.</h3>")
                done.set()
                return

            page = f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>{html.escape(title)}</title></head>
<body style="font-family: system-ui; padding: 2rem;">
  <h2>{html.escape(prompt)}</h2>
  <form action="/submit" method="get">
    <input name="value" autofocus style="font-size: 1.1rem; padding: .4rem; width: 28rem; max-width: 90vw;" />
    <button type="submit" style="font-size: 1.1rem; padding: .4rem .8rem;">OK</button>
  </form>
</body>
</html>"""
            self._send(200, page)

    # Bind to ephemeral port
    server = HTTPServer(("0.0.0.0", port), Handler)
    url = f"http://localhost:{server.server_port}/"

    # Run server in background thread; stop after first submission
    def serve_until_done():
        while not done.is_set():
            server.handle_request()

    t = threading.Thread(target=serve_until_done, daemon=True)
    t.start()

    log.info(f"Enter Pin: {url}")

    done.wait()  # block until user submits
    server.server_close()
    return result["value"]
