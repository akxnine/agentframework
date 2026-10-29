import argparse
import asyncio

from recon.config import Settings
from recon.console import setup_logging
from recon.pipeline import run_pipeline
from precheck.server_check import ServerCheck


async def main():
    p = argparse.ArgumentParser(description="Monthly brokerage statement reconciliation")
    p.add_argument("--folder", help="Folder with statements/, client_trades/ and master/ subfolders")
    p.add_argument("--dry-run", action="store_true", help="Build reports but do not send email")
    p.add_argument("--in-place", action="store_true", help="Update master trackers in place (default: copy to output/master)")
    p.add_argument("--keep-server", action="store_true", help="Leave the Foundry server running when finished")
    a = p.parse_args()
    setup_logging()
    s = Settings()
    if a.folder:
        s.docs_folder = a.folder
    s.in_place = a.in_place
    check = ServerCheck(s)
    try:
        check.ensure()
        print(await run_pipeline(s, a.dry_run))
    finally:
        if not a.keep_server:
            ServerCheck.stop()


if __name__ == "__main__":
    asyncio.run(main())
