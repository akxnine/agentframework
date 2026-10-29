import argparse
import asyncio
import logging
import os
import shlex
import subprocess
import sys

from recon.config import Settings
from recon.pipeline import run_pipeline


def resolve_from_preflight(s: Settings, reason: str) -> None:
    """Run foundry_preflight.py and read the verified host/model from its `--print-env` output."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "foundry_preflight.py")
    print(f"Pre-check failed ({reason.splitlines()[0][:120]}); running foundry_preflight.py ...", file=sys.stderr)
    proc = subprocess.run([sys.executable, script, "--print-env"], stdout=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise SystemExit("Preflight failed; see output above.")
    env = {}
    for line in proc.stdout.splitlines():
        if line.startswith("export "):
            k, _, v = line[7:].partition("=")
            env[k] = shlex.split(v)[0] if v else ""
    s.foundry_host = env.get("FOUNDRY_HOST", "").rstrip("/")
    s.model = env.get("FOUNDRY_MODEL", "")


async def main():
    p = argparse.ArgumentParser(description="Monthly brokerage statement reconciliation")
    p.add_argument("--folder", help="Folder with statements/, client_trades/ and master/ subfolders")
    p.add_argument("--dry-run", action="store_true", help="Build reports but do not send email")
    p.add_argument("--in-place", action="store_true", help="Update master trackers in place (default: copy to output/master)")
    p.add_argument("--keep-server", action="store_true", help="Leave the Foundry server running when finished")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = Settings()
    if a.folder:
        s.docs_folder = a.folder
    s.in_place = a.in_place
    try:
        try:
            s.check_server()
        except RuntimeError as e:
            # server down, port changed or model not served: let the preflight start/load, then re-verify
            resolve_from_preflight(s, str(e))
            try:
                s.check_server()
            except RuntimeError as e2:
                raise SystemExit(f"Pre-check failed: {e2}")
        print(await run_pipeline(s, a.dry_run))
    finally:
        if not a.keep_server:
            stop_server()


def stop_server() -> None:
    print("Stopping Foundry server ...", file=sys.stderr)
    try:
        subprocess.run(["foundry", "server", "stop"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"Could not stop Foundry server: {e}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
