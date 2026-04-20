"""
Helper: download all post videos for a Douyin user into a flat output folder,
        bypassing f2's create_user_folder so the path is exactly what we specify.

interval: 'all'  or  '2024-01-01|2024-12-31'
"""

import asyncio
import sys
from pathlib import Path


async def download_user(
    url: str,
    cookie: str,
    outdir: str,
    interval: str,
    naming: str = "{create:.10}_{aweme_id}",
    stop_check=None,
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
        "timeout":         30,
        "max_retries":     3,
        "max_connections": 5,
        "max_tasks":       5,
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

    if interval == "all":
        min_cursor, max_cursor = 0, 0
    else:
        min_cursor = interval_2_timestamp(interval, date_type="start")
        max_cursor = interval_2_timestamp(interval, date_type="end")

    sec_user_id = await SecUserIdFetcher.get_sec_user_id(url)

    user_path = Path(outdir)
    user_path.mkdir(parents=True, exist_ok=True)

    async for aweme_data_list in handler.fetch_user_post_videos(
        sec_user_id, min_cursor, max_cursor,
        kwargs["page_counts"], kwargs["max_counts"]
    ):
        if stop_check and stop_check():
            break
        await handler.downloader.create_download_tasks(
            kwargs, aweme_data_list._to_list(), user_path
        )


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
