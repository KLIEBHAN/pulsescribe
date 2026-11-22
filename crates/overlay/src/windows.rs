//! Native Windows 60 FPS non-activating floating overlay (Win32 layered window + Direct2D/DirectWrite).
//!
//! Every `unsafe` Win32/COM call in `crates/overlay` on Windows is isolated to this module.
//! - Uses `WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT`
//!   with `ShowWindow(SW_SHOWNOACTIVATE)` and `WM_MOUSEACTIVATE -> MA_NOACTIVATE` for zero focus steal.
//! - Click-through by default; clears `WS_EX_TRANSPARENT` only on interactive error/settings pills.
//! - Per-pixel alpha rounded corners via Direct2D `ID2D1DCRenderTarget` + DirectWrite +
//!   `UpdateLayeredWindow` with reusable DIB surface, solid/gradient brushes, and UTF-16 scratch buffer.
//! - Automatic `D2DERR_RECREATE_TARGET` recovery, destination-monitor DPI scaling (`GetDpiForMonitor`
//!   + `WM_DPICHANGED`), DirectWrite ellipsis trimming, and 0 FPS / 0 wakeups when idle or settled.

#![allow(unsafe_code)]

use std::cell::RefCell;
use std::collections::VecDeque;
use std::ffi::c_void;
use std::mem::size_of;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, AtomicIsize, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Instant;

use pulsescribe_core::animation::{BAR_GAP, BAR_WIDTH};
use pulsescribe_core::overlay_style::{
    compute_overlay_layout, hex_to_rgb, OverlayBarMode, OverlayLayoutParams, OverlayTone,
    BACKGROUND_COLOR, BADGE_CORNER_RADIUS, BADGE_FONT_SIZE, BADGE_PADDING_H, BADGE_PADDING_V,
    BAR_AREA_WIDTH, BORDER_COLOR, DISMISS_FONT_SIZE, DISMISS_GLYPH, LINE_HEIGHT, LINK_HIT_SLOP,
    LINK_TEXT_COLOR, META_FONT_SIZE, META_TEXT_COLOR, MUTED_TEXT_COLOR, OVERLAY_CORNER_RADIUS,
    OVERLAY_MAX_WIDTH_RATIO, PHASE_STEP_GAP, PHASE_STEP_WIDTH, SHIMMER_ACTIVE_STEP_COLOR,
    SHIMMER_BAND_WIDTH, SHIMMER_HIGHLIGHT_COLOR, SHIMMER_MAX_FPS, SHIMMER_PERIOD_SECONDS,
    TEXT_COLOR, TEXT_FONT_SIZE,
};
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{
    CloseHandle, COLORREF, D2DERR_RECREATE_TARGET, HANDLE, HINSTANCE, HWND, LPARAM, LRESULT, POINT,
    RECT, SIZE, WAIT_FAILED, WAIT_OBJECT_0, WPARAM,
};
use windows::Win32::Graphics::Direct2D::Common::{
    D2D1_ALPHA_MODE_PREMULTIPLIED, D2D1_COLOR_F, D2D1_GRADIENT_STOP, D2D1_PIXEL_FORMAT, D2D_RECT_F,
};
use windows::Win32::Graphics::Direct2D::{
    D2D1CreateFactory, ID2D1DCRenderTarget, ID2D1Factory, ID2D1LinearGradientBrush,
    ID2D1SolidColorBrush, D2D1_ANTIALIAS_MODE_PER_PRIMITIVE, D2D1_DRAW_TEXT_OPTIONS_CLIP,
    D2D1_EXTEND_MODE_CLAMP, D2D1_FACTORY_TYPE_SINGLE_THREADED, D2D1_FEATURE_LEVEL_DEFAULT,
    D2D1_GAMMA_2_2, D2D1_LINEAR_GRADIENT_BRUSH_PROPERTIES, D2D1_RENDER_TARGET_PROPERTIES,
    D2D1_RENDER_TARGET_TYPE_DEFAULT, D2D1_RENDER_TARGET_USAGE_NONE, D2D1_ROUNDED_RECT,
};
use windows::Win32::Graphics::DirectWrite::{
    DWriteCreateFactory, IDWriteFactory, IDWriteTextFormat, IDWriteTextLayout,
    DWRITE_FACTORY_TYPE_SHARED, DWRITE_FONT_STRETCH_NORMAL, DWRITE_FONT_STYLE_NORMAL,
    DWRITE_FONT_WEIGHT_MEDIUM, DWRITE_FONT_WEIGHT_REGULAR, DWRITE_FONT_WEIGHT_SEMI_BOLD,
    DWRITE_PARAGRAPH_ALIGNMENT_CENTER, DWRITE_TEXT_ALIGNMENT_CENTER, DWRITE_TEXT_ALIGNMENT_LEADING,
    DWRITE_TEXT_ALIGNMENT_TRAILING, DWRITE_TEXT_METRICS, DWRITE_TEXT_RANGE, DWRITE_TRIMMING,
    DWRITE_TRIMMING_GRANULARITY_CHARACTER, DWRITE_WORD_WRAPPING_NO_WRAP, DWRITE_WORD_WRAPPING_WRAP,
};
use windows::Win32::Graphics::Dxgi::Common::DXGI_FORMAT_B8G8R8A8_UNORM;
use windows::Win32::Graphics::Gdi::{
    CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GetMonitorInfoW,
    MonitorFromPoint, MonitorFromWindow, SelectObject, AC_SRC_ALPHA, AC_SRC_OVER, BITMAPINFO,
    BITMAPINFOHEADER, BI_RGB, BLENDFUNCTION, DIB_RGB_COLORS, HBITMAP, HDC, HGDIOBJ, HMONITOR,
    MONITORINFO, MONITOR_DEFAULTTONEAREST,
};
use windows::Win32::System::Com::{CoInitializeEx, CoUninitialize, COINIT_APARTMENTTHREADED};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::{CreateEventW, SetEvent, INFINITE};
use windows::Win32::UI::HiDpi::{GetDpiForMonitor, GetDpiForWindow, MDT_EFFECTIVE_DPI};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GetCursorPos,
    GetWindowLongPtrW, GetWindowRect, IsWindow, KillTimer, LoadCursorW,
    MsgWaitForMultipleObjectsEx, PeekMessageW, PostQuitMessage, RegisterClassExW, SetCursor,
    SetTimer, SetWindowLongPtrW, SetWindowPos, ShowWindow, SystemParametersInfoW, TranslateMessage,
    UnregisterClassW, UpdateLayeredWindow, GWLP_USERDATA, GWL_EXSTYLE, HWND_TOPMOST, IDC_ARROW,
    IDC_HAND, MA_NOACTIVATE, MSG, MWMO_INPUTAVAILABLE, PM_REMOVE, QS_ALLINPUT,
    SPI_GETCLIENTAREAANIMATION, SWP_FRAMECHANGED, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SW_HIDE,
    SW_SHOWNOACTIVATE, SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS, ULW_ALPHA, WM_DESTROY, WM_DPICHANGED,
    WM_LBUTTONUP, WM_MOUSEACTIVATE, WM_NCDESTROY, WM_QUIT, WM_SETCURSOR, WM_SETTINGCHANGE,
    WM_TIMER, WNDCLASSEXW, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
    WS_EX_TRANSPARENT, WS_POPUP,
};

use crate::model::{
    OverlayActionEvent, OverlayCommand, OverlayModel, OverlaySnapshot, OverlayTimerMode,
};
use crate::{OverlayHandle, OverlaySink};

/// Distance above the Windows taskbar work-area edge (in DIPs), matching `ui/overlay_windows.py`.
const WINDOWS_MARGIN_BOTTOM_DIPS: f64 = 60.0;

const TIMER_ANIM_ID: usize = 1;
const TIMER_SHIMMER_ID: usize = 2;
const TIMER_CLOCK_ID: usize = 3;
const TIMER_RETRY_PAINT_ID: usize = 4;
const CLOCK_INTERVAL_MS: u32 = 1000;
const SHIMMER_INTERVAL_MS: u32 = 1000 / SHIMMER_MAX_FPS;
const RETRY_PAINT_INTERVAL_MS: u32 = 100;
const MAX_PAINT_RETRIES: u8 = 20;

static CLASS_SEQ: AtomicIsize = AtomicIsize::new(1);

type ActionCallback = Arc<dyn Fn(OverlayActionEvent) + Send + Sync>;

#[derive(Debug, Clone, Copy)]
struct DipRect {
    left: f32,
    top: f32,
    right: f32,
    bottom: f32,
}

impl DipRect {
    fn contains(self, x: f32, y: f32) -> bool {
        x >= self.left && x <= self.right && y >= self.top && y <= self.bottom
    }

    fn inflated(self, pad: f32) -> Self {
        Self {
            left: self.left - pad,
            top: self.top - pad,
            right: self.right + pad,
            bottom: self.bottom + pad,
        }
    }
}

/// Kernel auto-reset event handle used to wake the Win32 UI loop without relying on
/// `PostMessageW`'s per-thread message queue quota.
struct WakeEvent(HANDLE);

// SAFETY: A Win32 event `HANDLE` created via `CreateEventW` is kernel-synchronized and safe to
// signal (`SetEvent`) and wait on (`MsgWaitForMultipleObjectsEx`) across threads until closed.
unsafe impl Send for WakeEvent {}
unsafe impl Sync for WakeEvent {}

impl WakeEvent {
    fn new() -> Result<Self, String> {
        let handle = unsafe { CreateEventW(None, false, false, None) }
            .map_err(|err| format!("CreateEventW failed: {err}"))?;
        Ok(Self(handle))
    }

    fn raw(&self) -> HANDLE {
        self.0
    }

    fn signal(&self) -> bool {
        unsafe { SetEvent(self.0) }.is_ok()
    }
}

impl Drop for WakeEvent {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

struct QueueInner {
    commands: VecDeque<OverlayCommand>,
    closed: bool,
    signaled: bool,
}

struct SharedQueue {
    inner: Mutex<QueueInner>,
    has_pending: AtomicBool,
    wake: WakeEvent,
}

impl SharedQueue {
    fn new() -> Result<Self, String> {
        Ok(Self {
            inner: Mutex::new(QueueInner {
                commands: VecDeque::new(),
                closed: false,
                signaled: false,
            }),
            has_pending: AtomicBool::new(false),
            wake: WakeEvent::new()?,
        })
    }

    fn wake_handle(&self) -> HANDLE {
        self.wake.raw()
    }

    fn has_pending(&self) -> bool {
        self.has_pending.load(Ordering::Acquire)
    }

    fn push(&self, cmd: OverlayCommand) {
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        if inner.closed {
            return;
        }
        if matches!(cmd, OverlayCommand::AudioLevel(_))
            && matches!(inner.commands.back(), Some(OverlayCommand::AudioLevel(_)))
        {
            let _ = inner.commands.pop_back();
        }
        inner.commands.push_back(cmd);
        self.has_pending.store(true, Ordering::Release);
        // Coalesce kernel wakeups while signaled; retry automatically if a prior signal failed.
        if !inner.signaled && self.wake.signal() {
            inner.signaled = true;
        }
    }

    fn drain(&self) -> Vec<OverlayCommand> {
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        inner.signaled = false;
        self.has_pending.store(false, Ordering::Release);
        inner.commands.drain(..).collect()
    }

    fn close(&self) {
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        inner.closed = true;
        inner.signaled = false;
        inner.commands.clear();
        self.has_pending.store(false, Ordering::Release);
    }
}

struct WindowsOverlaySink {
    queue: Arc<SharedQueue>,
}

impl OverlaySink for WindowsOverlaySink {
    fn send(&self, cmd: OverlayCommand) {
        self.queue.push(cmd);
    }
}

/// Native Windows Direct2D/DirectWrite overlay running on a dedicated Win32 message-loop thread.
///
/// Dropping `WindowsOverlay` posts [`OverlayCommand::Shutdown`] and joins the UI thread cleanly
/// (skipping `join` if dropped synchronously from inside an overlay action callback).
pub struct WindowsOverlay {
    handle: OverlayHandle,
    thread: Option<JoinHandle<()>>,
}

impl WindowsOverlay {
    /// Spawn the Windows overlay UI thread and return `(WindowsOverlay, OverlayHandle)`.
    pub fn spawn(
        on_action: impl Fn(OverlayActionEvent) + Send + Sync + 'static,
    ) -> Result<(Self, OverlayHandle), String> {
        let queue = Arc::new(SharedQueue::new()?);
        let handle = OverlayHandle::from_sink(Arc::new(WindowsOverlaySink {
            queue: Arc::clone(&queue),
        }));
        let action_cb: ActionCallback = Arc::new(on_action);
        let (ready_tx, ready_rx) = mpsc::sync_channel::<Result<(), String>>(1);

        let thread = thread::Builder::new()
            .name("pulsescribe-overlay-win".to_owned())
            .spawn(move || {
                run_win32_overlay_thread(queue, action_cb, ready_tx);
            })
            .map_err(|err| format!("failed to spawn Windows overlay thread: {err}"))?;

        match ready_rx.recv() {
            Ok(Ok(())) => Ok((
                Self {
                    handle: handle.clone(),
                    thread: Some(thread),
                },
                handle,
            )),
            Ok(Err(err)) => {
                let _ = thread.join();
                Err(err)
            }
            Err(_) => {
                let _ = thread.join();
                Err("Windows overlay thread exited before completing initialization".to_owned())
            }
        }
    }

    /// Return a cloneable handle for pushing daemon events to this overlay.
    pub fn handle(&self) -> OverlayHandle {
        self.handle.clone()
    }
}

impl Drop for WindowsOverlay {
    fn drop(&mut self) {
        self.handle.shutdown();
        if let Some(thread) = self.thread.take() {
            if thread.thread().id() != thread::current().id() {
                let _ = thread.join();
            }
        }
    }
}

struct ComGuard;

impl ComGuard {
    fn init() -> Result<Self, String> {
        let hr = unsafe { CoInitializeEx(None, COINIT_APARTMENTTHREADED) };
        if hr.is_err() {
            return Err(format!("CoInitializeEx failed: {hr:?}"));
        }
        Ok(Self)
    }
}

impl Drop for ComGuard {
    fn drop(&mut self) {
        unsafe { CoUninitialize() };
    }
}

struct LayeredSurface {
    hdc: HDC,
    bitmap: HBITMAP,
    prev_obj: HGDIOBJ,
    width_px: i32,
    height_px: i32,
}

impl LayeredSurface {
    fn ensure(slot: &mut Option<Self>, width_px: i32, height_px: i32) -> Result<&Self, String> {
        let width_px = width_px.max(1);
        let height_px = height_px.max(1);
        let needs_recreate = match slot.as_ref() {
            Some(existing) => existing.width_px != width_px || existing.height_px != height_px,
            None => true,
        };
        if needs_recreate {
            *slot = None;
            let hdc = unsafe { CreateCompatibleDC(None) };
            if hdc.is_invalid() {
                return Err("CreateCompatibleDC failed".to_owned());
            }
            let bmi = BITMAPINFO {
                bmiHeader: BITMAPINFOHEADER {
                    biSize: size_of::<BITMAPINFOHEADER>() as u32,
                    biWidth: width_px,
                    // Negative height selects top-down DIB matching Direct2D's top-left origin.
                    biHeight: -height_px,
                    biPlanes: 1,
                    biBitCount: 32,
                    biCompression: BI_RGB.0,
                    ..Default::default()
                },
                ..Default::default()
            };
            let mut bits: *mut c_void = std::ptr::null_mut();
            let bitmap = match unsafe {
                CreateDIBSection(Some(hdc), &bmi, DIB_RGB_COLORS, &mut bits, None, 0)
            } {
                Ok(bmp) => bmp,
                Err(err) => {
                    unsafe {
                        let _ = DeleteDC(hdc);
                    }
                    return Err(format!("CreateDIBSection failed: {err}"));
                }
            };
            let prev_obj = unsafe { SelectObject(hdc, bitmap.into()) };
            if prev_obj.is_invalid() {
                unsafe {
                    let _ = DeleteObject(bitmap.into());
                    let _ = DeleteDC(hdc);
                }
                return Err("SelectObject failed for layered DIB surface".to_owned());
            }
            *slot = Some(Self {
                hdc,
                bitmap,
                prev_obj,
                width_px,
                height_px,
            });
        }
        Ok(slot.as_ref().expect("surface initialized above"))
    }
}

impl Drop for LayeredSurface {
    fn drop(&mut self) {
        unsafe {
            let _ = SelectObject(self.hdc, self.prev_obj);
            let _ = DeleteObject(self.bitmap.into());
            let _ = DeleteDC(self.hdc);
        }
    }
}

struct TargetResources {
    target: ID2D1DCRenderTarget,
    brush: ID2D1SolidColorBrush,
    shimmer_brush: ID2D1LinearGradientBrush,
}

impl TargetResources {
    fn create(d2d_factory: &ID2D1Factory) -> Result<Self, String> {
        let rt_props = D2D1_RENDER_TARGET_PROPERTIES {
            r#type: D2D1_RENDER_TARGET_TYPE_DEFAULT,
            pixelFormat: D2D1_PIXEL_FORMAT {
                format: DXGI_FORMAT_B8G8R8A8_UNORM,
                alphaMode: D2D1_ALPHA_MODE_PREMULTIPLIED,
            },
            dpiX: 96.0,
            dpiY: 96.0,
            usage: D2D1_RENDER_TARGET_USAGE_NONE,
            minLevel: D2D1_FEATURE_LEVEL_DEFAULT,
        };
        let target = unsafe { d2d_factory.CreateDCRenderTarget(&rt_props) }
            .map_err(|err| format!("CreateDCRenderTarget failed: {err}"))?;

        let brush = unsafe { target.CreateSolidColorBrush(&color_from_hex(TEXT_COLOR, 1.0), None) }
            .map_err(|err| format!("CreateSolidColorBrush failed: {err}"))?;

        let stops = [
            D2D1_GRADIENT_STOP {
                position: 0.0,
                color: color_from_hex(SHIMMER_HIGHLIGHT_COLOR, 0.0),
            },
            D2D1_GRADIENT_STOP {
                position: 0.5,
                color: color_from_hex(SHIMMER_HIGHLIGHT_COLOR, 0.92),
            },
            D2D1_GRADIENT_STOP {
                position: 1.0,
                color: color_from_hex(SHIMMER_HIGHLIGHT_COLOR, 0.0),
            },
        ];
        let shimmer_stops = unsafe {
            target.CreateGradientStopCollection(&stops, D2D1_GAMMA_2_2, D2D1_EXTEND_MODE_CLAMP)
        }
        .map_err(|err| format!("CreateGradientStopCollection failed: {err}"))?;

        let grad_props = D2D1_LINEAR_GRADIENT_BRUSH_PROPERTIES::default();
        let shimmer_brush =
            unsafe { target.CreateLinearGradientBrush(&grad_props, None, &shimmer_stops) }
                .map_err(|err| format!("CreateLinearGradientBrush failed: {err}"))?;

        Ok(Self {
            target,
            brush,
            shimmer_brush,
        })
    }
}

struct D2dResources {
    d2d_factory: ID2D1Factory,
    rt: TargetResources,
    dwrite_factory: IDWriteFactory,
    text_format: IDWriteTextFormat,
    live_text_format: IDWriteTextFormat,
    badge_format: IDWriteTextFormat,
    meta_format: IDWriteTextFormat,
    dismiss_format: IDWriteTextFormat,
}

impl D2dResources {
    fn new() -> Result<Self, String> {
        let d2d_factory: ID2D1Factory =
            unsafe { D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, None) }
                .map_err(|err| format!("D2D1CreateFactory failed: {err}"))?;

        let rt = TargetResources::create(&d2d_factory)?;

        let dwrite_factory: IDWriteFactory =
            unsafe { DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED) }
                .map_err(|err| format!("DWriteCreateFactory failed: {err}"))?;

        let text_format = create_text_format(
            &dwrite_factory,
            TEXT_FONT_SIZE as f32,
            DWRITE_FONT_WEIGHT_REGULAR,
            DWRITE_TEXT_ALIGNMENT_LEADING,
            DWRITE_WORD_WRAPPING_NO_WRAP,
            true,
        )?;
        let live_text_format = create_text_format(
            &dwrite_factory,
            TEXT_FONT_SIZE as f32,
            DWRITE_FONT_WEIGHT_REGULAR,
            DWRITE_TEXT_ALIGNMENT_LEADING,
            DWRITE_WORD_WRAPPING_WRAP,
            false,
        )?;
        let badge_format = create_text_format(
            &dwrite_factory,
            BADGE_FONT_SIZE as f32,
            DWRITE_FONT_WEIGHT_SEMI_BOLD,
            DWRITE_TEXT_ALIGNMENT_CENTER,
            DWRITE_WORD_WRAPPING_NO_WRAP,
            true,
        )?;
        let meta_format = create_text_format(
            &dwrite_factory,
            META_FONT_SIZE as f32,
            DWRITE_FONT_WEIGHT_MEDIUM,
            DWRITE_TEXT_ALIGNMENT_TRAILING,
            DWRITE_WORD_WRAPPING_NO_WRAP,
            true,
        )?;
        let dismiss_format = create_text_format(
            &dwrite_factory,
            DISMISS_FONT_SIZE as f32,
            DWRITE_FONT_WEIGHT_REGULAR,
            DWRITE_TEXT_ALIGNMENT_CENTER,
            DWRITE_WORD_WRAPPING_NO_WRAP,
            false,
        )?;

        Ok(Self {
            d2d_factory,
            rt,
            dwrite_factory,
            text_format,
            live_text_format,
            badge_format,
            meta_format,
            dismiss_format,
        })
    }

    fn recreate_target(&mut self) -> bool {
        match TargetResources::create(&self.d2d_factory) {
            Ok(rt) => {
                self.rt = rt;
                true
            }
            Err(_) => false,
        }
    }
}

enum DrainOutcome {
    Continue,
    DestroyWindow,
}

struct PresentedFrameMeta {
    dpi: u32,
    settings_hit_rect: Option<DipRect>,
    dismiss_hit_rect: Option<DipRect>,
}

struct WindowState {
    hwnd: HWND,
    queue: Arc<SharedQueue>,
    on_action: ActionCallback,
    model: OverlayModel,
    d2d: D2dResources,
    surface: Option<LayeredSurface>,
    utf16_scratch: Vec<u16>,
    last_snapshot: Option<OverlaySnapshot>,
    last_dpi: u32,
    animations_enabled: bool,
    anim_timer_mode: OverlayTimerMode,
    shimmer_timer_active: bool,
    shimmer_origin: Instant,
    clock_timer_active: bool,
    retry_timer_active: bool,
    paint_retry_attempts: u8,
    pending_retry_dpi: Option<u32>,
    target_needs_recreate: bool,
    is_visible: bool,
    is_interactive: bool,
    settings_hit_rect: Option<DipRect>,
    dismiss_hit_rect: Option<DipRect>,
    shutting_down: bool,
}

impl WindowState {
    /// Drain queued commands and return whether the caller must invoke `DestroyWindow` AFTER
    /// releasing its `RefMut<WindowState>` borrow.
    fn drain_and_apply(&mut self) -> DrainOutcome {
        if self.shutting_down {
            return DrainOutcome::Continue;
        }
        let commands = self.queue.drain();
        if commands.is_empty() {
            return DrainOutcome::Continue;
        }
        let now = Instant::now();
        let mut any_visual_change = false;
        for cmd in commands {
            any_visual_change |= self.model.apply_command_mut(cmd, now);
        }

        if self.model.is_shutdown() {
            self.prepare_shutdown();
            return DrainOutcome::DestroyWindow;
        }

        if any_visual_change {
            let snap = self.model.snapshot(now);
            self.apply_snapshot(snap, now);
        } else {
            // AudioLevel-only batch: wake or pace wave timer without redrawing D2D prematurely.
            self.sync_anim_timer(self.model.timer_mode());
        }
        DrainOutcome::Continue
    }

    /// Stop timers, close and drain the queue, and hide the window; `DestroyWindow` is called by
    /// the caller only after the `RefMut<WindowState>` borrow is dropped so `WM_NCDESTROY` never
    /// aliases.
    fn prepare_shutdown(&mut self) {
        if self.shutting_down {
            return;
        }
        self.shutting_down = true;
        self.queue.close();
        self.stop_all_timers();
        unsafe {
            let _ = ShowWindow(self.hwnd, SW_HIDE);
        }
    }

    fn apply_snapshot(&mut self, snap: OverlaySnapshot, now: Instant) {
        if !snap.visible {
            self.stop_all_timers();
            self.settings_hit_rect = None;
            self.dismiss_hit_rect = None;
            self.set_interactive(false);
            if self.is_visible {
                unsafe {
                    let _ = ShowWindow(self.hwnd, SW_HIDE);
                }
                self.is_visible = false;
            }
            self.last_snapshot = Some(snap);
            return;
        }

        self.animations_enabled = windows_animations_enabled();
        let prev_shimmer = self.last_snapshot.as_ref().and_then(|s| s.shimmer);
        if snap.shimmer.is_some() && snap.shimmer != prev_shimmer {
            self.shimmer_origin = now;
        }

        self.sync_anim_timer(snap.timer_mode);
        self.sync_shimmer_timer(snap.shimmer.is_some() && self.animations_enabled);
        self.sync_clock_timer(snap.clock_active);

        self.paint_and_present(&snap, now, None, true);
        self.last_snapshot = Some(snap);
    }

    fn on_timer(&mut self, timer_id: usize) {
        if self.shutting_down {
            self.stop_all_timers();
            return;
        }
        let now = Instant::now();
        match timer_id {
            TIMER_ANIM_ID => {
                if !self.animations_enabled {
                    self.sync_anim_timer(OverlayTimerMode::Stopped);
                    return;
                }
                let tick = self.model.tick_animation(now);
                self.sync_anim_timer(tick.timer_mode);
                if tick.any_bar_changed {
                    let snap = self.model.snapshot(now);
                    self.paint_and_present(&snap, now, None, false);
                    self.last_snapshot = Some(snap);
                }
            }
            TIMER_SHIMMER_ID => {
                if let Some(snap) = self.last_snapshot.clone() {
                    if snap.visible && snap.shimmer.is_some() && self.animations_enabled {
                        self.paint_and_present(&snap, now, None, false);
                    } else {
                        self.sync_shimmer_timer(false);
                    }
                } else {
                    self.sync_shimmer_timer(false);
                }
            }
            TIMER_CLOCK_ID => {
                if self.model.recording_clock_text(now).is_some() {
                    let snap = self.model.snapshot(now);
                    self.paint_and_present(&snap, now, None, false);
                    self.last_snapshot = Some(snap);
                } else {
                    self.sync_clock_timer(false);
                }
            }
            TIMER_RETRY_PAINT_ID => {
                self.paint_retry_attempts = self.paint_retry_attempts.saturating_add(1);
                if self.paint_retry_attempts > MAX_PAINT_RETRIES {
                    self.cancel_paint_retry();
                    return;
                }
                let retry_dpi = self.pending_retry_dpi;
                if let Some(snap) = self.last_snapshot.clone() {
                    if snap.visible {
                        self.paint_and_present(&snap, now, retry_dpi, false);
                    } else {
                        self.cancel_paint_retry();
                    }
                } else {
                    self.cancel_paint_retry();
                }
            }
            _ => {}
        }
    }

    fn on_dpi_changed(&mut self, new_dpi: u32) {
        if self.shutting_down || (!self.is_visible && !self.retry_timer_active) {
            return;
        }
        if let Some(snap) = self.last_snapshot.clone() {
            if snap.visible {
                self.paint_and_present(&snap, Instant::now(), Some(new_dpi.max(96)), true);
            }
        }
    }

    fn on_setting_change(&mut self) {
        if self.shutting_down {
            return;
        }
        let next = windows_animations_enabled();
        if next == self.animations_enabled {
            return;
        }
        self.animations_enabled = next;
        let now = Instant::now();
        if let Some(snap) = self.last_snapshot.clone() {
            if snap.visible {
                if next && snap.shimmer.is_some() {
                    self.shimmer_origin = now;
                }
                self.sync_anim_timer(snap.timer_mode);
                self.sync_shimmer_timer(snap.shimmer.is_some() && self.animations_enabled);
                if self.is_visible || self.retry_timer_active {
                    self.paint_and_present(&snap, now, None, true);
                }
            }
        }
    }

    /// Resolve a left click, synchronously dismiss the currently displayed pill (never racing
    /// with a newer `ApplyState` waiting in the queue), and return `(callback, event)` to invoke
    /// AFTER dropping `RefMut`.
    fn on_left_click(
        &mut self,
        client_x_px: i32,
        client_y_px: i32,
    ) -> Option<(ActionCallback, OverlayActionEvent)> {
        if !self.is_interactive || self.shutting_down {
            return None;
        }
        let scale = self.last_dpi.max(96) as f32 / 96.0;
        let x = client_x_px as f32 / scale;
        let y = client_y_px as f32 / scale;

        let event = if self.settings_hit_rect.is_some_and(|r| r.contains(x, y)) {
            Some(OverlayActionEvent::OpenSettings)
        } else if self.dismiss_hit_rect.is_some_and(|r| r.contains(x, y)) {
            Some(OverlayActionEvent::Dismissed)
        } else {
            None
        }?;

        let now = Instant::now();
        if self.model.apply_command_mut(OverlayCommand::Dismiss, now) {
            let snap = self.model.snapshot(now);
            self.apply_snapshot(snap, now);
        } else {
            self.settings_hit_rect = None;
            self.dismiss_hit_rect = None;
            self.set_interactive(false);
        }
        Some((Arc::clone(&self.on_action), event))
    }

    fn cursor_is_over_interactive(&self) -> bool {
        if !self.is_interactive {
            return false;
        }
        let mut pt = POINT::default();
        if unsafe { GetCursorPos(&mut pt) }.is_err() {
            return false;
        }
        let mut win_rect = RECT::default();
        if unsafe { GetWindowRect(self.hwnd, &mut win_rect) }.is_err() {
            return false;
        }
        let scale = self.last_dpi.max(96) as f32 / 96.0;
        let x = (pt.x - win_rect.left) as f32 / scale;
        let y = (pt.y - win_rect.top) as f32 / scale;
        self.settings_hit_rect.is_some_and(|r| r.contains(x, y))
            || self.dismiss_hit_rect.is_some_and(|r| r.contains(x, y))
    }

    fn set_interactive(&mut self, interactive: bool) {
        if self.is_interactive == interactive {
            return;
        }
        self.is_interactive = interactive;
        unsafe {
            let ex = GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE);
            let next = if interactive {
                ex & !(WS_EX_TRANSPARENT.0 as isize)
            } else {
                ex | (WS_EX_TRANSPARENT.0 as isize)
            };
            SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, next);
            let _ = SetWindowPos(
                self.hwnd,
                Some(HWND_TOPMOST),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            );
        }
    }

    fn sync_anim_timer(&mut self, mode: OverlayTimerMode) {
        let effective = if self.animations_enabled {
            mode
        } else {
            OverlayTimerMode::Stopped
        };
        if self.anim_timer_mode == effective {
            return;
        }
        if self.anim_timer_mode != OverlayTimerMode::Stopped {
            unsafe {
                let _ = KillTimer(Some(self.hwnd), TIMER_ANIM_ID);
            }
            self.anim_timer_mode = OverlayTimerMode::Stopped;
        }
        if let Some(interval) = effective.interval() {
            let ms = (interval.as_millis() as u32).max(1);
            if unsafe { SetTimer(Some(self.hwnd), TIMER_ANIM_ID, ms, None) } != 0 {
                self.anim_timer_mode = effective;
            }
        }
    }

    fn sync_shimmer_timer(&mut self, active: bool) {
        if self.shimmer_timer_active == active {
            return;
        }
        unsafe {
            if active {
                if SetTimer(Some(self.hwnd), TIMER_SHIMMER_ID, SHIMMER_INTERVAL_MS, None) != 0 {
                    self.shimmer_timer_active = true;
                }
            } else {
                let _ = KillTimer(Some(self.hwnd), TIMER_SHIMMER_ID);
                self.shimmer_timer_active = false;
            }
        }
    }

    fn sync_clock_timer(&mut self, active: bool) {
        if self.clock_timer_active == active {
            return;
        }
        unsafe {
            if active {
                if SetTimer(Some(self.hwnd), TIMER_CLOCK_ID, CLOCK_INTERVAL_MS, None) != 0 {
                    self.clock_timer_active = true;
                }
            } else {
                let _ = KillTimer(Some(self.hwnd), TIMER_CLOCK_ID);
                self.clock_timer_active = false;
            }
        }
    }

    fn schedule_paint_retry(&mut self, dpi_override: Option<u32>, reset_budget: bool) {
        if self.shutting_down {
            return;
        }
        if reset_budget {
            self.paint_retry_attempts = 0;
        }
        if self.paint_retry_attempts >= MAX_PAINT_RETRIES {
            self.cancel_paint_retry();
            return;
        }
        self.pending_retry_dpi = dpi_override;
        if !self.retry_timer_active
            && unsafe {
                SetTimer(
                    Some(self.hwnd),
                    TIMER_RETRY_PAINT_ID,
                    RETRY_PAINT_INTERVAL_MS,
                    None,
                )
            } != 0
        {
            self.retry_timer_active = true;
        }
    }

    fn cancel_paint_retry(&mut self) {
        if self.retry_timer_active {
            unsafe {
                let _ = KillTimer(Some(self.hwnd), TIMER_RETRY_PAINT_ID);
            }
            self.retry_timer_active = false;
        }
        self.paint_retry_attempts = 0;
        self.pending_retry_dpi = None;
    }

    fn stop_all_timers(&mut self) {
        self.sync_anim_timer(OverlayTimerMode::Stopped);
        self.sync_shimmer_timer(false);
        self.sync_clock_timer(false);
        self.cancel_paint_retry();
    }

    /// Render and present a snapshot, staging hit-test/DPI/visibility state until
    /// `UpdateLayeredWindow` succeeds. Recovers immediately on `D2DERR_RECREATE_TARGET`, and
    /// schedules a bounded retry timer only if immediate recovery or presentation fails.
    fn paint_and_present(
        &mut self,
        snap: &OverlaySnapshot,
        now: Instant,
        dpi_override: Option<u32>,
        reset_retry_budget: bool,
    ) {
        if self.target_needs_recreate {
            if self.d2d.recreate_target() {
                self.target_needs_recreate = false;
            } else {
                self.schedule_paint_retry(dpi_override, reset_retry_budget);
                return;
            }
        }

        let result = match self.try_paint_snapshot(snap, now, dpi_override) {
            Ok(meta) => Ok(meta),
            Err(true) => {
                if self.d2d.recreate_target() {
                    self.target_needs_recreate = false;
                    self.try_paint_snapshot(snap, now, dpi_override)
                } else {
                    self.target_needs_recreate = true;
                    Err(true)
                }
            }
            Err(false) => Err(false),
        };

        match result {
            Ok(meta) => {
                self.cancel_paint_retry();
                self.last_dpi = meta.dpi;
                self.settings_hit_rect = meta.settings_hit_rect;
                self.dismiss_hit_rect = meta.dismiss_hit_rect;
                self.set_interactive(snap.interactive);
                if !self.is_visible {
                    unsafe {
                        let _ = ShowWindow(self.hwnd, SW_SHOWNOACTIVATE);
                    }
                    self.is_visible = true;
                }
            }
            Err(needs_recreate) => {
                if needs_recreate {
                    self.target_needs_recreate = true;
                }
                self.schedule_paint_retry(dpi_override, reset_retry_budget);
            }
        }
    }

    fn try_paint_snapshot(
        &mut self,
        snap: &OverlaySnapshot,
        now: Instant,
        dpi_override: Option<u32>,
    ) -> Result<PresentedFrameMeta, bool> {
        let (work_rect, dpi) = match dpi_override {
            Some(window_dpi) => (window_monitor_work_rect(self.hwnd), window_dpi.max(96)),
            None => active_monitor_work_rect_and_dpi(self.hwnd),
        };
        let scale = dpi as f32 / 96.0;
        let screen_width_dips =
            ((work_rect.right - work_rect.left).max(400) as f64) / f64::from(scale);

        let panel_h = snap.base_size.1 as f32;
        let center_y = panel_h / 2.0;

        // Measure only dynamic elements in DIPs; skip `display_text` measurement when `!allow_grow`.
        let has_badge = !snap.content.badge.is_empty();
        let badge_width = if has_badge {
            let measured = measure_text_width(
                &self.d2d.dwrite_factory,
                &self.d2d.badge_format,
                &snap.content.badge,
                &mut self.utf16_scratch,
            );
            (f64::from(measured).ceil() + 2.0 * BADGE_PADDING_H).max(44.0)
        } else {
            0.0
        };

        let show_settings = snap.action.is_some();
        let meta_text = if show_settings {
            "Settings"
        } else {
            snap.display_meta.as_str()
        };
        let meta_width = if !meta_text.is_empty() {
            if snap.clock_active {
                // Fixed width accommodates `0:00` through `99:59` with zero DirectWrite measure cost.
                46.0
            } else {
                let measured = measure_text_width(
                    &self.d2d.dwrite_factory,
                    &self.d2d.meta_format,
                    meta_text,
                    &mut self.utf16_scratch,
                );
                let min_w = if show_settings { 54.0 } else { 32.0 };
                f64::from(measured).ceil().max(min_w)
            }
        } else {
            0.0
        };

        let dismiss_width = if snap.content.dismissible { 18.0 } else { 0.0 };
        let measured_text_w = if snap.allow_grow && !snap.display_text.is_empty() {
            f64::from(measure_text_width(
                &self.d2d.dwrite_factory,
                &self.d2d.text_format,
                &snap.display_text,
                &mut self.utf16_scratch,
            ))
            .ceil()
        } else {
            0.0
        };

        let max_width = (screen_width_dips * OVERLAY_MAX_WIDTH_RATIO).max(snap.base_size.0);
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

        let panel_w = layout.width.round() as f32;
        let width_px = (panel_w * scale).round().max(1.0) as i32;
        let height_px = (panel_h * scale).round().max(1.0) as i32;

        let surface =
            LayeredSurface::ensure(&mut self.surface, width_px, height_px).map_err(|_| false)?;
        let sub_rect = RECT {
            left: 0,
            top: 0,
            right: width_px,
            bottom: height_px,
        };

        let target = &self.d2d.rt.target;
        let brush = &self.d2d.rt.brush;
        unsafe {
            if let Err(err) = target.BindDC(surface.hdc, &sub_rect) {
                return Err(err.code() == D2DERR_RECREATE_TARGET);
            }
            target.SetDpi(dpi as f32, dpi as f32);
            target.BeginDraw();
            target.Clear(Some(&D2D1_COLOR_F {
                r: 0.0,
                g: 0.0,
                b: 0.0,
                a: 0.0,
            }));

            // 1. Background pill + 1 px border.
            let bg_rect = D2D1_ROUNDED_RECT {
                rect: D2D_RECT_F {
                    left: 0.5,
                    top: 0.5,
                    right: panel_w - 0.5,
                    bottom: panel_h - 0.5,
                },
                radiusX: OVERLAY_CORNER_RADIUS as f32,
                radiusY: OVERLAY_CORNER_RADIUS as f32,
            };
            brush.SetColor(&color_from_hex(BACKGROUND_COLOR, 1.0));
            target.FillRoundedRectangle(&bg_rect, brush);
            brush.SetColor(&color_from_hex(BORDER_COLOR, 1.0));
            target.DrawRoundedRectangle(&bg_rect, brush, 1.0, None);

            // 2. Status slot (equalizer bars, processing line + shimmer, or outcome badge).
            let status_x = layout.status_x as f32;
            if has_badge {
                let (bg_hex, fg_hex) = snap
                    .content
                    .tone
                    .unwrap_or(OverlayTone::Success)
                    .badge_colors();
                let badge_h = (BADGE_FONT_SIZE + 2.0 * BADGE_PADDING_V + 4.0) as f32;
                let badge_top = (center_y - badge_h / 2.0).round();
                let badge_rect = D2D_RECT_F {
                    left: status_x,
                    top: badge_top,
                    right: status_x + badge_width as f32,
                    bottom: badge_top + badge_h,
                };
                brush.SetColor(&color_from_hex(bg_hex, 1.0));
                target.FillRoundedRectangle(
                    &D2D1_ROUNDED_RECT {
                        rect: badge_rect,
                        radiusX: BADGE_CORNER_RADIUS as f32,
                        radiusY: BADGE_CORNER_RADIUS as f32,
                    },
                    brush,
                );
                draw_text(
                    target,
                    &self.d2d.dwrite_factory,
                    &self.d2d.badge_format,
                    brush,
                    &snap.content.badge,
                    &mut self.utf16_scratch,
                    badge_rect,
                    fg_hex,
                    false,
                );
            } else {
                draw_status_bars(
                    target,
                    brush,
                    &self.d2d.rt.shimmer_brush,
                    snap,
                    status_x,
                    center_y,
                    now.saturating_duration_since(self.shimmer_origin)
                        .as_secs_f64(),
                    self.animations_enabled,
                );
            }

            // 3. Main text (single-line status with ellipsis or wrapped 2-line live transcript).
            if !snap.display_text.is_empty() && layout.text_width > 1.0 {
                let text_rect = D2D_RECT_F {
                    left: layout.text_x as f32,
                    top: 4.0,
                    right: (layout.text_x + layout.text_width) as f32,
                    bottom: panel_h - 4.0,
                };
                let color_hex = if snap.content.emphasized || snap.live_text_active {
                    TEXT_COLOR
                } else {
                    MUTED_TEXT_COLOR
                };
                let fmt = if snap.live_text_active {
                    &self.d2d.live_text_format
                } else {
                    &self.d2d.text_format
                };
                draw_text(
                    target,
                    &self.d2d.dwrite_factory,
                    fmt,
                    brush,
                    &snap.display_text,
                    &mut self.utf16_scratch,
                    text_rect,
                    color_hex,
                    false,
                );
            }

            // 4. Right-aligned meta label or clickable underlined "Settings" link.
            let mut staged_settings_hit_rect = None;
            if let Some(meta_x) = layout.meta_x {
                if !meta_text.is_empty() {
                    let rect = D2D_RECT_F {
                        left: meta_x as f32,
                        top: 4.0,
                        right: (meta_x + meta_width) as f32,
                        bottom: panel_h - 4.0,
                    };
                    let color_hex = if show_settings {
                        LINK_TEXT_COLOR
                    } else {
                        META_TEXT_COLOR
                    };
                    draw_text(
                        target,
                        &self.d2d.dwrite_factory,
                        &self.d2d.meta_format,
                        brush,
                        meta_text,
                        &mut self.utf16_scratch,
                        rect,
                        color_hex,
                        show_settings,
                    );
                    if show_settings {
                        staged_settings_hit_rect = Some(
                            DipRect {
                                left: rect.left,
                                top: rect.top,
                                right: rect.right,
                                bottom: rect.bottom,
                            }
                            .inflated(LINK_HIT_SLOP as f32),
                        );
                    }
                }
            }

            // 5. Optional clickable "×" dismiss button.
            let mut staged_dismiss_hit_rect = None;
            if let Some(dismiss_x) = layout.dismiss_x {
                let rect = D2D_RECT_F {
                    left: dismiss_x as f32,
                    top: 4.0,
                    right: (dismiss_x + dismiss_width) as f32,
                    bottom: panel_h - 4.0,
                };
                draw_text(
                    target,
                    &self.d2d.dwrite_factory,
                    &self.d2d.dismiss_format,
                    brush,
                    DISMISS_GLYPH,
                    &mut self.utf16_scratch,
                    rect,
                    MUTED_TEXT_COLOR,
                    false,
                );
                staged_dismiss_hit_rect = Some(
                    DipRect {
                        left: rect.left,
                        top: rect.top,
                        right: rect.right,
                        bottom: rect.bottom,
                    }
                    .inflated(LINK_HIT_SLOP as f32),
                );
            }

            if let Err(err) = target.EndDraw(None, None) {
                return Err(err.code() == D2DERR_RECREATE_TARGET);
            }

            // 6. Commit per-pixel alpha bitmap positioned above the destination monitor's taskbar.
            let dst_x = work_rect.left + ((work_rect.right - work_rect.left) - width_px) / 2;
            let margin_px = (WINDOWS_MARGIN_BOTTOM_DIPS * f64::from(scale)).round() as i32;
            let dst_y = work_rect.bottom - height_px - margin_px;
            let dst_pt = POINT { x: dst_x, y: dst_y };
            let src_pt = POINT { x: 0, y: 0 };
            let win_size = SIZE {
                cx: width_px,
                cy: height_px,
            };
            let blend = BLENDFUNCTION {
                BlendOp: AC_SRC_OVER as u8,
                BlendFlags: 0,
                SourceConstantAlpha: 255,
                AlphaFormat: AC_SRC_ALPHA as u8,
            };

            UpdateLayeredWindow(
                self.hwnd,
                None,
                Some(&dst_pt),
                Some(&win_size),
                Some(surface.hdc),
                Some(&src_pt),
                COLORREF(0),
                Some(&blend),
                ULW_ALPHA,
            )
            .map_err(|_| false)?;

            Ok(PresentedFrameMeta {
                dpi,
                settings_hit_rect: staged_settings_hit_rect,
                dismiss_hit_rect: staged_dismiss_hit_rect,
            })
        }
    }
}

#[allow(clippy::too_many_arguments)]
unsafe fn draw_status_bars(
    target: &ID2D1DCRenderTarget,
    brush: &ID2D1SolidColorBrush,
    shimmer_brush: &ID2D1LinearGradientBrush,
    snap: &OverlaySnapshot,
    status_x: f32,
    center_y: f32,
    shimmer_elapsed_secs: f64,
    animations_enabled: bool,
) {
    let mode = snap.content.bars;
    if matches!(mode, OverlayBarMode::Level | OverlayBarMode::Low) {
        let hex = mode.bar_color().unwrap_or("#ececec");
        brush.SetColor(&color_from_hex(hex, 1.0));
        let radius = (BAR_WIDTH / 2.0) as f32;
        for (i, &height) in snap.bar_heights.iter().enumerate() {
            let h = height as f32;
            let x = status_x + (i as f32) * ((BAR_WIDTH + BAR_GAP) as f32);
            let top = center_y - h / 2.0;
            target.FillRoundedRectangle(
                &D2D1_ROUNDED_RECT {
                    rect: D2D_RECT_F {
                        left: x,
                        top,
                        right: x + BAR_WIDTH as f32,
                        bottom: top + h,
                    },
                    radiusX: radius,
                    radiusY: radius,
                },
                brush,
            );
        }
        return;
    }

    let line_h = LINE_HEIGHT as f32;
    let line_top = (center_y - line_h / 2.0).round();
    let line_radius = line_h / 2.0;

    match mode {
        OverlayBarMode::Line => {
            brush.SetColor(&color_from_hex(mode.bar_color().unwrap_or("#6f6f6f"), 1.0));
            target.FillRoundedRectangle(
                &D2D1_ROUNDED_RECT {
                    rect: D2D_RECT_F {
                        left: status_x,
                        top: line_top,
                        right: status_x + BAR_AREA_WIDTH as f32,
                        bottom: line_top + line_h,
                    },
                    radiusX: line_radius,
                    radiusY: line_radius,
                },
                brush,
            );
        }
        OverlayBarMode::Phase1 | OverlayBarMode::Phase2 => {
            let (c1, c2) = mode.phase_step_colors().unwrap_or(("#ececec", "#333333"));
            let step1_hex = if animations_enabled && mode == OverlayBarMode::Phase1 {
                SHIMMER_ACTIVE_STEP_COLOR
            } else {
                c1
            };
            let step2_hex = if animations_enabled && mode == OverlayBarMode::Phase2 {
                SHIMMER_ACTIVE_STEP_COLOR
            } else {
                c2
            };
            let step_w = PHASE_STEP_WIDTH as f32;
            let gap = PHASE_STEP_GAP as f32;

            brush.SetColor(&color_from_hex(step1_hex, 1.0));
            target.FillRoundedRectangle(
                &D2D1_ROUNDED_RECT {
                    rect: D2D_RECT_F {
                        left: status_x,
                        top: line_top,
                        right: status_x + step_w,
                        bottom: line_top + line_h,
                    },
                    radiusX: line_radius,
                    radiusY: line_radius,
                },
                brush,
            );
            brush.SetColor(&color_from_hex(step2_hex, 1.0));
            target.FillRoundedRectangle(
                &D2D1_ROUNDED_RECT {
                    rect: D2D_RECT_F {
                        left: status_x + step_w + gap,
                        top: line_top,
                        right: status_x + step_w + gap + step_w,
                        bottom: line_top + line_h,
                    },
                    radiusX: line_radius,
                    radiusY: line_radius,
                },
                brush,
            );
        }
        _ => return,
    }

    if animations_enabled {
        if let Some(track) = snap.shimmer {
            let track_left = status_x + track.x as f32;
            let track_w = track.width as f32;
            let clip_rect = D2D_RECT_F {
                left: track_left,
                top: line_top,
                right: track_left + track_w,
                bottom: line_top + line_h,
            };
            let phase = (shimmer_elapsed_secs % SHIMMER_PERIOD_SECONDS) / SHIMMER_PERIOD_SECONDS;
            let band_w = SHIMMER_BAND_WIDTH as f32;
            let center_x = track_left - band_w / 2.0 + (track_w + band_w) * (phase as f32);

            let mut start_pt = D2D1_LINEAR_GRADIENT_BRUSH_PROPERTIES::default().startPoint;
            start_pt.X = center_x - band_w / 2.0;
            start_pt.Y = center_y;
            let mut end_pt = start_pt;
            end_pt.X = center_x + band_w / 2.0;

            shimmer_brush.SetStartPoint(start_pt);
            shimmer_brush.SetEndPoint(end_pt);
            target.PushAxisAlignedClip(&clip_rect, D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);
            target.FillRoundedRectangle(
                &D2D1_ROUNDED_RECT {
                    rect: clip_rect,
                    radiusX: line_radius,
                    radiusY: line_radius,
                },
                shimmer_brush,
            );
            target.PopAxisAlignedClip();
        }
    }
}

#[allow(clippy::too_many_arguments)]
unsafe fn draw_text(
    target: &ID2D1DCRenderTarget,
    factory: &IDWriteFactory,
    format: &IDWriteTextFormat,
    brush: &ID2D1SolidColorBrush,
    text: &str,
    utf16_scratch: &mut Vec<u16>,
    rect: D2D_RECT_F,
    color_hex: &str,
    underline: bool,
) {
    utf16_scratch.clear();
    utf16_scratch.extend(text.encode_utf16());
    if utf16_scratch.is_empty() {
        return;
    }
    let max_w = (rect.right - rect.left).max(1.0);
    let max_h = (rect.bottom - rect.top).max(1.0);
    let Ok(layout): Result<IDWriteTextLayout, _> =
        factory.CreateTextLayout(utf16_scratch, format, max_w, max_h)
    else {
        return;
    };
    if underline {
        let _ = layout.SetUnderline(
            true,
            DWRITE_TEXT_RANGE {
                startPosition: 0,
                length: utf16_scratch.len() as u32,
            },
        );
    }
    brush.SetColor(&color_from_hex(color_hex, 1.0));
    let mut origin = D2D1_LINEAR_GRADIENT_BRUSH_PROPERTIES::default().startPoint;
    origin.X = rect.left;
    origin.Y = rect.top;
    target.DrawTextLayout(origin, &layout, brush, D2D1_DRAW_TEXT_OPTIONS_CLIP);
}

fn measure_text_width(
    factory: &IDWriteFactory,
    format: &IDWriteTextFormat,
    text: &str,
    utf16_scratch: &mut Vec<u16>,
) -> f32 {
    utf16_scratch.clear();
    utf16_scratch.extend(text.encode_utf16());
    if utf16_scratch.is_empty() {
        return 0.0;
    }
    unsafe {
        let Ok(layout) = factory.CreateTextLayout(utf16_scratch, format, 4096.0, 64.0) else {
            return 0.0;
        };
        let mut metrics = DWRITE_TEXT_METRICS::default();
        if layout.GetMetrics(&mut metrics).is_ok() {
            metrics.widthIncludingTrailingWhitespace.max(metrics.width)
        } else {
            0.0
        }
    }
}

fn create_text_format(
    factory: &IDWriteFactory,
    font_size: f32,
    weight: windows::Win32::Graphics::DirectWrite::DWRITE_FONT_WEIGHT,
    align: windows::Win32::Graphics::DirectWrite::DWRITE_TEXT_ALIGNMENT,
    wrap: windows::Win32::Graphics::DirectWrite::DWRITE_WORD_WRAPPING,
    ellipsis: bool,
) -> Result<IDWriteTextFormat, String> {
    let fmt = unsafe {
        factory.CreateTextFormat(
            w!("Segoe UI"),
            None,
            weight,
            DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL,
            font_size,
            w!("en-US"),
        )
    }
    .map_err(|err| format!("CreateTextFormat failed: {err}"))?;
    unsafe {
        let _ = fmt.SetTextAlignment(align);
        let _ = fmt.SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
        let _ = fmt.SetWordWrapping(wrap);
        if ellipsis {
            if let Ok(sign) = factory.CreateEllipsisTrimmingSign(&fmt) {
                let trimming = DWRITE_TRIMMING {
                    granularity: DWRITE_TRIMMING_GRANULARITY_CHARACTER,
                    delimiter: 0,
                    delimiterCount: 0,
                };
                let _ = fmt.SetTrimming(&trimming, &sign);
            }
        }
    }
    Ok(fmt)
}

fn monitor_work_rect(monitor: HMONITOR) -> RECT {
    let mut info = MONITORINFO {
        cbSize: size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    if unsafe { GetMonitorInfoW(monitor, &mut info) }.as_bool() {
        info.rcWork
    } else {
        RECT {
            left: 0,
            top: 0,
            right: 1920,
            bottom: 1080,
        }
    }
}

fn window_monitor_work_rect(hwnd: HWND) -> RECT {
    let monitor = unsafe { MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) };
    monitor_work_rect(monitor)
}

fn active_monitor_work_rect_and_dpi(fallback_hwnd: HWND) -> (RECT, u32) {
    let mut pt = POINT::default();
    unsafe {
        let _ = GetCursorPos(&mut pt);
        let monitor = MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST);
        let mut dpi_x = 0u32;
        let mut dpi_y = 0u32;
        let dpi = if GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut dpi_x, &mut dpi_y).is_ok()
            && dpi_x >= 96
        {
            dpi_x
        } else {
            let win_dpi = GetDpiForWindow(fallback_hwnd);
            if win_dpi >= 96 {
                win_dpi
            } else {
                96
            }
        };
        (monitor_work_rect(monitor), dpi)
    }
}

fn windows_animations_enabled() -> bool {
    let mut enabled: i32 = 1;
    let ok = unsafe {
        SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION,
            0,
            Some((&mut enabled as *mut i32).cast::<c_void>()),
            SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS(0),
        )
    };
    ok.is_err() || enabled != 0
}

fn color_from_hex(hex: &str, alpha: f32) -> D2D1_COLOR_F {
    let rgb = hex_to_rgb(hex).unwrap_or(pulsescribe_core::overlay_style::Rgb {
        r: 255,
        g: 255,
        b: 255,
    });
    D2D1_COLOR_F {
        r: f32::from(rgb.r) / 255.0,
        g: f32::from(rgb.g) / 255.0,
        b: f32::from(rgb.b) / 255.0,
        a: alpha,
    }
}

fn run_win32_overlay_thread(
    queue: Arc<SharedQueue>,
    on_action: ActionCallback,
    ready_tx: mpsc::SyncSender<Result<(), String>>,
) {
    let _com = match ComGuard::init() {
        Ok(guard) => guard,
        Err(err) => {
            queue.close();
            let _ = ready_tx.send(Err(err));
            return;
        }
    };

    let d2d = match D2dResources::new() {
        Ok(res) => res,
        Err(err) => {
            queue.close();
            let _ = ready_tx.send(Err(err));
            return;
        }
    };

    let hinstance: HINSTANCE = match unsafe { GetModuleHandleW(None) } {
        Ok(h) => h.into(),
        Err(err) => {
            queue.close();
            let _ = ready_tx.send(Err(format!("GetModuleHandleW failed: {err}")));
            return;
        }
    };

    let seq = CLASS_SEQ.fetch_add(1, Ordering::Relaxed);
    let class_name_str = format!("PulseScribeOverlayWin_{seq}\0");
    let class_name: Vec<u16> = class_name_str.encode_utf16().collect();

    let wc = WNDCLASSEXW {
        cbSize: size_of::<WNDCLASSEXW>() as u32,
        lpfnWndProc: Some(overlay_wnd_proc),
        hInstance: hinstance,
        lpszClassName: PCWSTR(class_name.as_ptr()),
        ..Default::default()
    };
    if unsafe { RegisterClassExW(&wc) } == 0 {
        queue.close();
        let _ = ready_tx.send(Err("RegisterClassExW failed".to_owned()));
        return;
    }

    let ex_style =
        WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT;
    let hwnd = match unsafe {
        CreateWindowExW(
            ex_style,
            PCWSTR(class_name.as_ptr()),
            w!("PulseScribe Overlay"),
            WS_POPUP,
            0,
            0,
            1,
            1,
            None,
            None,
            Some(hinstance),
            None,
        )
    } {
        Ok(h) => h,
        Err(err) => {
            unsafe {
                let _ = UnregisterClassW(PCWSTR(class_name.as_ptr()), Some(hinstance));
            }
            queue.close();
            let _ = ready_tx.send(Err(format!("CreateWindowExW failed: {err}")));
            return;
        }
    };

    let now = Instant::now();
    let initial_dpi = {
        let raw = unsafe { GetDpiForWindow(hwnd) };
        if raw >= 96 {
            raw
        } else {
            96
        }
    };
    let state = Rc::new(RefCell::new(WindowState {
        hwnd,
        queue: Arc::clone(&queue),
        on_action,
        model: OverlayModel::new(now),
        d2d,
        surface: None,
        utf16_scratch: Vec::with_capacity(128),
        last_snapshot: None,
        last_dpi: initial_dpi,
        animations_enabled: windows_animations_enabled(),
        anim_timer_mode: OverlayTimerMode::Stopped,
        shimmer_timer_active: false,
        shimmer_origin: now,
        clock_timer_active: false,
        retry_timer_active: false,
        paint_retry_attempts: 0,
        pending_retry_dpi: None,
        target_needs_recreate: false,
        is_visible: false,
        is_interactive: false,
        settings_hit_rect: None,
        dismiss_hit_rect: None,
        shutting_down: false,
    }));
    let state_ptr = Box::into_raw(Box::new(Rc::clone(&state)));
    unsafe {
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, state_ptr as isize);
    }

    let _ = ready_tx.send(Ok(()));

    let drain_now = || -> bool {
        let outcome = state
            .try_borrow_mut()
            .map(|mut st| st.drain_and_apply())
            .unwrap_or(DrainOutcome::Continue);
        // `RefMut<WindowState>` is strictly dropped BEFORE `DestroyWindow` triggers `WM_NCDESTROY`.
        if matches!(outcome, DrainOutcome::DestroyWindow) {
            unsafe {
                let _ = DestroyWindow(hwnd);
            }
            return true;
        }
        false
    };

    let handles = [queue.wake_handle()];
    let mut msg = MSG::default();
    let mut should_exit = drain_now();

    while !should_exit {
        // 1. Dispatch all pending Win32 messages before sleeping.
        while unsafe { PeekMessageW(&mut msg, None, 0, 0, PM_REMOVE) }.as_bool() {
            if msg.message == WM_QUIT {
                should_exit = true;
                break;
            }
            unsafe {
                let _ = TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
            if queue.has_pending() && drain_now() {
                should_exit = true;
                break;
            }
        }
        if should_exit {
            break;
        }

        // 2. Drain any overlay commands queued while messages were processed.
        if drain_now() {
            break;
        }

        // 3. Block at 0% CPU until either the queue event signals or a Win32 message arrives.
        let wait = unsafe {
            MsgWaitForMultipleObjectsEx(Some(&handles), INFINITE, QS_ALLINPUT, MWMO_INPUTAVAILABLE)
        };
        if wait == WAIT_OBJECT_0 {
            if drain_now() {
                break;
            }
        } else if wait == WAIT_FAILED {
            break;
        }
    }

    queue.close();
    // Ensure window destruction (and synchronous `WM_NCDESTROY` Box reclamation) runs even if the
    // loop exited due to an external `WM_QUIT` or `WAIT_FAILED`.
    if unsafe { IsWindow(Some(hwnd)) }.as_bool() {
        if let Ok(mut st) = state.try_borrow_mut() {
            st.prepare_shutdown();
        }
        unsafe {
            let _ = DestroyWindow(hwnd);
        }
    }
    // Drop `WindowState` (Direct2D/DirectWrite COM interfaces & GDI DIB) strictly BEFORE
    // `UnregisterClassW` and `ComGuard::drop` (`CoUninitialize`).
    drop(state);

    unsafe {
        let _ = UnregisterClassW(PCWSTR(class_name.as_ptr()), Some(hinstance));
    }
}

unsafe extern "system" fn overlay_wnd_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    let raw = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut Rc<RefCell<WindowState>>;
    match msg {
        WM_MOUSEACTIVATE => LRESULT(MA_NOACTIVATE as isize),
        WM_TIMER => {
            if let Some(rc) = raw.as_ref() {
                if let Ok(mut state) = rc.try_borrow_mut() {
                    state.on_timer(wparam.0);
                }
            }
            LRESULT(0)
        }
        WM_DPICHANGED => {
            let new_dpi = (wparam.0 & 0xFFFF) as u32;
            if let Some(rc) = raw.as_ref() {
                if let Ok(mut state) = rc.try_borrow_mut() {
                    state.on_dpi_changed(new_dpi);
                }
            }
            LRESULT(0)
        }
        WM_SETTINGCHANGE => {
            if let Some(rc) = raw.as_ref() {
                if let Ok(mut state) = rc.try_borrow_mut() {
                    state.on_setting_change();
                }
            }
            DefWindowProcW(hwnd, msg, wparam, lparam)
        }
        WM_LBUTTONUP => {
            let pending_action = raw.as_ref().and_then(|rc| {
                let mut state = rc.try_borrow_mut().ok()?;
                let x = (lparam.0 & 0xFFFF) as i16 as i32;
                let y = ((lparam.0 >> 16) & 0xFFFF) as i16 as i32;
                state.on_left_click(x, y)
            });
            // Invoke external callback strictly AFTER `RefMut<WindowState>` is dropped, and contain
            // any unwinding panic so it can never cross the `extern "system"` Win32 boundary.
            if let Some((cb, ev)) = pending_action {
                if std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| cb(ev))).is_err() {
                    eprintln!("pulsescribe-overlay: action callback panicked");
                }
            }
            LRESULT(0)
        }
        WM_SETCURSOR => {
            let over_interactive = raw
                .as_ref()
                .and_then(|rc| rc.try_borrow().ok())
                .is_some_and(|state| state.cursor_is_over_interactive());
            if raw.as_ref().is_some() {
                let cursor_id = if over_interactive {
                    IDC_HAND
                } else {
                    IDC_ARROW
                };
                if let Ok(cursor) = LoadCursorW(None, cursor_id) {
                    SetCursor(Some(cursor));
                    return LRESULT(1);
                }
            }
            DefWindowProcW(hwnd, msg, wparam, lparam)
        }
        WM_DESTROY => {
            PostQuitMessage(0);
            LRESULT(0)
        }
        WM_NCDESTROY => {
            if !raw.is_null() {
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                drop(Box::from_raw(raw));
            }
            DefWindowProcW(hwnd, msg, wparam, lparam)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    use pulsescribe_core::{AppState, DaemonErrorCode, DaemonStatusError};

    #[test]
    fn shared_queue_coalesces_audio_levels_and_rejects_after_close() {
        let queue = SharedQueue::new().expect("CreateEventW");
        queue.push(OverlayCommand::AudioLevel(0.1));
        queue.push(OverlayCommand::AudioLevel(0.5));
        queue.push(OverlayCommand::InterimText("hi".to_owned()));
        assert!(queue.has_pending());

        let drained = queue.drain();
        assert_eq!(drained.len(), 2);
        assert!(!queue.has_pending());

        queue.push(OverlayCommand::InterimText("before-close".to_owned()));
        queue.close();
        assert!(!queue.has_pending());
        assert!(queue.drain().is_empty());

        queue.push(OverlayCommand::InterimText("after-close".to_owned()));
        assert!(!queue.has_pending());
        assert!(queue.drain().is_empty());
    }

    #[test]
    fn windows_overlay_renders_lifecycle_and_shuts_down_cleanly() {
        let (overlay, handle) = WindowsOverlay::spawn(|_| {}).expect("WindowsOverlay::spawn");

        handle.send(OverlayCommand::ApplyState {
            state: AppState::Listening,
            detail: None,
            error: None,
        });
        handle.send(OverlayCommand::ApplyState {
            state: AppState::Recording,
            detail: None,
            error: None,
        });
        handle.send(OverlayCommand::AudioLevel(0.4));
        handle.send(OverlayCommand::InterimText(
            "Hallo Windows Direct2D Overlay".to_owned(),
        ));
        thread::sleep(Duration::from_millis(50));

        handle.send(OverlayCommand::ApplyState {
            state: AppState::Transcribing,
            detail: Some("Transcribing, then refining".to_owned()),
            error: None,
        });
        thread::sleep(Duration::from_millis(50));

        handle.send(OverlayCommand::ApplyState {
            state: AppState::Done,
            detail: Some("Transcript pasted into Notepad · 0.7 s".to_owned()),
            error: None,
        });
        handle.send(OverlayCommand::ApplyState {
            state: AppState::Error,
            detail: None,
            error: Some(DaemonStatusError::new(
                DaemonErrorCode::MissingApiKey,
                Some("DEEPGRAM_API_KEY missing".to_owned()),
            )),
        });
        handle.send(OverlayCommand::ApplyState {
            state: AppState::Idle,
            detail: None,
            error: None,
        });
        handle.dismiss();
        thread::sleep(Duration::from_millis(30));

        drop(overlay);
    }
}
