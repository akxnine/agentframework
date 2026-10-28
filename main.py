import argparse
import asyncio

from recon.agent import build_agent
from recon.config import Settings


async def main():
    p = argparse.ArgumentParser(description="Trade reconciliation orchestration agent")
    p.add_argument("--folder", help="Folder containing client/ and advisor/ subfolders")
    p.add_argument("--dry-run", action="store_true", help="Create Excel but do not send email")
    a = p.parse_args()
    s = Settings()
    if a.folder:
        s.docs_folder = a.folder
    agent, _ = build_agent(s, a.dry_run)
    r = await agent.run("Reconcile the client and advisor trades in the folder and report discrepancies.")
    print(r.text)


if __name__ == "__main__":
    asyncio.run(main())
