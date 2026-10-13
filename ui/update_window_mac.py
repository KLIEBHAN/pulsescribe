"""AppKit update window for the macOS menu bar app.

Create ``MacUpdateWindow(on_action)`` on the main thread and call ``show``,
``render`` and ``hide`` there; ``on_action`` receives ``install``,
``download``, ``retry`` or ``close`` from the buttons. The window is a
non-activating panel, so automatic announcements never take focus away from
the app the user is dictating into.
"""

from __future__ import annotations

from ui.update_window_model import PROGRESS_INDETERMINATE, UpdateView

WINDOW_WIDTH = 480
WINDOW_HEIGHT_WITH_NOTES = 460
PADDING = 20
BUTTON_HEIGHT = 28
NOTES_BOTTOM = PADDING + BUTTON_HEIGHT + 14
HEADLINE_HEIGHT = 22
DETAIL_HEIGHT = 34
# Progress bar and notice share one row: notices only appear without progress.
STATUS_ROW_HEIGHT = 18
TOP_BLOCK_HEIGHT = PADDING + HEADLINE_HEIGHT + 4 + DETAIL_HEIGHT + 6 + STATUS_ROW_HEIGHT + 10
# Keeps the hidden notes view at a non-negative height so autoresizing stays exact.
WINDOW_HEIGHT_COMPACT = TOP_BLOCK_HEIGHT + NOTES_BOTTOM + 2

_HANDLER_CLASS = None


def _handler_class():
    """Create the Objective-C button target once per process."""
    global _HANDLER_CLASS
    if _HANDLER_CLASS is None:
        import objc  # type: ignore[import-not-found]
        from Foundation import NSObject  # type: ignore[import-not-found]

        class PulseScribeUpdateButtonHandler(NSObject):
            def initWithCallback_(self, callback):
                self = objc.super(PulseScribeUpdateButtonHandler, self).init()
                if self is None:
                    return None
                self.callback = callback
                return self

            @objc.signature(b"v@:@")
            def performAction_(self, _sender):
                self.callback()

        _HANDLER_CLASS = PulseScribeUpdateButtonHandler
    return _HANDLER_CLASS


class MacUpdateWindow:
    failed = False  # In-process AppKit panel: no separate process to fail

    def __init__(self, on_action):
        self._on_action = on_action
        self._panel = None
        self._view: UpdateView | None = None
        self._handlers: list = []

    def show(self, view: UpdateView, *, activate: bool) -> None:
        from AppKit import NSApplication  # type: ignore[import-not-found]

        if self._panel is None:
            self._build()
        appearing = not self._panel.isVisible()
        self._apply(view)
        if appearing:
            self._panel.center()
        app = NSApplication.sharedApplication()
        if activate:
            app.activateIgnoringOtherApps_(True)
            self._panel.makeKeyAndOrderFront_(None)
        else:
            self._panel.orderFrontRegardless()
            app.requestUserAttention_(10)  # NSInformationalRequest: one Dock bounce

    def render(self, view: UpdateView) -> None:
        if self._panel is None or not self._panel.isVisible():
            self._view = None  # Re-apply everything on the next show
            return
        self._apply(view)

    def hide(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)

    # ------------------------------------------------------------------

    def _build(self) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSBackingStoreBuffered,
            NSBezelBorder,
            NSBezelStyleRounded,
            NSButton,
            NSColor,
            NSFont,
            NSFontWeightSemibold,
            NSPanel,
            NSProgressIndicator,
            NSScrollView,
            NSTextField,
            NSTextView,
            NSViewHeightSizable,
            NSViewMaxYMargin,
            NSViewMinXMargin,
            NSViewMinYMargin,
            NSViewWidthSizable,
        )
        from Foundation import NSMakeRect, NSMakeSize  # type: ignore[import-not-found]

        width, height = WINDOW_WIDTH, WINDOW_HEIGHT_WITH_NOTES
        style = 1 | 2 | (1 << 7)  # titled, closable, non-activating panel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height), style, NSBackingStoreBuffered, False
        )
        panel.setTitle_("PulseScribe Update")
        panel.setReleasedWhenClosed_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(False)
        content = panel.contentView()
        inner = width - 2 * PADDING
        top_anchor = NSViewMinYMargin | NSViewWidthSizable

        def label(y: int, h: int, font, color):
            field = NSTextField.labelWithString_("")
            field.setFrame_(NSMakeRect(PADDING, y, inner, h))
            field.setFont_(font)
            field.setTextColor_(color)
            field.setAutoresizingMask_(top_anchor)
            try:
                field.setLineBreakMode_(0)  # Word wrap
                field.setMaximumNumberOfLines_(3)
            except Exception:
                pass
            content.addSubview_(field)
            return field

        y = height - PADDING - HEADLINE_HEIGHT
        self._headline = label(
            y, HEADLINE_HEIGHT,
            NSFont.systemFontOfSize_weight_(15, NSFontWeightSemibold),
            NSColor.labelColor(),
        )
        y -= 4 + DETAIL_HEIGHT
        self._detail = label(
            y, DETAIL_HEIGHT, NSFont.systemFontOfSize_(12),
            NSColor.secondaryLabelColor(),
        )
        y -= 6 + STATUS_ROW_HEIGHT
        progress = NSProgressIndicator.alloc().initWithFrame_(
            NSMakeRect(PADDING, y + 3, inner, 12)
        )
        progress.setStyle_(0)  # Bar
        progress.setMinValue_(0)
        progress.setMaxValue_(100)
        progress.setAutoresizingMask_(top_anchor)
        content.addSubview_(progress)
        self._progress = progress
        self._notice = label(
            y, STATUS_ROW_HEIGHT, NSFont.systemFontOfSize_(12),
            NSColor.systemOrangeColor(),
        )
        y -= 10

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(PADDING, NOTES_BOTTOM, inner, y - NOTES_BOTTOM)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        size = scroll.contentSize()
        notes = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, size.width, size.height)
        )
        notes.setEditable_(False)
        notes.setSelectable_(True)
        notes.setRichText_(False)
        notes.setFont_(NSFont.systemFontOfSize_(12))
        notes.setTextContainerInset_(NSMakeSize(6, 8))
        notes.setVerticallyResizable_(True)
        notes.setHorizontallyResizable_(False)
        notes.setAutoresizingMask_(NSViewWidthSizable)
        notes.setMinSize_(NSMakeSize(0, size.height))
        notes.setMaxSize_(NSMakeSize(1e7, 1e7))
        notes.textContainer().setWidthTracksTextView_(True)
        scroll.setDocumentView_(notes)
        content.addSubview_(scroll)
        self._notes_scroll = scroll
        self._notes = notes

        def button(title: str, x: int, w: int, key: str, callback):
            item = NSButton.alloc().initWithFrame_(
                NSMakeRect(x, PADDING, w, BUTTON_HEIGHT)
            )
            item.setTitle_(title)
            item.setBezelStyle_(NSBezelStyleRounded)
            item.setKeyEquivalent_(key)
            item.setAutoresizingMask_(NSViewMinXMargin | NSViewMaxYMargin)
            handler = _handler_class().alloc().initWithCallback_(callback)
            item.setTarget_(handler)
            item.setAction_("performAction:")
            self._handlers.append(handler)
            content.addSubview_(item)
            return item

        primary_w, close_w = 170, 100
        self._primary = button(
            "", width - PADDING - primary_w, primary_w, "\r", self._on_primary
        )
        self._close = button(
            "Close",
            width - PADDING - primary_w - 10 - close_w,
            close_w,
            "\x1b",
            lambda: self._on_action("close"),
        )
        self._panel = panel

    def _on_primary(self) -> None:
        view = self._view
        if view is not None and view.primary_action:
            self._on_action(view.primary_action)

    def _apply(self, view: UpdateView) -> None:
        """Update only the parts that changed (download progress is frequent)."""
        previous = self._view
        self._view = view
        if previous is None or previous.headline != view.headline:
            self._headline.setStringValue_(view.headline)
        if previous is None or previous.detail != view.detail:
            self._detail.setStringValue_(view.detail)
        if previous is None or previous.notice != view.notice:
            self._notice.setStringValue_(view.notice)
            self._notice.setHidden_(not view.notice)
        if previous is None or previous.progress != view.progress:
            self._apply_progress(previous, view.progress)
        if previous is None or previous.notes != view.notes:
            self._notes.setString_(view.notes)
            self._notes.scrollRangeToVisible_((0, 0))
            self._notes_scroll.setHidden_(not view.notes)
            self._resize(bool(view.notes))
        if previous is None or (
            previous.primary_label,
            previous.primary_enabled,
            previous.close_label,
        ) != (view.primary_label, view.primary_enabled, view.close_label):
            self._primary.setTitle_(view.primary_label)
            self._primary.setHidden_(not view.primary_label)
            self._primary.setEnabled_(view.primary_enabled)
            self._close.setTitle_(view.close_label)
            # Without a primary action, Return closes the window.
            self._close.setKeyEquivalent_("\r" if not view.primary_label else "\x1b")

    def _apply_progress(self, previous: UpdateView | None, progress: int | None) -> None:
        bar = self._progress
        was_indeterminate = (
            previous is not None and previous.progress == PROGRESS_INDETERMINATE
        )
        if progress is None:
            if was_indeterminate:
                bar.stopAnimation_(None)
            bar.setHidden_(True)
            return
        bar.setHidden_(False)
        if progress == PROGRESS_INDETERMINATE:
            bar.setIndeterminate_(True)
            bar.startAnimation_(None)
            return
        if was_indeterminate:
            bar.stopAnimation_(None)
        bar.setIndeterminate_(False)
        bar.setDoubleValue_(float(progress))

    def _resize(self, with_notes: bool) -> None:
        from Foundation import NSMakeRect  # type: ignore[import-not-found]

        panel = self._panel
        content_height = WINDOW_HEIGHT_WITH_NOTES if with_notes else WINDOW_HEIGHT_COMPACT
        frame = panel.frame()
        new_frame = panel.frameRectForContentRect_(
            NSMakeRect(0, 0, WINDOW_WIDTH, content_height)
        )
        top = frame.origin.y + frame.size.height
        panel.setFrame_display_(
            NSMakeRect(
                frame.origin.x,
                top - new_frame.size.height,
                new_frame.size.width,
                new_frame.size.height,
            ),
            True,
        )


__all__ = ["MacUpdateWindow"]
