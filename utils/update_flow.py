"""One background update worker, with event-driven UI state and hourly checks."""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from utils.update_install import (
    PreparedUpdate,
    can_replace_mac,
    is_local_windows,
    prepare_update,
)
from utils.updates import UpdateOffer, check_release, download_update
from utils.version import get_app_version

logger = logging.getLogger("pulsescribe.updates")


@dataclass(frozen=True)
class UpdateState:
    current: str
    status: str = "idle"
    offer: UpdateOffer | None = None
    percent: int = 0
    error: str = ""
    error_context: str = "check"
    manual: bool = False
    hint: str = ""
    automatic: bool = True

    @property
    def label(self) -> str:
        if self.status == "checking":
            return "Checking for Updates…"
        if self.status == "downloading":
            return f"Downloading Update… {self.percent}%"
        if self.status == "ready" and self.offer:
            return f"{'Restart to Update to' if self.automatic else 'Open Installer for'} {self.offer.version}…"
        if self.status == "available" and self.offer:
            return f"Download PulseScribe {self.offer.version}…"
        if self.status == "error":
            return "Update Failed — Retry…"
        return "Check for Updates…"


class UpdateFlow:
    def __init__(self, target: Path, publish):
        self.target = target
        self.publish = publish
        self.state = UpdateState(get_app_version())
        self.prepared: PreparedUpdate | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._timer: threading.Timer | None = None
        self._work: Path | None = None
        self._installing = False

    def _set(self, **changes) -> None:
        if self._stopped.is_set():
            return
        self.state = replace(self.state, **changes)
        try:
            self.publish(self.state)
        except Exception:
            logger.warning("Could not display update state", exc_info=True)

    def start(self) -> None:
        self._schedule(15)

    def _schedule(self, seconds: int) -> None:
        if self._stopped.is_set():
            return
        self._timer = threading.Timer(seconds, self._automatic_check)
        self._timer.daemon = True
        self._timer.start()

    def _automatic_check(self) -> None:
        self.check()
        self._schedule(3600)

    def stop(self) -> None:
        self._stopped.set()
        if self._timer:
            self._timer.cancel()
        if not self._installing and self._lock.acquire(blocking=False):
            try:
                self._discard_work()
            finally:
                self._lock.release()

    def _discard_work(self) -> None:
        if self._work:
            shutil.rmtree(self._work, ignore_errors=True)
            self._work = None

    def _run(self, action) -> None:
        if self._stopped.is_set() or not self._lock.acquire(blocking=False):
            return

        def worker():
            try:
                action()
            except Exception as error:
                logger.warning("Update failed: %s", error)
                self._set(status="error", error=str(error))
            finally:
                if self._stopped.is_set() and not self._installing:
                    self._discard_work()
                self._lock.release()

        threading.Thread(target=worker, name="PulseScribe-Update", daemon=True).start()

    def check(self, *, manual: bool = False) -> None:
        if self.state.status in ("downloading", "ready"):
            return

        def action():
            self._set(status="checking", error="", error_context="check", manual=manual)
            offer = check_release(
                self.state.current,
                sys.platform,
                local=sys.platform == "win32" and is_local_windows(self.target),
            )
            if not offer:
                self._set(status="idle", offer=None)
                return
            self._set(status="available", offer=offer)
            if sys.platform == "win32" or can_replace_mac(self.target):
                self._download()

        self._run(action)

    def download(self) -> None:
        if self.state.status not in ("available", "error") or self.state.offer is None:
            return

        def action():
            self._set(manual=True)
            self._download()

        self._run(action)

    def _download(self) -> None:
        offer = self.state.offer
        if offer is None or self._stopped.is_set():
            return
        self._discard_work()
        self._work = Path(tempfile.mkdtemp(prefix="pulsescribe-update-"))
        self._set(status="downloading", percent=0, error="", error_context="download")
        package = self._work / offer.name
        download_update(
            offer,
            package,
            lambda percent: self._set(percent=percent),
            self._stopped.is_set,
        )
        if self._stopped.is_set():
            shutil.rmtree(self._work, ignore_errors=True)
            return
        self.prepared = prepare_update(offer, package, self.target, sys.platform)
        if self.prepared.automatic and sys.platform == "darwin":
            shutil.rmtree(self._work)
            self._work = self.prepared.work
        self._set(
            status="ready",
            percent=100,
            hint=self.prepared.hint,
            automatic=self.prepared.automatic,
        )

    def install(self) -> None:
        if self.state.status != "ready" or self.prepared is None:
            raise ValueError("No verified update is ready.")
        self.prepared.launch()
        self._installing = True
