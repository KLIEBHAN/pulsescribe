"""UI-Komponenten für PulseScribe (Menübar, Overlay, Welcome).

macOS: MenuBarController, OverlayController, OnboardingWizardController, WelcomeController
Windows: WindowsOverlayController
"""

from __future__ import annotations

from importlib import import_module
import sys
from typing import TYPE_CHECKING

_MAC_EXPORTS = {
    "MenuBarController": (".menubar", "MenuBarController"),
    "OverlayController": (".overlay", "OverlayController"),
    "OnboardingWizardController": (".onboarding_wizard", "OnboardingWizardController"),
    "WelcomeController": (".welcome", "WelcomeController"),
}

if TYPE_CHECKING:
    if sys.platform == "darwin":
        from .menubar import MenuBarController  # noqa: F401
        from .onboarding_wizard import OnboardingWizardController  # noqa: F401
        from .overlay import OverlayController  # noqa: F401
        from .welcome import WelcomeController  # noqa: F401
    elif sys.platform == "win32":
        from .overlay_windows import WindowsOverlayController  # noqa: F401

if sys.platform == "darwin":
    __all__ = list(_MAC_EXPORTS)
elif sys.platform == "win32":
    __all__ = ["WindowsOverlayController"]
else:
    __all__ = []


def __getattr__(name: str):
    if sys.platform == "darwin":
        export = _MAC_EXPORTS.get(name)
        if export is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

        module_name, attr_name = export
        module = import_module(module_name, __name__)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value

    if sys.platform == "win32" and name == "WindowsOverlayController":
        # Prefer PySide6 overlay (GPU-accelerated), fallback to Tkinter.
        try:
            module = import_module(".overlay_pyside6", __name__)
            value = getattr(module, "PySide6OverlayController")
        except ImportError:
            module = import_module(".overlay_windows", __name__)
            value = getattr(module, "WindowsOverlayController")

        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
