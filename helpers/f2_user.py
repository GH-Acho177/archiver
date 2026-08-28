"""
Helper: download all post videos for a Douyin user into a flat output folder,
        bypassing f2's create_user_folder so the path is exactly what we specify.

interval:     'all'  or  '2024-01-01|2024-12-31'
archive_file: path to a text file (one aweme_id per line) tracking seen posts.
full:         True → re-download anything missing from disk (ignore archive).
              False → skip anything in the archive; stop when a full page is all-seen.
"""

import asyncio
import hashlib
import re
import sys
from pathlib import Path

_AWEME_ID_RE = re.compile(r"\d{15,20}")


async def _await_or_stop(awaitable, stop_check):
    """Await an f2 operation while allowing the UI to cancel it."""
    task = asyncio.ensure_future(awaitable)
    while not task.done():
        if stop_check and stop_check():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            return None, True
        await asyncio.sleep(0.1)
    return await task, False


def _load_archive(path: str) -> set:
    p = Path(path)
    if not p.exists():
        return set()
    return set(p.read_text("utf-8").splitlines())


def _append_archive(path: str, ids: "set[str]") -> None:
    if not ids:
        return
    with open(path, "a", encoding="utf-8") as f:
        for aid in ids:
            f.write(aid + "\n")


def _local_post_files(folder: Path) -> "dict[str, list[Path]]":
    files: dict[str, list[Path]] = {}
    if not folder.exists():
        return files
    for path in folder.iterdir():
        if not path.is_file():
            continue
        match = _AWEME_ID_RE.search(path.stem)
        if match:
            files.setdefault(match.group(), []).append(path)
    return files


def _post_is_complete(item: dict, local_files: "dict[str, list[Path]]") -> bool:
    """Compare remote post metadata with media that actually exists on disk."""
    files = local_files.get(str(item.get("aweme_id", "")), [])
    if not files:
        return False
    if item.get("aweme_type") == 68:
        expected_images = len([url for url in item.get("images", []) if url])
        expected_live = len([url for url in item.get("images_video", []) if url])
        actual_images = sum("_image_" in path.stem for path in files)
        actual_live = sum("_live_" in path.stem for path in files)
        return (expected_images + expected_live > 0
                and actual_images >= expected_images
                and actual_live >= expected_live)
    return any(path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
               for path in files)


def _same_content(first: Path, second: Path) -> bool:
    try:
        if first.stat().st_size != second.stat().st_size:
            return False
        def digest(path: Path) -> bytes:
            value = hashlib.sha256()
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    value.update(chunk)
            return value.digest()
        return digest(first) == digest(second)
    except OSError:
        return False


def _reconcile_post_names(item: dict, folder: Path, naming: str, formatter) -> None:
    """Rename legacy Douyin media using current metadata and remove exact copies."""
    post_id = str(item.get("aweme_id", ""))
    if not post_id:
        return
    base = formatter(naming, item)
    for source in list(_local_post_files(folder).get(post_id, [])):
        role_match = re.search(r"_(video|image_\d+|live_\d+)$", source.stem)
        if role_match:
            role = role_match.group(1)
        elif source.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}:
            role = "video"
        else:
            continue
        target = folder / f"{base}_{role}{source.suffix.lower()}"
        if source == target:
            continue
        try:
            if target.exists():
                if _same_content(source, target):
                    source.unlink()
                    print(f"[dedupe] Removed duplicate {source.name}")
                else:
                    print(f"[naming] Collision kept unchanged: {source.name}")
                continue
            source.rename(target)
            print(f"[naming] {source.name} -> {target.name}")
        except OSError as exc:
            print(f"[naming] Could not rename {source.name}: {exc}")


async def download_user(
    url: str,
    cookie: str,
    outdir: str,
    interval: str,
    naming: str = "{create:.10}_{aweme_id}_{desc:.60}",
    stop_check=None,
    full: bool = False,
    archive_file: str = "",
    sleep_req: float = 1.0,
    max_tasks: int = 4,
    progress_callback=None,
) -> dict:
    from f2.apps.douyin.utils import (
        ClientConfManager, SecUserIdFetcher, format_file_name,
    )
    from f2.apps.douyin.handler import DouyinHandler
    from f2.apps.douyin.filter import UserPostFilter
    from f2.utils.utils import interval_2_timestamp
    from helpers.douyin_browser import fetch_user_posts

    kwargs = {
        "cookie":          cookie,
        "url":             url,
        "path":            outdir,
        "mode":            "post",
        "naming":          naming,
        "interval":        interval,
        "languages":       "en_US",
        "timeout":         5,
        "max_retries":     3,
        "max_connections": max_tasks,
        "max_tasks":       max_tasks,
        # Douyin's user-post endpoint paginates reliably at f2's default of
        # 20. Larger values can return one partial page followed by an empty
        # cursor page even while has_more is true, truncating account syncs.
        "page_counts":     20,
        "max_counts":      None,
        "headers":         ClientConfManager.headers(),
        "folderize":       False,
        "music":           False,
        "cover":           False,
        "desc":            False,
        "lyric":           False,
    }

    handler = DouyinHandler(kwargs)
    # Archiver handles its own UI/Telegram output. A globally enabled f2 Bark
    # notifier otherwise performs an extra network request after every account.
    handler.enable_bark = False

    if interval == "all":
        min_cursor, max_cursor = 0, 0
    else:
        min_cursor = interval_2_timestamp(interval, date_type="start")
        max_cursor = interval_2_timestamp(interval, date_type="end")

    direct_id = re.search(r"douyin\.com/user/([^/?&#\s]+)", url)
    if direct_id:
        sec_user_id, stopped = direct_id.group(1), False
    else:
        direct_id = re.search(r"douyin\.com/user/([^/?&#\s]+)", url)
        if direct_id:
            sec_user_id, stopped = direct_id.group(1), False
        else:
            sec_user_id, stopped = await _await_or_stop(
                SecUserIdFetcher.get_sec_user_id(url), stop_check
            )
    if stopped:
        await handler.downloader.close()
        return {"remote_total": None, "remote_ids": []}

    # Direct F2 profile/list calls are currently answered with HTTP 200 and an
    # empty body by Douyin. Edge supplies the authoritative enumerated total.
    remote_total = None
    if progress_callback:
        progress_callback(0, None, "scanning")

    user_path = Path(outdir)
    user_path.mkdir(parents=True, exist_ok=True)
    remote_ids: set[str] = set()
    remote_names: dict[str, str] = {}
    remote_ids_complete = False
    pending_items: list[dict] = []
    local_files = _local_post_files(user_path)

    def _listing_completed(completed: bool, _seen: int) -> None:
        nonlocal remote_ids_complete
        remote_ids_complete = completed

    try:
        print("[browser] Reading Douyin posts through the saved Edge profile")
        pages = fetch_user_posts(
            sec_user_id,
            stop_check=stop_check,
            progress_callback=progress_callback,
            completion_callback=_listing_completed,
            cookie_header=cookie,
            full_scan=full,
        ).__aiter__()
        while True:
            try:
                aweme_data_list, stopped = await _await_or_stop(
                    pages.__anext__(), stop_check
                )
            except StopAsyncIteration:
                break
            if stopped:
                break
            if sleep_req > 0:
                _, stopped = await _await_or_stop(
                    asyncio.sleep(sleep_req), stop_check
                )
                if stopped:
                    break
            # The browser captures Douyin's original JSON. Feed it through
            # F2's mature adapter so the existing media downloader and naming
            # policy keep receiving their established normalized structure.
            if interval != "all":
                aweme_data_list = [
                    item for item in aweme_data_list
                    if min_cursor <= int(item.get("create_time", 0) or 0) <= max_cursor
                ]
            page = UserPostFilter({"aweme_list": aweme_data_list})._to_list()
            remote_ids.update(
                str(item["aweme_id"]) for item in page if item.get("aweme_id")
            )
            for item in page:
                post_id = str(item.get("aweme_id") or "")
                if post_id:
                    try:
                        remote_names[post_id] = format_file_name(naming, item)
                    except Exception:
                        pass
            if progress_callback:
                progress_callback(len(remote_ids), remote_total, "scanning")
            reached_local = False
            if full:
                items = [
                    item for item in page
                    if not _post_is_complete(item, local_files)
                ]
            else:
                # Do not stop in the middle of a page. Douyin may put older
                # pinned posts before the genuinely newest posts, so the first
                # local item is not necessarily the recent-post boundary.
                # Queue every missing post on this page, then stop before
                # requesting older pages once this page overlaps local disk.
                completeness = [
                    _post_is_complete(item, local_files) for item in page
                ]
                reached_local = any(completeness)
                items = [
                    item for item, complete in zip(page, completeness)
                    if not complete
                ]

            for item in items:
                if not item.get("nickname"):
                    item["nickname"] = "unknown"
                if not item.get("create"):
                    item["create"] = item.get("aweme_id", "unknown")

            pending_items.extend(items)
            if reached_local:
                print(
                    "[update] Current Douyin page overlaps local posts; "
                    "queued its missing posts and stopped before older pages"
                )
                break

        # Enumeration owns the single Edge profile lock. Finish it before
        # downloading so Edge can scan the next account while these media
        # tasks run concurrently.
        await pages.aclose()
        items = pending_items
        if items and not (stop_check and stop_check()):
                # f2 schedules all media tasks from a list and executes them
                # concurrently. Chunking by max_tasks makes "Per acct" the
                # actual simultaneous-post limit instead of downloading one
                # post at a time.
                if progress_callback:
                    # Keep the card on cumulative remote-list progress. Using
                    # this page's missing-item count resets (for example)
                    # 20/400 to 0/3 and makes the bar appear stuck or jump.
                    progress_callback(
                        len(remote_ids), remote_total, "downloading"
                    )
                for offset in range(0, len(items), max(1, max_tasks)):
                    chunk = items[offset:offset + max(1, max_tasks)]
                    try:
                        _, stopped = await _await_or_stop(
                            handler.downloader.create_download_tasks(
                                kwargs, chunk, user_path
                            ),
                            stop_check,
                        )
                    except Exception as exc:
                        chunk_ids = ", ".join(
                            str(item.get("aweme_id", "unknown")) for item in chunk
                        )
                        print(f"[error] Douyin posts {chunk_ids} failed: {exc}")
                        pending = list(handler.downloader.download_tasks)
                        for task in pending:
                            if not task.done():
                                task.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                        handler.downloader.download_tasks.clear()
                        stopped = False
                        # Retry only still-incomplete posts individually so one
                        # bad post cannot discard the remainder of the chunk.
                        local_after_failure = _local_post_files(user_path)
                        for item in chunk:
                            if _post_is_complete(item, local_after_failure):
                                continue
                            try:
                                _, stopped = await _await_or_stop(
                                    handler.downloader.create_download_tasks(
                                        kwargs, [item], user_path
                                    ),
                                    stop_check,
                                )
                            except Exception as item_exc:
                                post_id = str(item.get("aweme_id", "unknown"))
                                print(
                                    f"[error] Douyin post {post_id} failed: "
                                    f"{item_exc}"
                                )
                                handler.downloader.download_tasks.clear()
                                stopped = False
                            if stopped:
                                break
                    if progress_callback:
                        progress_callback(
                            len(remote_ids),
                            remote_total,
                            "downloading",
                        )
                    if stopped:
                        break
                if stopped:
                    items = []
                new_ids = {str(item["aweme_id"]) for item in items if item.get("aweme_id")}
                if archive_file:
                    # Only persist IDs where at least one file landed on disk.
                    # 丢失 (all CDN links failed) leaves no file, so those IDs
                    # stay out of the archive and are retried next run.
                    on_disk = {
                        m.group()
                        for p in user_path.iterdir() if p.is_file()
                        for m in [_AWEME_ID_RE.search(p.stem)] if m
                    }
                    _append_archive(archive_file, new_ids & on_disk)
    finally:
        await handler.downloader.close()
    return {
        "remote_total": len(remote_ids) if remote_ids_complete else None,
        "remote_ids": sorted(remote_ids),
        "remote_ids_complete": remote_ids_complete,
        "remote_names": remote_names,
    }


async def download_selected_posts(
    sec_user_id: str,
    cookie: str,
    outdir: str,
    post_ids: "set[str]",
    naming: str = "{create:.10}_{aweme_id}_{desc:.60}",
    stop_check=None,
    progress_callback=None,
) -> set[str]:
    """Download selected posts using metadata captured from the Edge list."""
    from f2.apps.douyin.utils import ClientConfManager
    from f2.apps.douyin.handler import DouyinHandler
    from f2.apps.douyin.filter import UserPostFilter
    from helpers.douyin_browser import fetch_user_posts

    wanted = {str(post_id) for post_id in post_ids}
    found: set[str] = set()
    completed = 0
    kwargs = {
        "cookie": cookie, "path": outdir, "mode": "post", "naming": naming,
        "interval": "all", "languages": "en_US", "timeout": 30,
        "max_retries": 3, "max_connections": 4, "max_tasks": 4,
        "headers": ClientConfManager.headers(), "folderize": False,
        "music": False, "cover": False, "desc": False, "lyric": False,
    }
    handler = DouyinHandler(kwargs)
    handler.enable_bark = False
    folder = Path(outdir)
    folder.mkdir(parents=True, exist_ok=True)
    pages = fetch_user_posts(
        sec_user_id, stop_check=stop_check, cookie_header=cookie
    )
    try:
        async for raw_page in pages:
            page = UserPostFilter({"aweme_list": raw_page})._to_list()
            selected = [
                item for item in page
                if str(item.get("aweme_id") or "") in wanted - found
            ]
            for item in selected:
                if stop_check and stop_check():
                    return found
                post_id = str(item.get("aweme_id"))
                found.add(post_id)
                await handler.downloader.create_download_tasks(
                    kwargs, [item], folder,
                )
                completed += 1
                if progress_callback:
                    progress_callback(completed, len(wanted), post_id)
            if found >= wanted:
                break
    finally:
        await pages.aclose()
        await handler.downloader.close()
    return found


async def maintain_user(
    url: str,
    cookie: str,
    outdir: str,
    naming: str = "{create:.10}_{aweme_id}_{desc:.60}",
    stop_check=None,
    rename_files: bool = False,
) -> dict:
    """Fetch the Douyin remote list, optionally reconciling local filenames."""
    from f2.apps.douyin.handler import DouyinHandler
    from f2.apps.douyin.utils import (
        ClientConfManager, SecUserIdFetcher, format_file_name,
    )
    kwargs = {
        "cookie": cookie, "url": url, "languages": "en_US",
        # f2 also uses `timeout` as an unconditional delay after every
        # metadata page. Ten seconds made maintenance scale very poorly across
        # accounts even though no media is downloaded here.
        "timeout": 2, "max_retries": 2, "max_connections": 2, "max_tasks": 2,
        "page_counts": 20, "max_counts": None,
        "headers": ClientConfManager.headers(),
    }
    handler = DouyinHandler(kwargs)
    handler.enable_bark = False
    remote_total = None
    remote_ids: set[str] = set()
    try:
        direct_id = re.search(r"douyin\.com/user/([^/?&#\s]+)", url)
        if direct_id:
            sec_user_id, stopped = direct_id.group(1), False
        else:
            sec_user_id, stopped = await _await_or_stop(
                SecUserIdFetcher.get_sec_user_id(url), stop_check
            )
        if stopped:
            return {"remote_total": None, "remote_ids": []}
        try:
            profile, profile_stopped = await _await_or_stop(
                handler.fetch_user_profile(sec_user_id), stop_check
            )
            if not profile_stopped:
                value = getattr(profile, "aweme_count", None)
                remote_total = int(value) if value is not None else None
        except Exception:
            pass
        folder = Path(outdir)
        if not folder.exists():
            return {"remote_total": remote_total, "remote_ids": []}
        pages = handler.fetch_user_post_videos(
            sec_user_id, 0, 0, kwargs["page_counts"], kwargs["max_counts"]
        ).__aiter__()
        reconciled_ids: set[str] = set()
        while True:
            try:
                page_data, stopped = await _await_or_stop(
                    pages.__anext__(), stop_check
                )
            except StopAsyncIteration:
                break
            if stopped:
                break
            page = page_data._to_list()
            remote_ids.update(
                str(item["aweme_id"]) for item in page if item.get("aweme_id")
            )
            if not rename_files:
                continue
            local_ids = set(_local_post_files(folder))
            for item in page:
                post_id = str(item.get("aweme_id", ""))
                if post_id not in local_ids:
                    continue
                if not item.get("nickname"):
                    item["nickname"] = "unknown"
                if not item.get("create"):
                    item["create"] = post_id or "unknown"
                _reconcile_post_names(item, folder, naming, format_file_name)
                reconciled_ids.add(post_id)

        if not rename_files:
            return {
                "remote_total": (
                    remote_total if remote_total is not None
                    else len(remote_ids) or None
                ),
                "remote_ids": sorted(remote_ids),
            }

        # Some older posts remain publicly accessible but are omitted from the
        # account-history endpoint. Resolve only legacy/placeholder names
        # individually by their embedded post IDs.
        unresolved_ids: set[str] = set()
        for post_id, files in _local_post_files(folder).items():
            if post_id in reconciled_ids:
                continue
            for path in files:
                role_match = re.search(
                    r"_(video|image_\d+|live_\d+)$", path.stem
                )
                if not role_match:
                    continue
                between = path.stem[
                    (_AWEME_ID_RE.search(path.stem).end()):role_match.start()
                ].strip(" _-")
                if not between or between == "untitled":
                    unresolved_ids.add(post_id)
                    break
        for post_id in sorted(unresolved_ids):
            if stop_check and stop_check():
                break
            try:
                metadata, stopped = await _await_or_stop(
                    handler.fetch_one_video(post_id), stop_check
                )
                if stopped:
                    break
                item = metadata._to_dict()
                if not item.get("nickname"):
                    item["nickname"] = "unknown"
                if not item.get("create"):
                    item["create"] = post_id
                _reconcile_post_names(item, folder, naming, format_file_name)
                await asyncio.sleep(0.2)
            except Exception as exc:
                print(f"[naming] Could not resolve title for {post_id}: {exc}")
    finally:
        await handler.downloader.close()
    return {
        # The profile endpoint intermittently omits/fails aweme_count even
        # when the complete post-list traversal succeeds. Sync already uses
        # the enumerated IDs as its fallback total; maintenance must do the
        # same or it reports "?" despite having the remote list.
        "remote_total": (remote_total if remote_total is not None
                         else len(remote_ids) or None),
        "remote_ids": sorted(remote_ids),
    }


async def main() -> None:
    if len(sys.argv) < 5:
        print(
            "Usage: f2_user.py <url> <cookie> <outdir> <interval> [naming]",
            file=sys.stderr,
        )
        sys.exit(1)

    url      = sys.argv[1]
    cookie   = sys.argv[2]
    outdir   = sys.argv[3]
    interval = sys.argv[4]
    naming   = (sys.argv[5] if len(sys.argv) > 5
                else "{create:.10}_{aweme_id}_{desc:.60}")
    await download_user(url, cookie, outdir, interval, naming)


if __name__ == "__main__":
    asyncio.run(main())
