import argparse
import asyncio

from recon.config import Settings
from recon.console import setup_logging
from recon.pipeline import run_pipeline


async def main():
    p = argparse.ArgumentParser(description="Monthly brokerage statement reconciliation (Claude SDK)")
    p.add_argument("--folder", help="Folder with statements/, client_trades/ and master/ subfolders")
    p.add_argument("--dry-run", action="store_true", help="Build reports but do not send email")
    p.add_argument("--in-place", action="store_true", help="Update master trackers in place (default: copy to output/master)")
    p.add_argument("--model", help="Claude model id (default: $CLAUDE_MODEL or claude-opus-5-5)")
    a = p.parse_args()
    setup_logging()
    s = Settings()
    if a.folder:
        s.docs_folder = a.folder
    if a.model:
        s.model = a.model
    s.in_place = a.in_place
    print(await run_pipeline(s, a.dry_run))


if __name__ == "__main__":
    asyncio.run(main())
