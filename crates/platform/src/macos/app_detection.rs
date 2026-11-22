//! Native macOS frontmost application detector via `NSWorkspace`.
//!
//! Mirrors `whisper_platform/app_detection.py::MacOSAppDetector`.

use objc2::rc::autoreleasepool;
use objc2_app_kit::NSWorkspace;

use crate::AppDetector;

/// Detects the active macOS application via `NSWorkspace::sharedWorkspace()`.
#[derive(Debug, Default, Clone, Copy)]
pub struct MacOsAppDetector;

impl AppDetector for MacOsAppDetector {
    fn frontmost_app(&self) -> Option<String> {
        autoreleasepool(|_| {
            let workspace = NSWorkspace::sharedWorkspace();
            let app = workspace.frontmostApplication()?;
            let name = app.localizedName()?;
            Some(name.to_string())
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frontmost_app_queries_nsworkspace_without_panicking() {
        let detector = MacOsAppDetector;
        if let Some(name) = detector.frontmost_app() {
            assert!(!name.is_empty());
        }
    }
}
