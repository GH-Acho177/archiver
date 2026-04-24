"""
Helper: download a single Douyin video by aweme_id into a flat output folder,
        bypassing AwemeIdFetcher's unauthenticated HTTP redirect step and f2's
        per-user subfolder creation.
"""

import asyncio
import sys
from pathlib import Path


async def download_one(
    aweme_id: str,
    cookie: str,
    outdir: str,
    naming: str = "{nickname}_{create}_{aweme_id}",
) -> None:
    from f2.apps.douyin.utils import ClientConfManager
    from f2.apps.douyin import utils as dy_utils

    async def _fake(cls, url: str) -> str:  # noqa: ARG001
        return aweme_id

    dy_utils.AwemeIdFetcher.get_aweme_id = classmethod(_fake)

    from f2.apps.douyin.handler import DouyinHandler

    kwargs = {
        "cookie":          cookie,
        "url":             f"https://www.douyin.com/video/{aweme_id}",
        "path":            outdir,
        "naming":          naming,
        "languages":       "en_US",
        "timeout":         30,
        "max_retries":     3,
        "max_connections": 10,
        "max_tasks":       10,
        "headers":         ClientConfManager.headers(),
        "folderize":       False,
        "music":           False,
        "cover":           False,
        "desc":            False,
        "lyric":           False,
    }

    handler = DouyinHandler(kwargs)

    aweme_data = await handler.fetch_one_video(aweme_id)
    user_path = Path(outdir)
    user_path.mkdir(parents=True, exist_ok=True)
    await handler.downloader.create_download_tasks(
        kwargs, aweme_data._to_dict(), user_path
    )


async def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: f2_one.py <aweme_id> <cookie> <outdir> [naming]", file=sys.stderr)
        sys.exit(1)

    aweme_id = sys.argv[1]
    cookie   = sys.argv[2]
    outdir   = sys.argv[3]
    naming   = sys.argv[4] if len(sys.argv) > 4 else "{nickname}_{create}_{aweme_id}"
    await download_one(aweme_id, cookie, outdir, naming)


if __name__ == "__main__":
    asyncio.run(main())
