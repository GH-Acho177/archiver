"""
Helper: download all post videos for a Douyin user into a flat output folder,
        bypassing f2's create_user_folder so the path is exactly what we specify.

interval:     'all'  or  '2024-01-01|2024-12-31'
archive_file: path to a text file (one aweme_id per line) tracking seen posts.
full:         True → re-download anything missing from disk (ignore archive).
              False → skip anything in the archive; stop when a full page is all-seen.
"""

import asyncio
import re
import sys
from pathlib import Path

_AWEME_ID_RE = re.compile(r"\d{15,20}")


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


async def download_user(
    url: str,
    cookie: str,
    outdir: str,
    interval: str,
    naming: str = "{create:.10}_{aweme_id}",
    stop_check=None,
    full: bool = False,
    archive_file: str = "",
) -> None:
    from f2.apps.douyin.utils import ClientConfManager, SecUserIdFetcher
    from f2.apps.douyin.handler import DouyinHandler
    from f2.utils.utils import interval_2_timestamp

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
        "max_connections": 10,
        "max_tasks":       10,
        "page_counts":     50,
        "max_counts":      None,
        "headers":         ClientConfManager.headers(),
        "folderize":       False,
        "music":           False,
        "cover":           False,
        "desc":            False,
        "lyric":           False,
    }

    handler = DouyinHandler(kwargs)

    if interval == "all":
        min_cursor, max_cursor = 0, 0
    else:
        min_cursor = interval_2_timestamp(interval, date_type="start")
        max_cursor = interval_2_timestamp(interval, date_type="end")

    sec_user_id = await SecUserIdFetcher.get_sec_user_id(url)

    user_path = Path(outdir)
    user_path.mkdir(parents=True, exist_ok=True)

    if full:
        # Full mode: re-download anything no longer on disk.
        seen_ids = {
            p.stem.rsplit("_", 1)[-1]
            for p in user_path.iterdir()
            if p.is_file()
        }
    else:
        # Update mode: trust the archive when it has entries.
        # If empty (first run / reset), seed from files already on disk so we
        # stop early instead of fetching all history.
        seen_ids = _load_archive(archive_file) if archive_file else set()
        if not seen_ids and user_path.exists():
            seen_ids = {
                p.stem.rsplit("_", 1)[-1]
                for p in user_path.iterdir()
                if p.is_file()
            }

    try:
        async for aweme_data_list in handler.fetch_user_post_videos(
            sec_user_id, min_cursor, max_cursor,
            kwargs["page_counts"], kwargs["max_counts"]
        ):
            if stop_check and stop_check():
                break
            page  = aweme_data_list._to_list()

            items = [item for item in page
                     if str(item.get("aweme_id", "")) not in seen_ids]

            # Update mode: if every post in this page is already archived,
            # we've caught up — no need to fetch older pages.
            if not full and page and not items:
                break

            for item in items:
                if not item.get("nickname"):
                    item["nickname"] = "unknown"
                if not item.get("create"):
                    item["create"] = item.get("aweme_id", "unknown")

            if items:
                await handler.downloader.create_download_tasks(
                    kwargs, items, user_path
                )
                new_ids = {str(item["aweme_id"]) for item in items if item.get("aweme_id")}
                seen_ids.update(new_ids)  # in-run dedup regardless of success
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
    naming   = sys.argv[5] if len(sys.argv) > 5 else "{create:.10}_{aweme_id}"
    await download_user(url, cookie, outdir, interval, naming)


if __name__ == "__main__":
    asyncio.run(main())
