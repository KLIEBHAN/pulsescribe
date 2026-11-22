//! Native macOS permission inspection and prompts (Microphone, Accessibility, Input Monitoring).
//!
//! Mirrors `utils/permissions.py`.

use std::ffi::c_void;
use std::process::{Command, Stdio};
use std::thread;

use objc2::rc::autoreleasepool;
use objc2::runtime::Bool;
use objc2::{msg_send, ClassType, Encode, Encoding};
use objc2_av_foundation::{AVAuthorizationStatus, AVCaptureDevice, AVMediaTypeAudio};
use objc2_core_foundation::{kCFBooleanTrue, CFBoolean, CFDictionary, CFString};
use objc2_core_graphics::{CGPreflightListenEventAccess, CGRequestListenEventAccess};

use crate::{MicrophonePermissionStatus, PermissionKind, PermissionsChecker, PlatformError};

#[allow(unsafe_code)]
#[link(name = "ApplicationServices", kind = "framework")]
extern "C" {
    static kAXTrustedCheckOptionPrompt: &'static CFString;
    fn AXIsProcessTrusted() -> bool;
    fn AXIsProcessTrustedWithOptions(options: Option<&CFDictionary<CFString, CFBoolean>>) -> bool;
}

#[allow(unsafe_code)]
extern "C" {
    static _NSConcreteGlobalBlock: c_void;
}

#[repr(C)]
struct BlockDescriptor {
    reserved: usize,
    size: usize,
}

#[repr(C)]
struct GlobalBoolBlock {
    isa: *const c_void,
    flags: i32,
    reserved: i32,
    invoke: unsafe extern "C" fn(*const c_void, Bool),
    descriptor: *const BlockDescriptor,
}

struct SyncGlobalBlock(GlobalBoolBlock);

#[allow(unsafe_code)]
// SAFETY: `GlobalBoolBlock` is an immutable global Clang/ObjC block literal (`BLOCK_IS_GLOBAL`)
// with no captured state; its pointer is safe to share across threads for `'static`.
unsafe impl Sync for SyncGlobalBlock {}

/// Transparent pointer to a static global Objective-C block, encoded as `@?` (`Encoding::Block`)
/// so `objc2`'s debug signature verification accepts it for `completionHandler:` parameters.
#[repr(transparent)]
#[derive(Clone, Copy)]
struct GlobalBlockPtr(*const GlobalBoolBlock);

#[allow(unsafe_code)]
// SAFETY: `GlobalBlockPtr` has the exact pointer ABI of an Objective-C block object (`@?`)
// and points only to our immutable `'static` `GlobalBoolBlock`.
unsafe impl Encode for GlobalBlockPtr {
    const ENCODING: Encoding = Encoding::Block;
}

#[allow(unsafe_code)]
unsafe extern "C" fn noop_mic_access_completion(_block: *const c_void, _granted: Bool) {}

const BLOCK_IS_GLOBAL: i32 = 1 << 28;

static MIC_BLOCK_DESCRIPTOR: BlockDescriptor = BlockDescriptor {
    reserved: 0,
    size: std::mem::size_of::<GlobalBoolBlock>(),
};

static MIC_COMPLETION_BLOCK: SyncGlobalBlock = SyncGlobalBlock(GlobalBoolBlock {
    isa: (&raw const _NSConcreteGlobalBlock).cast(),
    flags: BLOCK_IS_GLOBAL,
    reserved: 0,
    invoke: noop_mic_access_completion,
    descriptor: &raw const MIC_BLOCK_DESCRIPTOR,
});

/// Native macOS [`PermissionsChecker`] querying AVFoundation, ApplicationServices, and Quartz.
#[derive(Debug, Default, Clone, Copy)]
pub struct MacOsPermissionsChecker;

impl MacOsPermissionsChecker {
    pub fn is_accessibility_trusted() -> bool {
        #[allow(unsafe_code)]
        // SAFETY: `AXIsProcessTrusted` takes no arguments and queries process TCC trust state.
        unsafe {
            AXIsProcessTrusted()
        }
    }

    fn request_microphone_access() -> Result<(), PlatformError> {
        autoreleasepool(|_| {
            #[allow(unsafe_code)]
            // SAFETY: `AVMediaTypeAudio` is a read-only framework constant exported by AVFoundation.
            let Some(media_type) = (unsafe { AVMediaTypeAudio }) else {
                return Err(PlatformError::Unavailable("AVMediaTypeAudio unavailable"));
            };
            let block = GlobalBlockPtr(&raw const MIC_COMPLETION_BLOCK.0);
            #[allow(unsafe_code)]
            // SAFETY: `AVCaptureDevice` responds to `requestAccessForMediaType:completionHandler:`
            // accepting `AVMediaTypeAudio` and a `'static` ObjC `void (^)(BOOL)` block (`@?`).
            unsafe {
                let _: () = msg_send![
                    AVCaptureDevice::class(),
                    requestAccessForMediaType: media_type,
                    completionHandler: block
                ];
            }
            Ok(())
        })
    }

    fn request_accessibility_prompt() -> Result<(), PlatformError> {
        #[allow(unsafe_code)]
        // SAFETY: `kAXTrustedCheckOptionPrompt` and `kCFBooleanTrue` are immutable Apple framework
        // singletons; `CFDictionary::from_slices` retains both for the duration of the call.
        unsafe {
            let Some(cf_true) = kCFBooleanTrue else {
                return Err(PlatformError::Unavailable("kCFBooleanTrue unavailable"));
            };
            let options = CFDictionary::from_slices(&[kAXTrustedCheckOptionPrompt], &[cf_true]);
            let _ = AXIsProcessTrustedWithOptions(Some(&options));
        }
        Ok(())
    }
}

impl PermissionsChecker for MacOsPermissionsChecker {
    fn microphone_status(&self) -> MicrophonePermissionStatus {
        autoreleasepool(|_| {
            #[allow(unsafe_code)]
            // SAFETY: `AVMediaTypeAudio` is a framework constant; `authorizationStatusForMediaType`
            // is safe for `AVMediaTypeAudio` and does not show a prompt.
            let status = unsafe {
                let Some(media_type) = AVMediaTypeAudio else {
                    return MicrophonePermissionStatus::Unknown;
                };
                AVCaptureDevice::authorizationStatusForMediaType(media_type)
            };
            match status {
                AVAuthorizationStatus::Authorized => MicrophonePermissionStatus::Authorized,
                AVAuthorizationStatus::NotDetermined => MicrophonePermissionStatus::NotDetermined,
                AVAuthorizationStatus::Denied => MicrophonePermissionStatus::Denied,
                AVAuthorizationStatus::Restricted => MicrophonePermissionStatus::Restricted,
                _ => MicrophonePermissionStatus::Unknown,
            }
        })
    }

    fn has_accessibility(&self) -> bool {
        Self::is_accessibility_trusted()
    }

    fn has_input_monitoring(&self) -> bool {
        CGPreflightListenEventAccess()
    }

    fn request_permission(&self, kind: PermissionKind) -> Result<(), PlatformError> {
        match kind {
            PermissionKind::Microphone => Self::request_microphone_access(),
            PermissionKind::Accessibility => Self::request_accessibility_prompt(),
            PermissionKind::InputMonitoring => {
                let _ = CGRequestListenEventAccess();
                Ok(())
            }
        }
    }

    fn open_privacy_settings(&self, kind: PermissionKind) -> Result<(), PlatformError> {
        let url = kind.macos_settings_url();
        thread::Builder::new()
            .name("pulsescribe-open-settings".to_owned())
            .spawn(move || {
                let _ = Command::new("open")
                    .arg(&url)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            })
            .map_err(PlatformError::Io)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::CachedPermissionsChecker;

    #[test]
    fn permissions_snapshot_queries_all_three_tcc_apis_without_prompt() {
        let checker = CachedPermissionsChecker::new(MacOsPermissionsChecker);
        let snap = checker.snapshot();
        assert_ne!(snap.microphone, MicrophonePermissionStatus::Unknown);
    }

    #[test]
    fn microphone_request_uses_block_encoding_and_passes_objc2_signature_check() {
        assert_eq!(GlobalBlockPtr::ENCODING, Encoding::Block);
        let checker = MacOsPermissionsChecker;
        // When status is already decided, AVFoundation invokes the completion block without UI.
        if checker.microphone_status() != MicrophonePermissionStatus::NotDetermined {
            checker
                .request_permission(PermissionKind::Microphone)
                .expect(
                    "requestAccessForMediaType:completionHandler: must pass objc2 signature check",
                );
        }
    }
}
