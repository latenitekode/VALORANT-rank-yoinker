import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from json.decoder import JSONDecodeError
from urllib.parse import urlparse

import requests
from requests.exceptions import ConnectionError

from src.colors import color
from src.constants import PROJECT_ROOT


FORK_REPOSITORY = "latenitekode/VALORANT-rank-yoinker"
GITHUB_API = f"https://api.github.com/repos/{FORK_REPOSITORY}"
FORK_RAW = f"https://raw.githubusercontent.com/{FORK_REPOSITORY}/main"


class Requests:
    def __init__(self, version, log, Error):
        self.Error = Error
        self.version = version
        self.headers = {}
        self.log = log
        self._thread_local = threading.local()
        self._headers_lock = threading.RLock()
        self._backoff_lock = threading.RLock()
        self._host_backoff_until = {}

        self.lockfile = self.get_lockfile()
        self.region = self.get_region()
        self.pd_url = f"https://pd.{self.region[0]}.a.pvp.net"
        self.glz_url = f"https://glz-{self.region[1][0]}.{self.region[1][1]}.a.pvp.net"
        self.log(f"Api urls: pd_url: '{self.pd_url}', glz_url: '{self.glz_url}'")
        self.region = self.region[0]

        self.puuid = ""
        # Fetch PUUID so it is available to every subsystem. Header acquisition is
        # bounded; if the lockfile exists before the local API is ready, re-open it
        # once through the existing account-manager flow and try again.
        if not self.get_headers(init=True):
            self.log("Riot local auth was not ready; refreshing lockfile once")
            self.lockfile = self.get_lockfile(ignoreLockfile=True)
            if not self.get_headers(init=True):
                raise RuntimeError("Riot entitlements endpoint did not become ready")

    def _session(self):
        """One requests.Session per worker thread for keep-alive connection reuse."""
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=8, pool_maxsize=8, max_retries=0
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._thread_local.session = session
        return session

    @staticmethod
    def _version_tuple(value):
        parts = [int(part) for part in re.findall(r"\d+", str(value or ""))]
        return tuple(parts or [0])

    @staticmethod
    def check_version(version, copy_run_update_script):
        """Check releases from this fork only; never silently switch to upstream."""
        try:
            response = requests.get(
                f"{GITHUB_API}/releases?per_page=1", timeout=(2.5, 6.0)
            )
            response.raise_for_status()
            releases = response.json()
            if not releases:
                return

            latest = releases[0]
            release_version = latest.get("tag_name", "0")
            link = None
            for asset in latest.get("assets", []):
                content_type = str(asset.get("content_type") or "").lower()
                name = str(asset.get("name") or "").lower()
                if "zip" in content_type or name.endswith(".zip"):
                    link = asset.get("browser_download_url")
                    break

            if not link or Requests._version_tuple(release_version) <= Requests._version_tuple(version):
                return

            print(color("[UPDATE] New fork version available!", fore=(0, 255, 0)))
            if str(sys.argv[0]).lower().endswith(".exe"):
                while True:
                    update_now = input(
                        color("Would you like to update now? (Y/n): ", fore=(0, 255, 0))
                    )
                    answer = update_now.strip().lower()
                    if answer in ("n", "no"):
                        return
                    if answer in ("", "y", "yes"):
                        copy_run_update_script(link)
                        os._exit(1)
                    print('Please respond with "yes" or "no" ("y", "n") or press enter')
        except requests.exceptions.RequestException:
            print(
                color(
                    "[WARNING] Unable to check for updates - skipping...",
                    fore=(255, 165, 0),
                )
            )
        except Exception:
            print(
                color(
                    "[WARNING] Error checking for updates - skipping...",
                    fore=(255, 165, 0),
                )
            )

    @staticmethod
    def _safe_extract_zip(zipped, destination):
        root = os.path.realpath(destination)
        for member in zipped.infolist():
            target = os.path.realpath(os.path.join(root, member.filename))
            if target != root and not target.startswith(root + os.sep):
                raise ValueError(f"unsafe update archive path: {member.filename}")
        zipped.extractall(root)

    @staticmethod
    def copy_run_update_script(link):
        update_root = os.path.join(os.getenv("APPDATA"), "vry")
        os.makedirs(update_root, exist_ok=True)
        shutil.copyfile(
            os.path.join(PROJECT_ROOT, "updatescript.bat"),
            os.path.join(update_root, "updatescript.bat"),
        )
        response = requests.get(link, stream=True, timeout=(3.05, 30.0))
        response.raise_for_status()
        zipped = zipfile.ZipFile(io.BytesIO(response.content))
        Requests._safe_extract_zip(zipped, update_root)
        extracted_folder = os.path.join(
            update_root, ".".join(os.path.basename(link).split(".")[:-1])
        )
        subprocess.Popen(
            [
                os.path.join(update_root, "updatescript.bat"),
                extracted_folder,
                PROJECT_ROOT,
                update_root,
            ]
        )

    @staticmethod
    def check_status():
        try:
            response = requests.get(
                f"{FORK_RAW}/status.json", timeout=(2.5, 6.0)
            )
            response.raise_for_status()
            status_data = response.json()
            if not status_data["status_good"] or status_data["print_message"]:
                status_color = (
                    (255, 0, 0) if not status_data["status_good"] else (0, 255, 0)
                )
                print(color(status_data["message_to_display"], fore=status_color))
        except requests.exceptions.RequestException:
            print(
                color(
                    "[WARNING] Unable to check status - skipping...",
                    fore=(255, 165, 0),
                )
            )
        except Exception:
            print(
                color(
                    "[WARNING] Failed processing status - skipping...",
                    fore=(255, 165, 0),
                )
            )

    @staticmethod
    def _retry_after_seconds(response, default=0.5):
        value = (response.headers or {}).get("Retry-After", "")
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
        try:
            parsed = parsedate_to_datetime(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return float(default)

    @staticmethod
    def _host_key(url):
        return (urlparse(url).hostname or url).lower()

    def _active_host_backoff(self, url):
        key = self._host_key(url)
        now = time.monotonic()
        with self._backoff_lock:
            until = self._host_backoff_until.get(key, 0.0)
            if until <= now:
                self._host_backoff_until.pop(key, None)
                return 0.0
            return until - now

    def _set_host_backoff(self, url, seconds):
        key = self._host_key(url)
        seconds = min(30.0, max(0.0, float(seconds)))
        with self._backoff_lock:
            self._host_backoff_until[key] = max(
                self._host_backoff_until.get(key, 0.0), time.monotonic() + seconds
            )

    @staticmethod
    def _failed_response(endpoint, status=599, error_code="REQUEST_FAILED"):
        response = requests.Response()
        response.status_code = int(status)
        response._content = json.dumps({"errorCode": error_code}).encode("utf-8")
        response.url = endpoint
        return response

    def _failure_value(self, url_type, endpoint, status=599, error_code="REQUEST_FAILED"):
        if url_type == "pd":
            return self._failed_response(endpoint, status=status, error_code=error_code)
        if url_type == "local":
            return None
        return {"errorCode": error_code, "_http_status": int(status)}

    def fetch(self, url_type: str, endpoint: str, method: str, rate_limit_seconds=5, json_body=None):
        """Bounded Riot request wrapper with per-host storm protection.

        Historical return types are preserved: GLZ/local/custom return decoded JSON,
        while PD returns a requests.Response. Remote calls have finite connect/read
        timeouts, bounded retries, one synchronized auth refresh, and shared per-host
        cooldown after 429/5xx/network failures.
        """
        method = str(method or "get").lower()

        if url_type == "glz":
            url = self.glz_url + endpoint
            timeout = (2.5, 7.0)
        elif url_type == "pd":
            url = self.pd_url + endpoint
            timeout = (2.5, 7.0)
        elif url_type == "local":
            url = f"https://127.0.0.1:{self.lockfile['port']}{endpoint}"
            timeout = (0.8, 2.0)
        elif url_type == "custom":
            url = endpoint
            timeout = (2.5, 7.0)
        else:
            raise ValueError(f"Unknown url_type: {url_type}")

        auth_refreshed = False
        max_attempts = 3 if url_type == "local" else 2
        last_response = None

        for attempt in range(max_attempts):
            if url_type != "local":
                host_wait = self._active_host_backoff(url)
                if host_wait > 0:
                    # A very short existing cooldown may be cheaper than failing the
                    # call, but never let a worker sit behind a long global backoff.
                    if host_wait <= 0.35:
                        time.sleep(host_wait)
                    else:
                        self.log(
                            f"host backoff active for {self._host_key(url)} ({host_wait:.2f}s); "
                            "degrading this request"
                        )
                        return self._failure_value(
                            url_type, endpoint, status=429, error_code="RATE_LIMITED"
                        )

            try:
                if url_type == "local":
                    headers = {
                        "Authorization": "Basic "
                        + base64.b64encode(
                            ("riot:" + self.lockfile["password"]).encode()
                        ).decode()
                    }
                else:
                    headers = self.get_headers()
                    if not headers:
                        return self._failure_value(
                            url_type, endpoint, status=401, error_code="AUTH_UNAVAILABLE"
                        )

                response = self._session().request(
                    method,
                    url,
                    headers=headers,
                    verify=False,
                    timeout=timeout,
                    json=json_body,
                )
                last_response = response
                if endpoint != "/chat/v4/presences":
                    self.log(
                        f"fetch: url: '{url_type}', endpoint: {endpoint}, method: {method},"
                        f" response code: {response.status_code}"
                    )

                if url_type == "local":
                    try:
                        data = response.json()
                    except (JSONDecodeError, ValueError):
                        data = None
                    if response.status_code == 200 and not (
                        isinstance(data, dict) and data.get("errorCode") == "RPC_ERROR"
                    ):
                        return data
                    if attempt < max_attempts - 1:
                        time.sleep(0.12 * (attempt + 1))
                        continue
                    return None

                try:
                    data = response.json()
                except (JSONDecodeError, ValueError):
                    data = None

                if (
                    isinstance(data, dict)
                    and data.get("errorCode") == "BAD_CLAIMS"
                    and not auth_refreshed
                ):
                    self.log("detected bad claims; refreshing Riot auth once")
                    auth_refreshed = True
                    if self.get_headers(refresh=True):
                        continue
                    return self._failure_value(
                        url_type, endpoint, status=401, error_code="AUTH_UNAVAILABLE"
                    )

                # 400/404 from exact GLZ membership are normal absence signals and
                # must remain distinguishable from network/rate-limit uncertainty.
                if response.status_code in (400, 404):
                    if url_type == "pd":
                        return response
                    if not isinstance(data, dict):
                        data = {"errorCode": "RESOURCE_NOT_FOUND"}
                    data.setdefault("_http_status", response.status_code)
                    return data

                if response.ok:
                    if url_type == "pd":
                        return response
                    return data

                if response.status_code == 429 or 500 <= response.status_code <= 504:
                    default_wait = 0.6 if response.status_code == 429 else 0.35
                    wait = self._retry_after_seconds(response, default=default_wait)
                    wait = min(float(rate_limit_seconds or 5), max(0.25, wait))
                    self._set_host_backoff(url, wait)
                    if attempt < max_attempts - 1 and wait <= 2.0:
                        self.log(
                            f"transient Riot {response.status_code}; bounded retry in {wait:.2f}s"
                        )
                        time.sleep(wait)
                        continue
                    return self._failure_value(
                        url_type,
                        endpoint,
                        status=response.status_code,
                        error_code="RATE_LIMITED" if response.status_code == 429 else "SERVER_ERROR",
                    )

                self.log(f"response not ok {url_type} endpoint: {response.text[:500]}")
                if url_type == "pd":
                    return response
                if isinstance(data, dict):
                    data.setdefault("_http_status", response.status_code)
                    return data
                return self._failure_value(
                    url_type, endpoint, status=response.status_code
                )

            except (requests.exceptions.RequestException, ConnectionError) as exc:
                self.log(
                    f"request error: url_type={url_type}, endpoint={endpoint}, "
                    f"attempt={attempt + 1}/{max_attempts}: {exc}"
                )
                if url_type != "local":
                    wait = 0.30 * (attempt + 1)
                    self._set_host_backoff(url, wait)
                if attempt < max_attempts - 1:
                    time.sleep(0.15 * (attempt + 1))
                    continue

        if url_type == "pd" and last_response is not None:
            return last_response
        return self._failure_value(url_type, endpoint)

    def get_region(self):
        path = os.path.join(
            os.getenv("LOCALAPPDATA"), r"VALORANT\Saved\Logs\ShooterGame.log"
        )
        pd_url = None
        glz_url = None
        with open(path, "r", encoding="utf8") as file:
            for line in file:
                if ".a.pvp.net/account-xp/v1/" in line:
                    pd_url = line.split(".a.pvp.net/account-xp/v1/")[0].split(".")[-1]
                elif "https://glz" in line:
                    glz_bits = line.split("https://glz-")[1].split(".")
                    if len(glz_bits) >= 2:
                        glz_url = [glz_bits[0], glz_bits[1]]
                if pd_url and glz_url:
                    self.log(f"got region from logs '{[pd_url, glz_url]}'")
                    if pd_url == "pbe":
                        return ["na", ["na", "1"]]
                    return [pd_url, glz_url]
        raise RuntimeError("Unable to determine Riot region from ShooterGame.log")

    def get_current_version(self):
        path = os.path.join(
            os.getenv("LOCALAPPDATA"), r"VALORANT\Saved\Logs\ShooterGame.log"
        )
        with open(path, "r", encoding="utf8") as file:
            for line in file:
                if "CI server version:" in line:
                    version_without_shipping = line.split("CI server version: ", 1)[1].strip()
                    version = "-".join(version_without_shipping.split("-"))
                    self.log(f"got version from logs '{version}'")
                    return version
        raise RuntimeError("Unable to determine Riot client version from ShooterGame.log")

    def get_lockfile(self, ignoreLockfile=False):
        # ignoring lockfile is for when it exists but local endpoints are not ready yet
        path = os.path.join(
            os.getenv("LOCALAPPDATA"), r"Riot Games\Riot Client\Config\lockfile"
        )
        if self.Error.LockfileError(path, ignoreLockfile=ignoreLockfile):
            with open(path) as lockfile:
                self.log("opened lockfile")
                data = lockfile.read().split(":")
                keys = ["name", "PID", "port", "password", "protocol"]
                return dict(zip(keys, data))
        return None

    def get_headers(self, refresh=False, init=False):
        """Get Riot auth headers with bounded retries and synchronized refresh."""
        if self.headers and not refresh:
            return self.headers

        with self._headers_lock:
            # Another worker may have refreshed while we waited for the lock.
            if self.headers and not refresh:
                return self.headers

            max_attempts = 8 if init else 5
            entitlements = None
            for attempt in range(max_attempts):
                if not self.lockfile:
                    self.log("Riot lockfile unavailable while fetching auth headers")
                    return False

                local_headers = {
                    "Authorization": "Basic "
                    + base64.b64encode(
                        ("riot:" + self.lockfile["password"]).encode()
                    ).decode()
                }
                url = (
                    f"https://127.0.0.1:{self.lockfile['port']}"
                    "/entitlements/v1/token"
                )
                try:
                    response = requests.get(
                        url,
                        headers=local_headers,
                        verify=False,
                        timeout=(0.8, 2.5),
                    )
                    self.log(f"{url}\n{local_headers}")
                    entitlements = response.json()
                except (requests.exceptions.RequestException, ValueError) as exc:
                    self.log(
                        f"local entitlements request failed "
                        f"({attempt + 1}/{max_attempts}): {exc}"
                    )
                    try:
                        self.lockfile = self.get_lockfile()
                    except Exception:
                        pass
                    if attempt < max_attempts - 1:
                        time.sleep(min(1.0, 0.25 * (attempt + 1)))
                    continue

                message = str((entitlements or {}).get("message") or "")
                if message == "Entitlements token is not ready yet":
                    if attempt < max_attempts - 1:
                        time.sleep(0.5)
                    continue
                if message == "Invalid URI format":
                    self.log(f"Invalid uri format: {entitlements}")
                    if init:
                        return False
                    if attempt < max_attempts - 1:
                        time.sleep(0.5)
                    continue

                required = ("subject", "accessToken", "token")
                if isinstance(entitlements, dict) and all(
                    entitlements.get(key) for key in required
                ):
                    break
                self.log(f"Incomplete entitlements response: {entitlements}")
                if attempt < max_attempts - 1:
                    time.sleep(0.4)
            else:
                return False

            if not isinstance(entitlements, dict):
                return False

            self.puuid = entitlements["subject"]
            headers = {
                "Authorization": f"Bearer {entitlements['accessToken']}",
                "X-Riot-Entitlements-JWT": entitlements["token"],
                "X-Riot-ClientPlatform": (
                    "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjog"
                    "IldpbmRvd3MiLA0KCSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5"
                    "MDQyLjEuMjU2LjY0Yml0IiwNCgkicGxhdGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9"
                ),
                "X-Riot-ClientVersion": self.get_current_version(),
                "User-Agent": "ShooterGame/13 Windows/10.0.19043.1.256.64bit",
            }
            self.headers = headers
            return self.headers
