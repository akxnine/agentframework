import argparse
import asyncio

from orchestrator.config import Settings
from orchestrator.console import setup_logging


def _run(args: argparse.Namespace) -> None:
    from orchestrator.pipeline import run_pipeline

    setup_logging()
    settings = Settings()
    if args.folder:
        settings.docs_folder = args.folder
    settings.in_place = args.in_place
    print(asyncio.run(run_pipeline(settings, args.dry_run)))


def _watch_compliance(args: argparse.Namespace) -> None:
    from orchestrator.compliance_watch import watch

    setup_logging()
    settings = Settings()
    if args.folder:
        settings.docs_folder = args.folder
    settings.in_place = args.in_place
    asyncio.run(watch(settings, once=args.once))


def main() -> None:
    p = argparse.ArgumentParser(description="Trade reconciliation: Claude Agent SDK orchestrator")
    sub = p.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run the reconciliation workflow once")
    run_p.add_argument("--folder", help="Folder with statements/, client_trades/ and master/ subfolders")
    run_p.add_argument("--dry-run", action="store_true", help="Build reports but do not send email")
    run_p.add_argument("--in-place", action="store_true", help="Update master trackers in place (default: copy to output/master)")
    run_p.set_defaults(func=_run)

    watch_p = sub.add_parser("watch-compliance", help="Poll the compliance mailbox for approve/reject replies")
    watch_p.add_argument("--folder", help="Folder with master/ subfolder to update on decisions")
    watch_p.add_argument("--in-place", action="store_true", help="Match the --in-place tracker location used by `run`")
    watch_p.add_argument("--once", action="store_true", help="Process pending replies once and exit instead of polling")
    watch_p.set_defaults(func=_watch_compliance)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
