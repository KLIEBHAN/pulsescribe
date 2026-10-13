"""Platform-neutral content of the update window.

``build_update_view(state)`` turns an ``UpdateState`` into the texts, progress
and buttons that the macOS (AppKit) and Windows (Qt) windows render, so both
platforms show the same update steps.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from utils.update_flow import UpdateState

NOTES_MAX_CHARS = 12_000
PROGRESS_INDETERMINATE = -1


@dataclass(frozen=True)
class UpdateView:
    headline: str
    detail: str = ""
    notes: str = ""
    notice: str = ""
    # None hides the bar, PROGRESS_INDETERMINATE animates it, 0-100 is determinate.
    progress: int | None = None
    primary_label: str = ""
    primary_action: str = ""
    primary_enabled: bool = True
    close_label: str = "Close"

    def to_dict(self) -> dict:
        """Serialize for the Windows window process."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UpdateView":
        return cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})


def _version_line(state: UpdateState) -> str:
    if state.offer is None:
        return f"Installed: {state.current}"
    return f"Installed: {state.current}  →  New: {state.offer.version}"


_LIST_MARKERS = ("- ", "* ", "\u2022 ")


def reflow_release_notes(text: str) -> str:
    """Join hard-wrapped paragraph lines so the window can wrap them itself.

    Code fences and list items keep their own lines; continuation lines of a
    list item are joined to that item.
    """
    output: list[str] = []
    in_fence = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            output.append(line)
            continue
        previous = output[-1] if output else ""
        joinable = (
            not in_fence
            and stripped
            and previous.strip()
            and not previous.strip().startswith("```")
            and not stripped.startswith(_LIST_MARKERS)
            and not stripped.startswith("#")
            and not previous.strip().startswith("#")
        )
        if joinable:
            output[-1] = f"{previous} {stripped}"
        else:
            output.append(line if in_fence else stripped)
    return "\n".join(output).strip()


def _notes(state: UpdateState) -> str:
    if state.offer is None:
        return ""
    notes = reflow_release_notes(state.offer.notes or "")
    return (notes or "No release notes were published.")[:NOTES_MAX_CHARS]


def build_update_view(state: UpdateState, notice: str = "") -> UpdateView:
    """Return what the update window shows for one update state."""
    offer = state.offer
    if state.status == "checking":
        return UpdateView(
            "Checking for updates…",
            detail=_version_line(state),
            notice=notice,
            progress=PROGRESS_INDETERMINATE,
        )
    if state.status == "downloading" and offer:
        return UpdateView(
            f"Downloading PulseScribe {offer.version}… {state.percent}%",
            detail=_version_line(state),
            notes=_notes(state),
            notice=notice,
            progress=state.percent,
            primary_label="Restart and Install",
            primary_action="install",
            primary_enabled=False,
            close_label="Hide",
        )
    if state.status == "ready" and offer:
        return UpdateView(
            f"PulseScribe {offer.version} is ready to install",
            detail=state.hint or _version_line(state),
            notes=_notes(state),
            notice=notice,
            primary_label="Restart and Install" if state.automatic else "Open Installer",
            primary_action="install",
            close_label="Later",
        )
    if state.status == "available" and offer:
        return UpdateView(
            f"PulseScribe {offer.version} is available",
            detail=_version_line(state),
            notes=_notes(state),
            notice=notice,
            primary_label="Download",
            primary_action="download",
            close_label="Later",
        )
    if state.status == "error":
        return UpdateView(
            "Update failed",
            detail=state.error or "The update could not be completed.",
            notes=_notes(state),
            notice=notice,
            primary_label="Retry",
            primary_action="retry",
        )
    return UpdateView(
        f"PulseScribe {state.current} is up to date",
        detail="PulseScribe checks for updates automatically every hour.",
        notice=notice,
    )


__all__ = [
    "PROGRESS_INDETERMINATE",
    "UpdateView",
    "build_update_view",
    "reflow_release_notes",
]
