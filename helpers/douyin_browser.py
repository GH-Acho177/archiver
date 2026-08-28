"""Enumerate Douyin user posts through an Archiver-owned Edge profile."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websockets

_PROFILE_LOCK = threading.Lock()
_SESSION_ACTIVE = False
_BROWSER_PROCESS: subprocess.Popen | None = None
_BROWSER_PORT: int | None = None
_LAST_ENUMERATION_END = 0.0
_ACCOUNT_COOLDOWN_SECONDS = 2.0
_SCROLL_INTERVAL_SECONDS = 1.5


def begin_browser_session() -> None:
    """Keep one Edge instance alive for all accounts in the current sync."""
    global _SESSION_ACTIVE
    _SESSION_ACTIVE = True


async def _close_browser(port: int | None, process: subprocess.Popen | None) -> None:
    if port:
        try:
            targets = await _json_url(f"http://127.0.0.1:{port}/json/list", 2)
            target = next((item for item in targets if item.get("type") == "page"), None)
            if target:
                websocket = await websockets.connect(target["webSocketDebuggerUrl"])
                cdp = _Cdp(websocket)
                try:
                    await cdp.call("Browser.close")
                except Exception:
                    pass
                await cdp.close()
        except Exception:
            pass
    if process and process.poll() is None:
        try:
            await asyncio.to_thread(process.wait, 5)
        except subprocess.TimeoutExpired:
            process.terminate()


def end_browser_session() -> None:
    """Close the shared Edge instance after the complete sync job."""
    global _SESSION_ACTIVE, _BROWSER_PROCESS, _BROWSER_PORT
    _SESSION_ACTIVE = False
    process, port = _BROWSER_PROCESS, _BROWSER_PORT
    _BROWSER_PROCESS = None
    _BROWSER_PORT = None
    if process or port:
        asyncio.run(_close_browser(port, process))


def _edge_executable() -> str:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("msedge")
    if found:
        return found
    raise RuntimeError("Microsoft Edge was not found")


def default_profile_dir() -> Path:
    # v1 accumulated stale Douyin site state that can leave profile headers
    # usable while every Works-list request fails. Keep it untouched and use a
    # clean generation; configured cookies are injected into this profile.
    return Path.cwd() / "config" / "douyin_edge_profile_v2"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _json_url(url: str, timeout: float = 15.0):
    def read():
        with urllib.request.urlopen(url, timeout=2) as response:
            return json.load(response)
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            return await asyncio.to_thread(read)
        except Exception:
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError("Edge did not open its debugging connection")
            await asyncio.sleep(0.25)


async def _new_page(port: int, url: str) -> dict:
    """Create a dedicated browser target for one account enumeration."""
    encoded = urllib.parse.quote(url, safe="")

    def create():
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?{encoded}", method="PUT"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    target = await asyncio.to_thread(create)
    if not target.get("webSocketDebuggerUrl"):
        raise RuntimeError("Edge did not create a controllable account page")
    return target


async def _close_page(port: int, target_id: str) -> None:
    if not target_id:
        return
    def close():
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=2
        ) as response:
            response.read()
    try:
        await asyncio.to_thread(close)
    except Exception:
        pass


async def _close_restored_douyin_pages(port: int) -> None:
    """Remove tabs restored after Edge previously exited uncleanly."""
    try:
        targets = await _json_url(f"http://127.0.0.1:{port}/json/list", 3)
    except Exception:
        return
    for target in targets:
        if (
            target.get("type") == "page"
            and "douyin.com/" in str(target.get("url") or "")
        ):
            await _close_page(port, str(target.get("id") or ""))


class _Cdp:
    def __init__(self, websocket):
        self.websocket = websocket
        self.counter = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for raw in self.websocket:
                message = json.loads(raw)
                if "id" in message:
                    future = self.pending.pop(message["id"], None)
                    if future and not future.done():
                        future.set_result(message)
                elif "method" in message:
                    await self.events.put(message)
        except BaseException as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)

    async def call(self, method: str, params: dict | None = None):
        self.counter += 1
        call_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[call_id] = future
        await self.websocket.send(json.dumps({
            "id": call_id, "method": method, "params": params or {},
        }))
        response = await asyncio.wait_for(future, 15)
        if "error" in response:
            raise RuntimeError(response["error"].get("message", str(response["error"])))
        return response.get("result", {})

    async def close(self):
        self.reader.cancel()
        await self.websocket.close()


def _extract_page(payload: dict) -> tuple[list[dict], bool]:
    items = payload.get("aweme_list") or []
    return ([item for item in items if isinstance(item, dict)], bool(payload.get("has_more")))


def _browser_cookies(cookie_header: str) -> list[dict]:
    cookies = []
    for part in cookie_header.split(";"):
        name, separator, value = part.strip().partition("=")
        if not separator or not name:
            continue
        cookies.append({
            "name": name,
            "value": value,
            "domain": ".douyin.com",
            "path": "/",
            "secure": True,
        })
    return cookies


async def fetch_user_posts(
    sec_user_id: str,
    stop_check=None,
    progress_callback=None,
    completion_callback=None,
    cookie_header: str = "",
    explicit_pagination: bool = False,
    full_scan: bool = False,
    profile_dir: Path | None = None,
    idle_seconds: float = 25.0,
    timeout_seconds: float = 120.0,
):
    """Yield pages captured from the authenticated Douyin web application."""
    global _BROWSER_PROCESS, _BROWSER_PORT, _LAST_ENUMERATION_END
    # Chromium does not allow two processes to use one profile. Sync may run
    # several account workers, so browser-backed Douyin enumeration is queued.
    while not _PROFILE_LOCK.acquire(blocking=False):
        if stop_check and stop_check():
            return
        await asyncio.sleep(0.25)
    # A fresh profile navigation immediately after the preceding account is a
    # common trigger for Douyin's in-page "service exception" response.
    account_cooldown = 8.0 if full_scan else _ACCOUNT_COOLDOWN_SECONDS
    cooldown = account_cooldown - (time.monotonic() - _LAST_ENUMERATION_END)
    while cooldown > 0:
        if stop_check and stop_check():
            _PROFILE_LOCK.release()
            return
        await asyncio.sleep(min(0.25, cooldown))
        cooldown = account_cooldown - (
            time.monotonic() - _LAST_ENUMERATION_END
        )

    profile = (profile_dir or default_profile_dir()).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    shared = _SESSION_ACTIVE
    process = _BROWSER_PROCESS if shared else None
    port = _BROWSER_PORT if shared else None
    browser_started = not process or process.poll() is not None or not port
    if browser_started:
        port = _free_port()
        command = [
            _edge_executable(),
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--disable-background-mode",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--start-minimized",
            "--new-window",
            "--window-size=1280,900",
            "about:blank",
        ]
        try:
            process = subprocess.Popen(command)
        except OSError as exc:
            _PROFILE_LOCK.release()
            raise RuntimeError(f"Could not start Edge: {exc}") from exc
        if shared:
            _BROWSER_PROCESS, _BROWSER_PORT = process, port

    cdp = None
    target_id = ""
    completed = False
    seen: set[str] = set()
    try:
        await _json_url(f"http://127.0.0.1:{port}/json/list")
        # Also clean stale profile tabs when reconnecting to an Edge process
        # that survived an earlier stopped or crashed sync.
        await _close_restored_douyin_pages(port)
        account_url = f"https://www.douyin.com/user/{sec_user_id}"
        print(f"[browser] Opening Douyin account: {sec_user_id}")
        # Enter through the site root first. Directly opening many signed
        # profile URLs in fresh tabs is more likely to receive Douyin's
        # generic in-page service-exception response.
        target = await _new_page(port, "about:blank")
        target_id = str(target.get("id") or "")
        websocket = await websockets.connect(
            target["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024
        )
        cdp = _Cdp(websocket)
        await cdp.call("Network.enable", {"maxTotalBufferSize": 100_000_000})
        await cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
        await cdp.call("Page.enable")
        browser_cookies = _browser_cookies(cookie_header)
        if browser_cookies:
            await cdp.call("Network.setCookies", {"cookies": browser_cookies})
            print(
                f"[browser] Applied {len(browser_cookies)} configured "
                "Douyin cookies to Edge"
            )
        await cdp.call("Page.navigate", {"url": "https://www.douyin.com/"})
        await asyncio.sleep(3.0)
        navigation = await cdp.call("Page.navigate", {"url": account_url})
        if navigation.get("errorText"):
            raise RuntimeError(
                f"Edge could not open the requested Douyin account: "
                f"{navigation['errorText']}"
            )
        await asyncio.sleep(3.0)
        works_tab = await cdp.call("Runtime.evaluate", {
            "expression": """
                (() => {
                  const visible = node => {
                    const box = node.getBoundingClientRect();
                    return box.width > 0 && box.height > 0;
                  };
                  const nodes = [...document.querySelectorAll(
                    '[role=tab], button, a, div, span'
                  )];
                  const target = nodes.find(node =>
                    visible(node)
                    && node.getAttribute('role') === 'tab'
                    && node.textContent.trim() === '作品'
                  );
                  if (!target) return false;
                  target.click();
                  return {
                    clicked: true,
                    tag: target.tagName,
                    role: target.getAttribute('role') || '',
                    className: String(target.className || '').slice(0, 160),
                    y: Math.round(target.getBoundingClientRect().y),
                  };
                })()
            """,
            "returnByValue": True,
        })
        selected_tab = works_tab.get("result", {}).get("value")
        if selected_tab:
            print(f"[browser] Selected Douyin Works tab: {selected_tab}")
        else:
            print("[browser] Works tab was not found; using the profile default")

        started = asyncio.get_running_loop().time()
        last_page = started
        # Full archive requests many consecutive cursor pages. Driving the
        # bottom sentinel every 1.5 seconds, independently of responses, can
        # stack requests and trigger Douyin's in-page service exception.
        # Pace each Full scroll from the preceding list response instead.
        scroll_interval = 5.0 if full_scan else _SCROLL_INTERVAL_SECONDS
        next_scroll_at = started + scroll_interval
        yielded = False
        server_has_more = True
        explicit_started = False
        while asyncio.get_running_loop().time() - started < timeout_seconds:
            if stop_check and stop_check():
                break
            try:
                event = await asyncio.wait_for(cdp.events.get(), 1.0)
            except asyncio.TimeoutError:
                event = None
            if event and event["method"] == "Network.responseReceived":
                response = event["params"].get("response", {})
                url = response.get("url", "")
                if "/aweme/v1/web/aweme/post/" in url:
                    try:
                        body = await cdp.call("Network.getResponseBody", {
                            "requestId": event["params"]["requestId"]
                        })
                        payload = json.loads(body.get("body", "{}"))
                        page, has_more = _extract_page(payload)
                        server_has_more = has_more
                        if full_scan:
                            next_scroll_at = (
                                asyncio.get_running_loop().time()
                                + scroll_interval
                            )
                    except Exception:
                        page, has_more = [], True
                    fresh = [item for item in page
                             if str(item.get("aweme_id", "")) not in seen]
                    for item in fresh:
                        seen.add(str(item.get("aweme_id", "")))
                    if fresh:
                        yielded = True
                        last_page = asyncio.get_running_loop().time()
                        if progress_callback:
                            progress_callback(len(seen), None, "scanning")
                        yield fresh
                    if explicit_pagination and has_more and not explicit_started:
                        explicit_started = True
                        cursor = payload.get("max_cursor")
                        request_url = url
                        user_agent_result = await cdp.call("Runtime.evaluate", {
                            "expression": "navigator.userAgent",
                            "returnByValue": True,
                        })
                        user_agent = str(
                            user_agent_result.get("result", {}).get("value", "")
                        )
                        from f2.utils.abogus import ABogus
                        abogus = ABogus(user_agent=user_agent)
                        cursor_history = {str(cursor)}
                        while has_more and not (stop_check and stop_check()):
                            if cursor is None:
                                print(
                                    "[browser] Douyin Full pagination stopped: "
                                    "response omitted the next cursor"
                                )
                                break
                            parsed_url = urllib.parse.urlsplit(request_url)
                            query = urllib.parse.parse_qsl(
                                parsed_url.query, keep_blank_values=True
                            )
                            query = [
                                (key, value) for key, value in query
                                if key not in {
                                    "a_bogus", "X-Bogus", "max_cursor",
                                    "sec_user_id",
                                }
                            ]
                            query.extend([
                                ("sec_user_id", sec_user_id),
                                ("max_cursor", str(cursor)),
                            ])
                            unsigned_query = urllib.parse.urlencode(query)
                            signed_query = abogus.generate_abogus(unsigned_query)[0]
                            signed_url = urllib.parse.urlunsplit((
                                parsed_url.scheme,
                                parsed_url.netloc,
                                parsed_url.path,
                                signed_query,
                                "",
                            ))
                            replay = await cdp.call("Runtime.evaluate", {
                                "expression": f"""
                                    (async () => {{
                                      const response = await fetch({json.dumps(signed_url)}, {{
                                        credentials: 'include',
                                        headers: {{'accept': 'application/json, text/plain, */*'}},
                                      }});
                                      return {{
                                        ok: response.ok,
                                        status: response.status,
                                        contentType: response.headers.get('content-type') || '',
                                        finalUrl: response.url,
                                        text: await response.text(),
                                      }};
                                    }})()
                                """,
                                "awaitPromise": True,
                                "returnByValue": True,
                            })
                            value = replay.get("result", {}).get("value", {})
                            if not value.get("ok"):
                                print(
                                    "[browser] Douyin Full cursor request failed "
                                    f"with HTTP {value.get('status', '?')}"
                                )
                                break
                            try:
                                next_payload = json.loads(value.get("text") or "{}")
                            except (TypeError, json.JSONDecodeError):
                                print(
                                    "[browser] Douyin Full cursor request returned "
                                    "invalid JSON "
                                    f"(HTTP {value.get('status', '?')}, "
                                    f"type {value.get('contentType') or '?'}, "
                                    f"bytes {len(value.get('text') or '')}, "
                                    f"path {str(value.get('finalUrl') or '').split('?', 1)[0]})"
                                )
                                break
                            next_page, has_more = _extract_page(next_payload)
                            server_has_more = has_more
                            next_cursor = next_payload.get("max_cursor")
                            fresh = [
                                item for item in next_page
                                if str(item.get("aweme_id", "")) not in seen
                            ]
                            for item in fresh:
                                seen.add(str(item.get("aweme_id", "")))
                            if fresh:
                                last_page = asyncio.get_running_loop().time()
                                if progress_callback:
                                    progress_callback(len(seen), None, "scanning")
                                yield fresh
                            if not has_more:
                                completed = True
                                break
                            if not fresh or str(next_cursor) in cursor_history:
                                print(
                                    "[browser] Douyin Full pagination stopped: "
                                    "cursor did not advance to new posts"
                                )
                                break
                            cursor_history.add(str(next_cursor))
                            cursor = next_cursor
                            await asyncio.sleep(_SCROLL_INTERVAL_SECONDS)
                        break
                    if not has_more:
                        completed = True
                        break
            now = asyncio.get_running_loop().time()
            if now >= next_scroll_at:
                await cdp.call("Runtime.evaluate", {"expression": """
                    (() => {
                      const nodes = [...document.querySelectorAll('*')]
                        .filter(node => node.scrollHeight > node.clientHeight + 200)
                        .sort((a, b) => b.scrollHeight - a.scrollHeight);
                      const feed = nodes[0] || document.scrollingElement;
                      if (!feed) return false;
                      // Leave and re-enter the bottom sentinel. Remaining
                      // pinned at scrollHeight does not retrigger Douyin's
                      // IntersectionObserver after its second cursor page.
                      feed.scrollTop = Math.max(0, feed.scrollHeight - feed.clientHeight - 900);
                      setTimeout(() => { feed.scrollTop = feed.scrollHeight; }, 300);
                      return true;
                    })()
                """})
                # If the page does not answer this scroll, retry only after a
                # complete pacing interval instead of hammering the sentinel.
                next_scroll_at = now + scroll_interval
            elapsed = asyncio.get_running_loop().time() - started
            if (
                yielded
                and server_has_more
                and asyncio.get_running_loop().time() - last_page >= idle_seconds
            ):
                print(
                    f"[browser] Douyin pagination stalled after {len(seen)} "
                    "posts while the server still reported more pages"
                )
                break
            if yielded and not server_has_more:
                break
        if not yielded:
            raise RuntimeError(
                "Douyin returned no post list in Edge. Open the Archiver Edge "
                "profile, complete any verification, and try again."
            )
    finally:
        if completion_callback:
            try:
                completion_callback(completed, len(seen))
            except Exception:
                pass
        if cdp:
            await cdp.close()
        if port and target_id:
            await _close_page(port, target_id)
        if not shared:
            await _close_browser(port, process)
        _LAST_ENUMERATION_END = time.monotonic()
        _PROFILE_LOCK.release()
