"""Runs the reconciliation pipeline in a background thread so a GUI can trigger it and follow its events."""
import asyncio
import logging
import threading
import time
import traceback

from . import events
from .config import Settings
from .costs import scan_inputs
from .events import EventLogHandler
from .pipeline import run_pipeline

STEPS = {0: "Orchestrator run"}


class RunController:
    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.run_id = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, settings: Settings, dry_run: bool = True) -> str:
        if self.running:
            raise RuntimeError("A run is already in progress")
        self.run_id = time.strftime("%Y%m%d_%H%M%S")
        events.start_run(f"{settings.output_folder}/runs", self.run_id)
        self.thread = threading.Thread(target=self._work, args=(settings, dry_run), daemon=True)
        self.thread.start()
        return self.run_id

    def cancel(self) -> None:
        if self._loop and self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)

    def _work(self, settings: Settings, dry_run: bool) -> None:
        handler = EventLogHandler()
        logging.getLogger("recon").addHandler(handler)
        events.emit("run_start", run_id=self.run_id, dry_run=dry_run, folder=settings.docs_folder)
        try:
            events.emit("plan", **scan_inputs(settings.docs_folder))
            self._loop = asyncio.new_event_loop()
            self._task = self._loop.create_task(run_pipeline(settings, dry_run))
            result = self._loop.run_until_complete(self._task)
            events.emit("run_end", status="done", result=result)
        except asyncio.CancelledError:
            events.emit("run_end", status="cancelled", result="Cancelled by user")
        except Exception as e:
            events.emit("run_end", status="failed", result=f"{e}\n{traceback.format_exc()}")
        finally:
            logging.getLogger("recon").removeHandler(handler)
            if self._loop:
                self._loop.close()
            self._loop = self._task = None
            events.stop_run()
