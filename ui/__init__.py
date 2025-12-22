"""UI-Komponenten für PulseScribe (Menübar, Overlay, Welcome).

macOS: MenuBarController, OverlayController, OnboardingWizardController, WelcomeController
Windows: WindowsOverlayController
"""

import sys

__all__ = []

if sys.platform == "darwin":
    from .menubar import MenuBarController
    from .overlay import OverlayController
    from .onboarding_wizard import OnboardingWizardController
    from .welcome import WelcomeController

    __all__ = [
        "MenuBarController",
        "OverlayController",
        "OnboardingWizardController",
        "WelcomeController",
    ]
elif sys.platform == "win32":
    from .overlay_windows import WindowsOverlayController

    __all__ = ["WindowsOverlayController"]
