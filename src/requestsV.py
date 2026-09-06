import base64
import json
import time
import threading
from json.decoder import JSONDecodeError
import requests
from src.colors import color
import os
import shutil
import sys
import zipfile
import io
import subprocess
from requests.exceptions import ConnectionError
from src.constants import PROJECT_ROOT

class Requests:
    def __init__(self, version, log, Error):
        self.Error = Error
        self.version = version
        self.headers = {}
        self.log = log
        self._thread_local = threading.local()

        self.lockfile = self.get_lockfile()
        self.region = self.get_region()
        self.pd_url = f"https://pd.{self.region[0]}.a.pvp.net"
        self.glz_url = f"https://glz-{self.region[1][0]}.{self.region[1][1]}.a.pvp.net"
        self.log(f"Api urls: pd_url: '{self.pd_url}', glz_url: '{self.glz_url}'")
        self.region = self.region[0]
        
        self.puuid = ''
        #fetch puuid so its avaible outside
        if not self.get_headers(init=True):
            self.log("Invalid URI format, invalid lockfile, going back to menu")
            self.get_lockfile(ignoreLockfile=True)
        


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
    def check_version(version, copy_run_update_script):
        # checking for latest release
        try:
            r = requests.get("https://api.github.com/repos/mdevio/VALORANT-rank-yoinker/releases")
        except requests.exceptions.RequestException:
            print(color("[WARNING] Unable to check for updates - skipping...", fore=(255, 165, 0)))
            return

        try:
            json_data = r.json()
            release_version = json_data[0]["tag_name"]  # get release version
            for asset in json_data[0]["assets"]:
                if "zip" in asset["content_type"]:
                        link = asset["browser_download_url"] # link for the latest release
                        break
            if float(release_version) > float(version):
                print(color("[UPDATE] New version available!", fore=(0, 255, 0)))
                if sys.argv[0][-3:] == "exe":
                    while True:
                        update_now = input(color("Would you like to update now? (Y/n): ", fore=(0, 255, 0)))
                        if update_now.lower() == "n" or update_now.lower() == "no":
                            return
                        elif update_now.lower() == "y" or update_now.lower() == "yes" or update_now == "":
                            copy_run_update_script(link)
                            os._exit(1)
                        else:
                            print('Please respond with "yes" or "no" ("y", "n") or press enter')
        except Exception:
            print(color("[WARNING] Error checking for updates - skipping...", fore=(255, 165, 0)))
            return

    @staticmethod
    def copy_run_update_script(link):
        try:
            os.mkdir(os.path.join(os.getenv('APPDATA'), "vry"))
        except FileExistsError:
            pass
        shutil.copyfile(os.path.join(PROJECT_ROOT, "updatescript.bat"), os.path.join(os.getenv('APPDATA'), "vry", "updatescript.bat"))
        r_zip = requests.get(link, stream=True)
        z = zipfile.ZipFile(io.BytesIO(r_zip.content))
        z.extractall(os.path.join(os.getenv('APPDATA'), "vry"))
        subprocess.Popen([os.path.join(os.getenv('APPDATA'), "vry", "updatescript.bat"), os.path.join(os.getenv('APPDATA'), "vry", ".".join(os.path.basename(link).split(".")[:-1])), PROJECT_ROOT, os.path.join(os.getenv('APPDATA'), "vry")])

    @staticmethod
    def check_status():
        # checking status
        try:
            rStatus = requests.get(
                "https://raw.githubusercontent.com/mdevio/VALORANT-rank-yoinker/main/status.json")
        except requests.exceptions.RequestException:
            print(color("[WARNING] Unable to check status - skipping...", fore=(255, 165, 0)))
            return

        try:
            status_data = rStatus.json()
            if not status_data["status_good"] or status_data["print_message"]:
                status_color = (255, 0, 0) if not status_data["status_good"] else (0, 255, 0)
                print(color(status_data["message_to_display"], fore=status_color))
        except Exception:
            print(color("[WARNING] Failed processing status - skipping...", fore=(255, 165, 0)))
            return
            
    def fetch(self, url_type: str, endpoint: str, method: str, rate_limit_seconds=5):
        """Bounded Riot request wrapper.

        Keeps VRY's historical return types (GLZ/local -> decoded JSON, PD ->
        Response) while removing unbounded recursive retries and long sleeps.
        This is especially important during PREGAME -> INGAME where one slow
        player request used to hold the whole scoreboard.
        """
        method = str(method or "get").lower()

        def failed_response(status=599, body=b"{}"):
            r = requests.Response()
            r.status_code = int(status)
            r._content = body
            r.url = endpoint
            return r

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
            try:
                if url_type == "local":
                    local_headers = {
                        'Authorization': 'Basic ' + base64.b64encode(
                            ('riot:' + self.lockfile['password']).encode()
                        ).decode()
                    }
                    headers = local_headers
                else:
                    headers = self.get_headers()

                response = self._session().request(
                    method, url, headers=headers, verify=False, timeout=timeout
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
                    except JSONDecodeError:
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
                except JSONDecodeError:
                    data = None

                if isinstance(data, dict) and data.get("errorCode") == "BAD_CLAIMS" and not auth_refreshed:
                    self.log("detected bad claims; refreshing Riot auth once")
                    self.headers = {}
                    auth_refreshed = True
                    continue

                # Membership endpoints use 400/404 as normal "not in this phase"
                # signals. Return them immediately rather than sleeping/retrying.
                if response.status_code in (400, 404):
                    if url_type == "pd":
                        return response
                    return data if isinstance(data, dict) else {"errorCode": "RESOURCE_NOT_FOUND"}

                if response.ok:
                    if url_type == "pd":
                        return response
                    return data

                if response.status_code == 429 or 500 <= response.status_code <= 504:
                    if attempt < max_attempts - 1:
                        retry_after = response.headers.get("Retry-After", "")
                        try:
                            wait = min(2.0, max(0.25, float(retry_after)))
                        except (TypeError, ValueError):
                            wait = 0.35 * (attempt + 1)
                        self.log(
                            f"transient Riot {response.status_code}; bounded retry in {wait:.2f}s"
                        )
                        time.sleep(wait)
                        continue

                self.log(f"response not ok {url_type} endpoint: {response.text[:500]}")
                if url_type == "pd":
                    return response
                return data if isinstance(data, dict) else {"errorCode": "REQUEST_FAILED"}

            except (requests.exceptions.RequestException, ConnectionError) as exc:
                self.log(
                    f"request error: url_type={url_type}, endpoint={endpoint}, "
                    f"attempt={attempt + 1}/{max_attempts}: {exc}"
                )
                if attempt < max_attempts - 1:
                    time.sleep(0.15 * (attempt + 1))
                    continue

        if url_type == "pd":
            return last_response if last_response is not None else failed_response()
        if url_type == "local":
            return None
        return {"errorCode": "REQUEST_FAILED"}

    def get_region(self):
        path = os.path.join(os.getenv('LOCALAPPDATA'), R'VALORANT\Saved\Logs\ShooterGame.log')
        with open(path, "r", encoding="utf8") as file:
            while True:
                line = file.readline()
                if '.a.pvp.net/account-xp/v1/' in line:
                    pd_url = line.split('.a.pvp.net/account-xp/v1/')[0].split('.')[-1]
                elif 'https://glz' in line:
                    glz_url = [(line.split('https://glz-')[1].split(".")[0]),
                               (line.split('https://glz-')[1].split(".")[1])]
                if "pd_url" in locals().keys() and "glz_url" in locals().keys():
                    self.log(f"got region from logs '{[pd_url, glz_url]}'")
                    if pd_url == "pbe":
                        return ["na", "na-1", "na"]
                    return [pd_url, glz_url]

    def get_current_version(self):
        path = os.path.join(os.getenv('LOCALAPPDATA'), R'VALORANT\Saved\Logs\ShooterGame.log')
        with open(path, "r", encoding="utf8") as file:
            while True:
                line = file.readline()
                if 'CI server version:' in line:
                    version_without_shipping = line.split('CI server version: ')[1].strip()
                    version = version_without_shipping.split("-")
                    version = "-".join(version)
                    self.log(f"got version from logs '{version}'")
                    return version

    def get_lockfile(self, ignoreLockfile=False):
        #ignoring lockfile is for when lockfile exists but it's not really valid, (local endpoints are not initialized yet)
        path = os.path.join(os.getenv('LOCALAPPDATA'), R'Riot Games\Riot Client\Config\lockfile')
        
        if self.Error.LockfileError(path, ignoreLockfile=ignoreLockfile):
            with open(path) as lockfile:
                self.log("opened lockfile")
                data = lockfile.read().split(':')
                keys = ['name', 'PID', 'port', 'password', 'protocol']
                return dict(zip(keys, data))


    def get_headers(self, refresh=False, init=False):
        if self.headers == {} or refresh:
            try_again = True
            while try_again:
                local_headers = {'Authorization': 'Basic ' + base64.b64encode(
                    ('riot:' + self.lockfile['password']).encode()).decode()}
                try:
                    response = requests.get(f"https://127.0.0.1:{self.lockfile['port']}/entitlements/v1/token",
                                            headers=local_headers, verify=False)
                    self.log(f"https://127.0.0.1:{self.lockfile['port']}/entitlements/v1/token\n{local_headers}")
                except ConnectionError:
                    self.log(f"https://127.0.0.1:{self.lockfile['port']}/entitlements/v1/token\n{local_headers}")
                    self.log("Connection error, retrying in 1 seconds, getting new lockfile")
                    time.sleep(1)
                    self.lockfile = self.get_lockfile()
                    continue
                entitlements = response.json()
                if entitlements.get("message") == "Entitlements token is not ready yet":
                    try_again = True
                    time.sleep(1)
                elif entitlements.get("message") == "Invalid URI format":
                    self.log(f"Invalid uri format: {entitlements}")
                    if init:
                        return False
                    else:
                        try_again = True
                        time.sleep(5)
                else:
                    try_again = False

            self.puuid = entitlements['subject']
            headers = {
                'Authorization': f"Bearer {entitlements['accessToken']}",
                'X-Riot-Entitlements-JWT': entitlements['token'],
                'X-Riot-ClientPlatform': "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjog"
                                         "IldpbmRvd3MiLA0KCSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5"
                                         "MDQyLjEuMjU2LjY0Yml0IiwNCgkicGxhdGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9",
                'X-Riot-ClientVersion': self.get_current_version(),
                "User-Agent": "ShooterGame/13 Windows/10.0.19043.1.256.64bit"
            }
            self.headers = headers
        return self.headers
