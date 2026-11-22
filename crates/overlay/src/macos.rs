//! Native macOS 60 FPS non-activating floating overlay (`NSPanel` + Core Animation `CALayer`).
//!
//! Every `unsafe` FFI/AppKit call in `crates/overlay` is isolated to this module.
//! - Uses `NSPanel` with `NonactivatingPanel` + level `25` (`kCGStatusWindowLevel` / above
//!   normal & fullscreen windows) and `orderFrontRegardless()` so showing/updating NEVER steals
//!   keyboard focus.
//! - Click-through by default (`ignoresMouseEvents = true`), turning interactive only when
//!   showing a dismissible error or clickable `"Settings"` link.
//! - Adaptive `CFRunLoopTimer` (60 Hz active / 24 Hz decay / invalidated at rest -> 0 FPS idle).
//! - GPU-composited `CAGradientLayer` shimmer (`CABasicAnimation`) during `Loading`,
//!   `Transcribing`, and `Refining` (0 CPU timer wakeups; disabled when macOS Reduce Motion is on).

#![allow(unsafe_code)]

use std::cell::{Cell, RefCell};
use std::collections::VecDeque;
use std::ffi::c_void;
use std::ptr::{self, NonNull};
use std::rc::Rc;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::{define_class, msg_send, sel, DefinedClass, MainThreadOnly};
use objc2_app_kit::{
    NSBackingStoreType, NSButton, NSColor, NSFont, NSFontWeightMedium, NSFontWeightRegular,
    NSFontWeightSemibold, NSLineBreakMode, NSPanel, NSScreen, NSTextAlignment, NSTextField, NSView,
    NSWindowCollectionBehavior, NSWindowStyleMask, NSWorkspace,
};
use objc2_core_foundation::{
    kCFRunLoopCommonModes, kCFRunLoopDefaultMode, CFAbsoluteTimeGetCurrent, CFRetained, CFRunLoop,
    CFRunLoopSource, CFRunLoopSourceContext, CFRunLoopTimer, CFRunLoopTimerContext, CGFloat,
    CGPoint, CGRect, CGSize,
};
use objc2_core_graphics::CGColor;
use objc2_foundation::{MainThreadMarker, NSArray, NSNumber, NSObject, NSString};
use objc2_quartz_core::{
    CAAnimation, CABasicAnimation, CAGradientLayer, CALayer, CAMediaTiming, CATransaction,
};
use pulsescribe_core::animation::{BAR_COUNT, BAR_GAP, BAR_MIN_HEIGHT, BAR_WIDTH};
use pulsescribe_core::overlay_style::{
    compute_overlay_layout, hex_to_rgb, OverlayBarMode, OverlayLayoutParams, OverlayTone,
    ShimmerTrack, BACKGROUND_COLOR, BADGE_CORNER_RADIUS, BADGE_FONT_SIZE, BADGE_PADDING_H,
    BADGE_PADDING_V, BAR_AREA_WIDTH, BORDER_COLOR, DISMISS_FONT_SIZE, DISMISS_GLYPH, LINE_HEIGHT,
    LINK_TEXT_COLOR, LIVE_TEXT_LINES, LIVE_TEXT_LINE_PITCH, META_FONT_SIZE, META_TEXT_COLOR,
    MUTED_TEXT_COLOR, OVERLAY_CORNER_RADIUS, OVERLAY_HEIGHT, OVERLAY_MAX_WIDTH_RATIO,
    OVERLAY_WIDTH, PHASE_STEP_GAP, PHASE_STEP_WIDTH, SHIMMER_ACTIVE_STEP_COLOR, SHIMMER_BAND_WIDTH,
    SHIMMER_HIGHLIGHT_COLOR, SHIMMER_PERIOD_SECONDS, TEXT_COLOR, TEXT_FONT_SIZE,
};

use crate::model::{
    OverlayActionEvent, OverlayCommand, OverlayModel, OverlaySnapshot, OverlayTimerMode,
    OVERLAY_MARGIN_BOTTOM,
};
use crate::{OverlayHandle, OverlaySink};

/// Floating level above normal and fullscreen windows (`kCGStatusWindowLevel` = 25).
const OVERLAY_WINDOW_LEVEL: isize = 25;
const SHIMMER_ANIMATION_KEY: &str = "pulsescribe.shimmer";
const CLOCK_INTERVAL_SECS: f64 = 1.0;

struct SendRunLoopWakeup {
    run_loop: CFRetained<CFRunLoop>,
    source: CFRetained<CFRunLoopSource>,
}

// SAFETY: `CFRunLoopSourceSignal` and `CFRunLoopWakeUp` are documented thread-safe Core Foundation
// functions explicitly designed to signal a `CFRunLoop` from background worker threads.
unsafe impl Send for SendRunLoopWakeup {}
unsafe impl Sync for SendRunLoopWakeup {}

struct SharedQueue {
    commands: Mutex<VecDeque<OverlayCommand>>,
    wakeup: Mutex<Option<SendRunLoopWakeup>>,
}

impl SharedQueue {
    fn new() -> Self {
        Self {
            commands: Mutex::new(VecDeque::new()),
            wakeup: Mutex::new(None),
        }
    }

    fn attach_wakeup(&self, run_loop: CFRetained<CFRunLoop>, source: CFRetained<CFRunLoopSource>) {
        let mut guard = self.wakeup.lock().unwrap_or_else(|e| e.into_inner());
        *guard = Some(SendRunLoopWakeup { run_loop, source });
    }

    fn detach_wakeup(&self) {
        let mut guard = self.wakeup.lock().unwrap_or_else(|e| e.into_inner());
        *guard = None;
    }

    fn push(&self, cmd: OverlayCommand) {
        {
            let mut q = self.commands.lock().unwrap_or_else(|e| e.into_inner());
            // Coalesce consecutive AudioLevel updates so a burst never builds a backlog.
            if matches!(cmd, OverlayCommand::AudioLevel(_))
                && matches!(q.back(), Some(OverlayCommand::AudioLevel(_)))
            {
                let _ = q.pop_back();
            }
            q.push_back(cmd);
        }
        let guard = self.wakeup.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(wakeup) = guard.as_ref() {
            wakeup.source.signal();
            wakeup.run_loop.wake_up();
        }
    }

    fn drain(&self) -> Vec<OverlayCommand> {
        let mut q = self.commands.lock().unwrap_or_else(|e| e.into_inner());
        q.drain(..).collect()
    }
}

struct MacOsOverlaySink {
    queue: Arc<SharedQueue>,
}

impl OverlaySink for MacOsOverlaySink {
    fn send(&self, cmd: OverlayCommand) {
        self.queue.push(cmd);
    }
}

struct ButtonTargetIvars {
    queue: Arc<SharedQueue>,
    on_action: Arc<dyn Fn(OverlayActionEvent) + Send + Sync>,
}

define_class!(
    #[unsafe(super(NSObject))]
    #[thread_kind = MainThreadOnly]
    #[ivars = ButtonTargetIvars]
    struct OverlayButtonTarget;

    impl OverlayButtonTarget {
        #[unsafe(method(onSettingsClicked:))]
        fn on_settings_clicked(&self, _sender: Option<&AnyObject>) {
            (self.ivars().on_action)(OverlayActionEvent::OpenSettings);
            self.ivars().queue.push(OverlayCommand::Dismiss);
        }

        #[unsafe(method(onDismissClicked:))]
        fn on_dismiss_clicked(&self, _sender: Option<&AnyObject>) {
            (self.ivars().on_action)(OverlayActionEvent::Dismissed);
            self.ivars().queue.push(OverlayCommand::Dismiss);
        }
    }
);

impl OverlayButtonTarget {
    fn new(
        mtm: MainThreadMarker,
        queue: Arc<SharedQueue>,
        on_action: Arc<dyn Fn(OverlayActionEvent) + Send + Sync>,
    ) -> Retained<Self> {
        let this = Self::alloc(mtm).set_ivars(ButtonTargetIvars { queue, on_action });
        unsafe { msg_send![super(this), init] }
    }
}

struct OverlayInner {
    mtm: MainThreadMarker,
    queue: Arc<SharedQueue>,
    model: OverlayModel,
    panel: Retained<NSPanel>,
    root_view: Retained<NSView>,
    bar_layers: Vec<Retained<CALayer>>,
    line_primary: Retained<CALayer>,
    line_secondary: Retained<CALayer>,
    shimmer_clip: Retained<CALayer>,
    shimmer_gradient: Retained<CAGradientLayer>,
    badge_label: Retained<NSTextField>,
    main_label: Retained<NSTextField>,
    meta_label: Retained<NSTextField>,
    settings_button: Retained<NSButton>,
    dismiss_button: Retained<NSButton>,
    _button_target: Retained<OverlayButtonTarget>,
    last_bar_mode: OverlayBarMode,
    last_shimmer: Option<ShimmerTrack>,
    last_panel_size: (f64, f64),
    anim_timer: Option<CFRetained<CFRunLoopTimer>>,
    anim_timer_mode: OverlayTimerMode,
    clock_timer: Option<CFRetained<CFRunLoopTimer>>,
    source: Option<CFRetained<CFRunLoopSource>>,
    run_loop: CFRetained<CFRunLoop>,
    shutting_down: Cell<bool>,
}

/// Native macOS overlay controller owned on the AppKit main thread.
///
/// Dropping `MacOsOverlay` invalidates all Core Foundation runloop timers/sources, clears button
/// targets, and closes the floating `NSPanel` cleanly.
pub struct MacOsOverlay {
    inner: Rc<RefCell<OverlayInner>>,
    /// Raw pointer to `Rc<RefCell<OverlayInner>>` stored as `CFRunLoopSource`/`Timer` context,
    /// freed strictly in `Drop` AFTER all timers and the runloop source have been invalidated.
    ctx_box: *mut Rc<RefCell<OverlayInner>>,
}

impl MacOsOverlay {
    /// Create the native floating `NSPanel` overlay on the AppKit main thread and return a
    /// thread-safe [`OverlayHandle`] for driving it from background Tokio tasks.
    pub fn new(
        mtm: MainThreadMarker,
        on_action: impl Fn(OverlayActionEvent) + Send + Sync + 'static,
    ) -> Result<(Self, OverlayHandle), String> {
        let run_loop = CFRunLoop::main()
            .ok_or_else(|| "CFRunLoop::main() unavailable on current thread".to_owned())?;
        let queue = Arc::new(SharedQueue::new());
        let action_cb: Arc<dyn Fn(OverlayActionEvent) + Send + Sync> = Arc::new(on_action);
        let button_target = OverlayButtonTarget::new(mtm, Arc::clone(&queue), action_cb);

        let initial_rect = CGRect::new(
            CGPoint::new(0.0, 0.0),
            CGSize::new(OVERLAY_WIDTH, OVERLAY_HEIGHT),
        );
        let style = NSWindowStyleMask::Borderless | NSWindowStyleMask::NonactivatingPanel;
        let panel = NSPanel::initWithContentRect_styleMask_backing_defer(
            NSPanel::alloc(mtm),
            initial_rect,
            style,
            NSBackingStoreType::Buffered,
            false,
        );
        panel.setLevel(OVERLAY_WINDOW_LEVEL);
        panel.setHidesOnDeactivate(false);
        panel.setHasShadow(true);
        panel.setOpaque(false);
        panel.setBackgroundColor(Some(&NSColor::clearColor()));
        panel.setIgnoresMouseEvents(true);
        panel.setCollectionBehavior(
            NSWindowCollectionBehavior::CanJoinAllSpaces
                | NSWindowCollectionBehavior::FullScreenAuxiliary
                | NSWindowCollectionBehavior::Stationary,
        );
        // Prevent AppKit from freeing the panel early behind our `Retained<NSPanel>`.
        unsafe { panel.setReleasedWhenClosed(false) };

        let root_view = NSView::initWithFrame(NSView::alloc(mtm), initial_rect);
        root_view.setWantsLayer(true);
        let root_layer = root_view
            .layer()
            .ok_or_else(|| "NSView did not create a backing CALayer".to_owned())?;
        root_layer.setBackgroundColor(Some(&cg_color_from_hex(BACKGROUND_COLOR, 1.0)));
        root_layer.setBorderColor(Some(&cg_color_from_hex(BORDER_COLOR, 1.0)));
        root_layer.setBorderWidth(1.0);
        root_layer.setCornerRadius(OVERLAY_CORNER_RADIUS);
        root_layer.setMasksToBounds(true);

        // 10 audio equalizer bars.
        let mut bar_layers = Vec::with_capacity(BAR_COUNT);
        let bar_color = cg_color_from_hex("#ececec", 1.0);
        for _ in 0..BAR_COUNT {
            let layer = CALayer::layer();
            layer.setBackgroundColor(Some(&bar_color));
            layer.setCornerRadius(BAR_WIDTH / 2.0);
            layer.setBounds(CGRect::new(
                CGPoint::new(0.0, 0.0),
                CGSize::new(BAR_WIDTH, BAR_MIN_HEIGHT),
            ));
            layer.setHidden(true);
            root_layer.addSublayer(&layer);
            bar_layers.push(layer);
        }

        // Processing lines (`Line`, `Phase1`, `Phase2`) + clipped Core Animation shimmer gradient.
        let line_primary = CALayer::layer();
        line_primary.setCornerRadius(LINE_HEIGHT / 2.0);
        line_primary.setHidden(true);
        root_layer.addSublayer(&line_primary);

        let line_secondary = CALayer::layer();
        line_secondary.setCornerRadius(LINE_HEIGHT / 2.0);
        line_secondary.setHidden(true);
        root_layer.addSublayer(&line_secondary);

        let shimmer_clip = CALayer::layer();
        shimmer_clip.setCornerRadius(LINE_HEIGHT / 2.0);
        shimmer_clip.setMasksToBounds(true);
        shimmer_clip.setHidden(true);
        let shimmer_gradient = CAGradientLayer::layer();
        shimmer_gradient.setStartPoint(CGPoint::new(0.0, 0.5));
        shimmer_gradient.setEndPoint(CGPoint::new(1.0, 0.5));
        let transparent_hl = cg_color_from_hex(SHIMMER_HIGHLIGHT_COLOR, 0.0);
        let solid_hl = cg_color_from_hex(SHIMMER_HIGHLIGHT_COLOR, 0.92);
        let colors = build_cgcolor_array(&[&transparent_hl, &solid_hl, &transparent_hl]);
        unsafe { shimmer_gradient.setColors(Some(&colors)) };
        shimmer_clip.addSublayer(&shimmer_gradient);
        root_layer.addSublayer(&shimmer_clip);

        // Outcome badge pill (`Pasted`, `Copied`, `No speech`, `Error`, `Not pasted`).
        let badge_label = make_label(mtm, BADGE_FONT_SIZE, unsafe { NSFontWeightSemibold });
        badge_label.setAlignment(NSTextAlignment::Center);
        badge_label.setWantsLayer(true);
        if let Some(layer) = badge_label.layer() {
            layer.setCornerRadius(BADGE_CORNER_RADIUS);
            layer.setMasksToBounds(true);
        }
        badge_label.setHidden(true);
        root_view.addSubview(&badge_label);

        // Main status / live interim transcript label.
        let main_label = make_label(mtm, TEXT_FONT_SIZE, unsafe { NSFontWeightRegular });
        main_label.setTextColor(Some(&ns_color_from_hex(TEXT_COLOR, 1.0)));
        main_label.setLineBreakMode(NSLineBreakMode::ByTruncatingTail);
        main_label.setMaximumNumberOfLines(1);
        root_view.addSubview(&main_label);

        // Right-aligned meta label (`0:04`, `10:00`, `1 of 2`, `1.2 s`) using monospaced digits
        // so the 1 Hz recording clock never jitters horizontally.
        let meta_label = NSTextField::labelWithString(&NSString::from_str(""), mtm);
        meta_label.setFont(Some(&NSFont::monospacedDigitSystemFontOfSize_weight(
            META_FONT_SIZE,
            unsafe { NSFontWeightRegular },
        )));
        meta_label.setDrawsBackground(false);
        meta_label.setTextColor(Some(&ns_color_from_hex(META_TEXT_COLOR, 1.0)));
        meta_label.setAlignment(NSTextAlignment::Right);
        meta_label.setHidden(true);
        root_view.addSubview(&meta_label);

        // Clickable "Settings" button shown on actionable error states.
        let settings_button = NSButton::initWithFrame(
            NSButton::alloc(mtm),
            CGRect::new(CGPoint::new(0.0, 0.0), CGSize::new(60.0, 22.0)),
        );
        settings_button.setTitle(&NSString::from_str("Settings"));
        settings_button.setBordered(false);
        settings_button.setFont(Some(&NSFont::systemFontOfSize_weight(
            META_FONT_SIZE,
            unsafe { NSFontWeightMedium },
        )));
        settings_button.setContentTintColor(Some(&ns_color_from_hex(LINK_TEXT_COLOR, 1.0)));
        unsafe {
            settings_button.setTarget(Some(&button_target));
            settings_button.setAction(Some(sel!(onSettingsClicked:)));
        }
        settings_button.setHidden(true);
        root_view.addSubview(&settings_button);

        // Clickable "×" dismiss button shown on dismissible error toasts.
        let dismiss_button = NSButton::initWithFrame(
            NSButton::alloc(mtm),
            CGRect::new(CGPoint::new(0.0, 0.0), CGSize::new(20.0, 22.0)),
        );
        dismiss_button.setTitle(&NSString::from_str(DISMISS_GLYPH));
        dismiss_button.setBordered(false);
        dismiss_button.setFont(Some(&NSFont::systemFontOfSize_weight(
            DISMISS_FONT_SIZE,
            unsafe { NSFontWeightRegular },
        )));
        dismiss_button.setContentTintColor(Some(&ns_color_from_hex(MUTED_TEXT_COLOR, 1.0)));
        unsafe {
            dismiss_button.setTarget(Some(&button_target));
            dismiss_button.setAction(Some(sel!(onDismissClicked:)));
        }
        dismiss_button.setHidden(true);
        root_view.addSubview(&dismiss_button);

        panel.setContentView(Some(&root_view));
        panel.orderOut(None);

        let inner = Rc::new(RefCell::new(OverlayInner {
            mtm,
            queue: Arc::clone(&queue),
            model: OverlayModel::new(Instant::now()),
            panel,
            root_view,
            bar_layers,
            line_primary,
            line_secondary,
            shimmer_clip,
            shimmer_gradient,
            badge_label,
            main_label,
            meta_label,
            settings_button,
            dismiss_button,
            _button_target: button_target,
            last_bar_mode: OverlayBarMode::None,
            last_shimmer: None,
            last_panel_size: (OVERLAY_WIDTH, OVERLAY_HEIGHT),
            anim_timer: None,
            anim_timer_mode: OverlayTimerMode::Stopped,
            clock_timer: None,
            source: None,
            run_loop: run_loop.clone(),
            shutting_down: Cell::new(false),
        }));

        let ctx_box = Box::into_raw(Box::new(Rc::clone(&inner)));
        let mut source_ctx = CFRunLoopSourceContext {
            version: 0,
            info: ctx_box.cast::<c_void>(),
            retain: None,
            release: None,
            copyDescription: None,
            equal: None,
            hash: None,
            schedule: None,
            cancel: None,
            perform: Some(runloop_source_perform),
        };
        let source = match unsafe { CFRunLoopSource::new(None, 0, &mut source_ctx) } {
            Some(src) => src,
            None => {
                // Reclaim `ctx_box` on construction failure so `OverlayInner`/`NSPanel` never leak.
                unsafe { drop(Box::from_raw(ctx_box)) };
                return Err("CFRunLoopSourceCreate returned null".to_owned());
            }
        };
        run_loop.add_source(Some(&source), unsafe { kCFRunLoopCommonModes });
        queue.attach_wakeup(run_loop, source.clone());
        inner.borrow_mut().source = Some(source);

        let handle = OverlayHandle::from_sink(Arc::new(MacOsOverlaySink { queue }));
        Ok((Self { inner, ctx_box }, handle))
    }

    /// Synchronously drain any queued commands on the main thread (useful in smoke tests).
    pub fn process_pending(&self) {
        let mut inner = self.inner.borrow_mut();
        inner.drain_and_apply(self.ctx_box);
    }

    /// Whether the underlying `NSPanel` is currently visible on screen.
    pub fn is_visible(&self) -> bool {
        self.inner.borrow().panel.isVisible()
    }

    /// Whether the `NSPanel` became the key window (must always remain `false` for zero focus steal).
    pub fn is_key_window(&self) -> bool {
        self.inner.borrow().panel.isKeyWindow()
    }

    /// Current animation timer pacing (`Stopped` = 0 FPS, 0 wakeups).
    pub fn timer_mode(&self) -> OverlayTimerMode {
        self.inner.borrow().anim_timer_mode
    }
}

/// Pump the macOS main `CFRunLoop` for `duration` (helper for standalone tests and examples).
pub fn pump_main_run_loop(_mtm: MainThreadMarker, duration: std::time::Duration) {
    let deadline = Instant::now() + duration;
    while Instant::now() < deadline {
        let remaining = deadline
            .saturating_duration_since(Instant::now())
            .as_secs_f64()
            .min(0.016);
        CFRunLoop::run_in_mode(unsafe { kCFRunLoopDefaultMode }, remaining, false);
    }
}

impl Drop for MacOsOverlay {
    fn drop(&mut self) {
        {
            let mut inner = self.inner.borrow_mut();
            inner.teardown();
        }
        // SAFETY: All `CFRunLoopTimer`s and `CFRunLoopSource` referencing `self.ctx_box` were
        // invalidated synchronously on the main thread inside `teardown()` above, so no callback
        // can ever dereference `self.ctx_box` again.
        unsafe {
            drop(Box::from_raw(self.ctx_box));
        }
    }
}

impl OverlayInner {
    fn teardown(&mut self) {
        if self.shutting_down.replace(true) {
            return;
        }
        self.queue.detach_wakeup();
        self.stop_anim_timer();
        self.stop_clock_timer();
        self.stop_shimmer();
        if let Some(source) = self.source.take() {
            source.invalidate();
        }
        unsafe {
            self.settings_button.setTarget(None);
            self.settings_button.setAction(None);
            self.dismiss_button.setTarget(None);
            self.dismiss_button.setAction(None);
        }
        self.panel.orderOut(None);
        self.panel.close();
    }

    fn drain_and_apply(&mut self, ctx_ptr: *mut Rc<RefCell<OverlayInner>>) {
        if self.shutting_down.get() {
            return;
        }
        let commands = self.queue.drain();
        if commands.is_empty() {
            return;
        }

        let now = Instant::now();
        let mut any_visual_change = false;
        for cmd in commands {
            any_visual_change |= self.model.apply_command_mut(cmd, now);
        }

        if self.model.is_shutdown() {
            self.teardown();
            return;
        }

        if any_visual_change {
            let snap = self.model.snapshot(now);
            self.render_snapshot(&snap, ctx_ptr);
        } else {
            // AudioLevel-only batch: wake or pace the wave timer with zero AppKit allocations.
            self.sync_anim_timer(self.model.timer_mode(), ctx_ptr);
        }
    }

    fn render_snapshot(&mut self, snap: &OverlaySnapshot, ctx_ptr: *mut Rc<RefCell<OverlayInner>>) {
        if !snap.visible {
            self.stop_anim_timer();
            self.stop_clock_timer();
            self.stop_shimmer();
            self.panel.setIgnoresMouseEvents(true);
            self.panel.orderOut(None);
            return;
        }

        let panel_height = snap.base_size.1;
        let center_y = panel_height / 2.0;

        // 1. Update text, badge, and action controls before measuring layout widths.
        self.main_label
            .setStringValue(&NSString::from_str(&snap.display_text));
        self.main_label.setTextColor(Some(&ns_color_from_hex(
            if snap.content.emphasized || snap.live_text_active {
                TEXT_COLOR
            } else {
                MUTED_TEXT_COLOR
            },
            1.0,
        )));
        self.main_label
            .setMaximumNumberOfLines(if snap.live_text_active {
                LIVE_TEXT_LINES as isize
            } else {
                1
            });
        self.main_label.setLineBreakMode(if snap.live_text_active {
            NSLineBreakMode::ByWordWrapping
        } else {
            NSLineBreakMode::ByTruncatingTail
        });

        let has_badge = !snap.content.badge.is_empty();
        let badge_width = if has_badge {
            let (bg_hex, fg_hex) = snap
                .content
                .tone
                .unwrap_or(OverlayTone::Success)
                .badge_colors();
            self.badge_label
                .setStringValue(&NSString::from_str(&snap.content.badge));
            self.badge_label
                .setTextColor(Some(&ns_color_from_hex(fg_hex, 1.0)));
            self.badge_label.setDrawsBackground(true);
            self.badge_label
                .setBackgroundColor(Some(&ns_color_from_hex(bg_hex, 1.0)));
            self.badge_label.setHidden(false);
            let fit = self.badge_label.fittingSize();
            (fit.width.ceil() + 2.0 * BADGE_PADDING_H).max(40.0)
        } else {
            self.badge_label.setHidden(true);
            0.0
        };

        let show_settings_btn = snap.action.is_some();
        let show_meta_label = !show_settings_btn && !snap.display_meta.is_empty();
        let meta_width = if show_settings_btn {
            self.meta_label.setHidden(true);
            self.settings_button.setHidden(false);
            self.settings_button.fittingSize().width.ceil().max(54.0)
        } else if show_meta_label {
            self.settings_button.setHidden(true);
            self.meta_label
                .setStringValue(&NSString::from_str(&snap.display_meta));
            self.meta_label.setHidden(false);
            // Reserve room for `10:00`+ when the 1 Hz recording clock is active so ticking
            // never clips or requires resizing the overlay.
            let min_w = if snap.clock_active { 46.0 } else { 32.0 };
            self.meta_label.fittingSize().width.ceil().max(min_w)
        } else {
            self.settings_button.setHidden(true);
            self.meta_label.setHidden(true);
            0.0
        };

        let dismiss_width = if snap.content.dismissible {
            self.dismiss_button.setHidden(false);
            18.0
        } else {
            self.dismiss_button.setHidden(true);
            0.0
        };

        // 2. Compute horizontal layout via shared `pulsescribe_core::compute_overlay_layout`.
        let screen_frame = active_screen_frame(self.mtm);
        let max_width = (screen_frame.size.width * OVERLAY_MAX_WIDTH_RATIO).max(snap.base_size.0);
        let measured_text_w = self.main_label.fittingSize().width.ceil();
        let layout = compute_overlay_layout(&OverlayLayoutParams {
            text_width: measured_text_w,
            meta_width,
            dismiss_width,
            status_width: if has_badge {
                badge_width
            } else {
                BAR_AREA_WIDTH
            },
            grow: snap.allow_grow,
            max_width,
            base_width: snap.base_size.0,
        });

        let panel_width = layout.width.round();
        let origin_x =
            (screen_frame.origin.x + (screen_frame.size.width - panel_width) / 2.0).round();
        let origin_y = (screen_frame.origin.y + OVERLAY_MARGIN_BOTTOM).round();
        let panel_frame = CGRect::new(
            CGPoint::new(origin_x, origin_y),
            CGSize::new(panel_width, panel_height),
        );

        let size_changed = self.last_panel_size != (panel_width, panel_height);
        self.panel.setFrame_display(panel_frame, false);
        if size_changed {
            self.root_view.setFrame(CGRect::new(
                CGPoint::new(0.0, 0.0),
                CGSize::new(panel_width, panel_height),
            ));
            self.last_panel_size = (panel_width, panel_height);
        }

        // 3. Position subviews & CALayers inside one implicit-action-disabled transaction.
        CATransaction::begin();
        CATransaction::setDisableActions(true);

        if has_badge {
            let fit_h = self.badge_label.fittingSize().height.ceil();
            let badge_h = (fit_h + 2.0 * BADGE_PADDING_V).max(BADGE_FONT_SIZE + 6.0);
            self.badge_label.setFrame(CGRect::new(
                CGPoint::new(layout.status_x, (center_y - badge_h / 2.0).round()),
                CGSize::new(badge_width, badge_h),
            ));
        }

        let text_h = if snap.live_text_active && snap.live_line_count > 1 {
            (LIVE_TEXT_LINE_PITCH * (snap.live_line_count as f64) + 2.0)
                .min(panel_height - 10.0)
                .max(20.0)
        } else {
            self.main_label
                .fittingSize()
                .height
                .ceil()
                .clamp(18.0, 22.0)
        };
        self.main_label.setFrame(CGRect::new(
            CGPoint::new(layout.text_x, (center_y - text_h / 2.0).round()),
            CGSize::new(layout.text_width.max(0.0), text_h),
        ));

        if let Some(meta_x) = layout.meta_x {
            let h = 20.0;
            let frame = CGRect::new(
                CGPoint::new(meta_x, (center_y - h / 2.0).round()),
                CGSize::new(meta_width, h),
            );
            if show_settings_btn {
                self.settings_button.setFrame(frame);
            } else {
                self.meta_label.setFrame(frame);
            }
        }
        if let Some(dismiss_x) = layout.dismiss_x {
            let h = 22.0;
            self.dismiss_button.setFrame(CGRect::new(
                CGPoint::new(dismiss_x, (center_y - h / 2.0).round()),
                CGSize::new(dismiss_width, h),
            ));
        }

        self.apply_status_visuals(snap, layout.status_x, center_y);

        CATransaction::commit();

        // 4. Click-through toggle + non-activating reveal.
        self.panel.setIgnoresMouseEvents(!snap.interactive);
        if !self.panel.isVisible() {
            self.panel.orderFrontRegardless();
        }

        // 5. Sync adaptive 60/24/0 Hz animation timer and 1 Hz recording clock timer.
        self.sync_anim_timer(snap.timer_mode, ctx_ptr);
        self.sync_clock_timer(snap.clock_active, ctx_ptr);
    }

    fn apply_status_visuals(&mut self, snap: &OverlaySnapshot, status_x: f64, center_y: f64) {
        let mode = snap.content.bars;
        let show_bars = matches!(mode, OverlayBarMode::Level | OverlayBarMode::Low);
        let mode_changed = self.last_bar_mode != mode;
        self.last_bar_mode = mode;

        if show_bars {
            if mode_changed {
                let color_hex = mode.bar_color().unwrap_or("#ececec");
                let cg = cg_color_from_hex(color_hex, 1.0);
                for bar in &self.bar_layers {
                    bar.setBackgroundColor(Some(&cg));
                    bar.setHidden(false);
                }
            }
            for (i, bar) in self.bar_layers.iter().enumerate() {
                let x = status_x + (i as f64) * (BAR_WIDTH + BAR_GAP) + BAR_WIDTH / 2.0;
                bar.setPosition(CGPoint::new(x, center_y));
                bar.setBounds(CGRect::new(
                    CGPoint::new(0.0, 0.0),
                    CGSize::new(BAR_WIDTH, snap.bar_heights[i]),
                ));
            }
            self.line_primary.setHidden(true);
            self.line_secondary.setHidden(true);
            self.stop_shimmer();
            return;
        }

        for bar in &self.bar_layers {
            bar.setHidden(true);
        }

        let line_y = (center_y - LINE_HEIGHT / 2.0).round();
        match mode {
            OverlayBarMode::Line => {
                let color = cg_color_from_hex(mode.bar_color().unwrap_or("#6f6f6f"), 1.0);
                self.line_primary.setBackgroundColor(Some(&color));
                self.line_primary.setFrame(CGRect::new(
                    CGPoint::new(status_x, line_y),
                    CGSize::new(BAR_AREA_WIDTH, LINE_HEIGHT),
                ));
                self.line_primary.setHidden(false);
                self.line_secondary.setHidden(true);
            }
            OverlayBarMode::Phase1 | OverlayBarMode::Phase2 => {
                let (c1, c2) = mode.phase_step_colors().unwrap_or(("#ececec", "#333333"));
                let shimmer_active = !reduce_motion_enabled();
                let step1_hex = if shimmer_active && mode == OverlayBarMode::Phase1 {
                    SHIMMER_ACTIVE_STEP_COLOR
                } else {
                    c1
                };
                let step2_hex = if shimmer_active && mode == OverlayBarMode::Phase2 {
                    SHIMMER_ACTIVE_STEP_COLOR
                } else {
                    c2
                };
                let step1_color = cg_color_from_hex(step1_hex, 1.0);
                let step2_color = cg_color_from_hex(step2_hex, 1.0);
                self.line_primary.setBackgroundColor(Some(&step1_color));
                self.line_primary.setFrame(CGRect::new(
                    CGPoint::new(status_x, line_y),
                    CGSize::new(PHASE_STEP_WIDTH, LINE_HEIGHT),
                ));
                self.line_primary.setHidden(false);

                self.line_secondary.setBackgroundColor(Some(&step2_color));
                self.line_secondary.setFrame(CGRect::new(
                    CGPoint::new(status_x + PHASE_STEP_WIDTH + PHASE_STEP_GAP, line_y),
                    CGSize::new(PHASE_STEP_WIDTH, LINE_HEIGHT),
                ));
                self.line_secondary.setHidden(false);
            }
            _ => {
                self.line_primary.setHidden(true);
                self.line_secondary.setHidden(true);
            }
        }

        if let Some(track) = snap.shimmer {
            self.start_or_update_shimmer(status_x + track.x, line_y, track.width, track);
        } else {
            self.stop_shimmer();
        }
    }

    fn start_or_update_shimmer(&mut self, x: f64, y: f64, width: f64, track: ShimmerTrack) {
        if reduce_motion_enabled() {
            self.stop_shimmer();
            return;
        }
        let changed = self.last_shimmer != Some(track);
        self.last_shimmer = Some(track);
        self.shimmer_clip.setFrame(CGRect::new(
            CGPoint::new(x, y),
            CGSize::new(width, LINE_HEIGHT),
        ));
        self.shimmer_clip.setHidden(false);

        if !changed {
            return;
        }

        let key = NSString::from_str(SHIMMER_ANIMATION_KEY);
        self.shimmer_gradient.removeAnimationForKey(&key);
        self.shimmer_gradient.setBounds(CGRect::new(
            CGPoint::new(0.0, 0.0),
            CGSize::new(SHIMMER_BAND_WIDTH, LINE_HEIGHT),
        ));
        let start_x = -SHIMMER_BAND_WIDTH / 2.0;
        let end_x = width + SHIMMER_BAND_WIDTH / 2.0;
        self.shimmer_gradient
            .setPosition(CGPoint::new(start_x, LINE_HEIGHT / 2.0));

        let anim = CABasicAnimation::animationWithKeyPath(Some(&NSString::from_str("position.x")));
        let from_num = NSNumber::new_f64(start_x);
        let to_num = NSNumber::new_f64(end_x);
        unsafe {
            anim.setFromValue(Some(from_num.as_ref()));
            anim.setToValue(Some(to_num.as_ref()));
        }
        anim.setDuration(SHIMMER_PERIOD_SECONDS);
        anim.setRepeatCount(f32::INFINITY);
        let ca_anim: &CAAnimation = anim.as_ref();
        self.shimmer_gradient
            .addAnimation_forKey(ca_anim, Some(&key));
    }

    fn stop_shimmer(&mut self) {
        if self.last_shimmer.is_none() && self.shimmer_clip.isHidden() {
            return;
        }
        self.last_shimmer = None;
        let key = NSString::from_str(SHIMMER_ANIMATION_KEY);
        self.shimmer_gradient.removeAnimationForKey(&key);
        self.shimmer_clip.setHidden(true);
    }

    fn on_anim_tick(&mut self, ctx_ptr: *mut Rc<RefCell<OverlayInner>>) {
        if self.shutting_down.get() {
            self.stop_anim_timer();
            return;
        }
        let tick = self.model.tick_animation(Instant::now());
        if tick.any_bar_changed {
            CATransaction::begin();
            CATransaction::setDisableActions(true);
            for (i, changed) in tick.changed_bars.iter().enumerate() {
                if *changed {
                    self.bar_layers[i].setBounds(CGRect::new(
                        CGPoint::new(0.0, 0.0),
                        CGSize::new(BAR_WIDTH, tick.bar_heights[i]),
                    ));
                }
            }
            CATransaction::commit();
        }
        self.sync_anim_timer(tick.timer_mode, ctx_ptr);
    }

    fn on_clock_tick(&mut self) {
        if self.shutting_down.get() {
            self.stop_clock_timer();
            return;
        }
        if let Some(clock_text) = self.model.recording_clock_text(Instant::now()) {
            self.meta_label
                .setStringValue(&NSString::from_str(&clock_text));
        } else {
            self.stop_clock_timer();
        }
    }

    fn sync_anim_timer(&mut self, mode: OverlayTimerMode, ctx_ptr: *mut Rc<RefCell<OverlayInner>>) {
        if self.anim_timer_mode == mode {
            return;
        }
        self.stop_anim_timer();
        let Some(interval) = mode.interval() else {
            return;
        };
        let mut timer_ctx = CFRunLoopTimerContext {
            version: 0,
            info: ctx_ptr.cast::<c_void>(),
            retain: None,
            release: None,
            copyDescription: None,
        };
        let fire_date = CFAbsoluteTimeGetCurrent() + interval.as_secs_f64();
        let timer = unsafe {
            CFRunLoopTimer::new(
                None,
                fire_date,
                interval.as_secs_f64(),
                0,
                0,
                Some(anim_timer_callback),
                &mut timer_ctx,
            )
        };
        if let Some(timer) = timer {
            self.run_loop
                .add_timer(Some(&timer), unsafe { kCFRunLoopCommonModes });
            self.anim_timer = Some(timer);
            self.anim_timer_mode = mode;
        }
    }

    fn stop_anim_timer(&mut self) {
        if let Some(timer) = self.anim_timer.take() {
            timer.invalidate();
        }
        self.anim_timer_mode = OverlayTimerMode::Stopped;
    }

    fn sync_clock_timer(&mut self, active: bool, ctx_ptr: *mut Rc<RefCell<OverlayInner>>) {
        if active == self.clock_timer.is_some() {
            return;
        }
        if !active {
            self.stop_clock_timer();
            return;
        }
        let mut timer_ctx = CFRunLoopTimerContext {
            version: 0,
            info: ctx_ptr.cast::<c_void>(),
            retain: None,
            release: None,
            copyDescription: None,
        };
        let fire_date = CFAbsoluteTimeGetCurrent() + CLOCK_INTERVAL_SECS;
        let timer = unsafe {
            CFRunLoopTimer::new(
                None,
                fire_date,
                CLOCK_INTERVAL_SECS,
                0,
                0,
                Some(clock_timer_callback),
                &mut timer_ctx,
            )
        };
        if let Some(timer) = timer {
            self.run_loop
                .add_timer(Some(&timer), unsafe { kCFRunLoopCommonModes });
            self.clock_timer = Some(timer);
        }
    }

    fn stop_clock_timer(&mut self) {
        if let Some(timer) = self.clock_timer.take() {
            timer.invalidate();
        }
    }
}

unsafe extern "C-unwind" fn runloop_source_perform(info: *mut c_void) {
    if info.is_null() {
        return;
    }
    let ctx_ptr = info.cast::<Rc<RefCell<OverlayInner>>>();
    let rc = unsafe { &*ctx_ptr };
    if let Ok(mut inner) = rc.try_borrow_mut() {
        inner.drain_and_apply(ctx_ptr);
    }
}

unsafe extern "C-unwind" fn anim_timer_callback(_timer: *mut CFRunLoopTimer, info: *mut c_void) {
    if info.is_null() {
        return;
    }
    let ctx_ptr = info.cast::<Rc<RefCell<OverlayInner>>>();
    let rc = unsafe { &*ctx_ptr };
    if let Ok(mut inner) = rc.try_borrow_mut() {
        inner.on_anim_tick(ctx_ptr);
    }
}

unsafe extern "C-unwind" fn clock_timer_callback(_timer: *mut CFRunLoopTimer, info: *mut c_void) {
    if info.is_null() {
        return;
    }
    let ctx_ptr = info.cast::<Rc<RefCell<OverlayInner>>>();
    let rc = unsafe { &*ctx_ptr };
    if let Ok(mut inner) = rc.try_borrow_mut() {
        inner.on_clock_tick();
    }
}

fn make_label(mtm: MainThreadMarker, size: f64, weight: CGFloat) -> Retained<NSTextField> {
    let label = NSTextField::labelWithString(&NSString::from_str(""), mtm);
    label.setFont(Some(&NSFont::systemFontOfSize_weight(size, weight)));
    label.setDrawsBackground(false);
    label
}

fn active_screen_frame(mtm: MainThreadMarker) -> CGRect {
    if let Some(screen) = NSScreen::mainScreen(mtm) {
        return screen.frame();
    }
    let screens = NSScreen::screens(mtm);
    if !screens.is_empty() {
        return screens.objectAtIndex(0).frame();
    }
    CGRect::new(CGPoint::new(0.0, 0.0), CGSize::new(1440.0, 900.0))
}

fn reduce_motion_enabled() -> bool {
    NSWorkspace::sharedWorkspace().accessibilityDisplayShouldReduceMotion()
}

fn ns_color_from_hex(hex: &str, alpha: f64) -> Retained<NSColor> {
    let rgb = hex_to_rgb(hex).unwrap_or(pulsescribe_core::overlay_style::Rgb {
        r: 255,
        g: 255,
        b: 255,
    });
    NSColor::colorWithSRGBRed_green_blue_alpha(
        f64::from(rgb.r) / 255.0,
        f64::from(rgb.g) / 255.0,
        f64::from(rgb.b) / 255.0,
        alpha,
    )
}

fn cg_color_from_hex(hex: &str, alpha: f64) -> CFRetained<CGColor> {
    ns_color_from_hex(hex, alpha).CGColor().into()
}

fn build_cgcolor_array(colors: &[&CGColor]) -> Retained<NSArray> {
    if colors.is_empty() {
        return NSArray::array();
    }
    let mut ptrs: Vec<NonNull<AnyObject>> = Vec::with_capacity(colors.len());
    for &c in colors {
        let raw: *const CGColor = ptr::from_ref(c);
        if let Some(nn) = NonNull::new(raw as *mut AnyObject) {
            ptrs.push(nn);
        }
    }
    if ptrs.is_empty() {
        return NSArray::array();
    }
    match NonNull::new(ptrs.as_mut_ptr()) {
        Some(ptr) => unsafe { NSArray::arrayWithObjects_count(ptr, ptrs.len()) },
        None => NSArray::array(),
    }
}
