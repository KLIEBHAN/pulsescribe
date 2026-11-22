//! Native macOS sound player via CoreAudio (`AudioToolbox`) with `afplay` fallback.
//!
//! Mirrors `whisper_platform/sound.py::MacOSSoundPlayer`.

use std::collections::{HashMap, HashSet};
use std::process::{Command, Stdio};
use std::ptr::NonNull;
use std::sync::Mutex;
use std::thread;

use objc2_audio_toolbox::{
    AudioServicesCreateSystemSoundID, AudioServicesDisposeSystemSoundID,
    AudioServicesPlaySystemSound, SystemSoundID,
};
use objc2_core_foundation::{CFString, CFURLPathStyle, CFURL};

use crate::{lock_unpoisoned, SoundName, SoundPlayer};

#[derive(Debug, Default)]
struct SoundCache {
    sound_ids: HashMap<SoundName, SystemSoundID>,
    failed: HashSet<SoundName>,
}

/// CoreAudio system-sound player caching `SystemSoundID` handles (~0.2 ms playback).
#[derive(Debug, Default)]
pub struct MacOsSoundPlayer {
    cache: Mutex<SoundCache>,
}

impl MacOsSoundPlayer {
    pub fn new() -> Self {
        Self::default()
    }

    fn load_sound_id(path: &str) -> Option<SystemSoundID> {
        let cf_path = CFString::from_str(path);
        let cf_url = CFURL::with_file_system_path(
            None,
            Some(&cf_path),
            CFURLPathStyle::CFURLPOSIXPathStyle,
            false,
        )?;
        let mut sound_id: SystemSoundID = 0;
        #[allow(unsafe_code)]
        // SAFETY: `cf_url` is a valid retained `CFURL` reference and `sound_id` is a valid,
        // aligned, writable `SystemSoundID` pointer for the duration of the synchronous FFI call.
        let status =
            unsafe { AudioServicesCreateSystemSoundID(&cf_url, NonNull::from(&mut sound_id)) };
        if status == 0 {
            Some(sound_id)
        } else {
            tracing::debug!(path, status, "AudioServicesCreateSystemSoundID failed");
            None
        }
    }

    fn play_fallback(path: &'static str) {
        let _ = thread::Builder::new()
            .name("pulsescribe-afplay".to_owned())
            .spawn(move || {
                let _ = Command::new("afplay")
                    .arg(path)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            });
    }
}

impl SoundPlayer for MacOsSoundPlayer {
    fn play(&self, sound: SoundName) {
        let path = sound.macos_system_path();
        let sound_id = {
            let mut cache = lock_unpoisoned(&self.cache);
            if cache.failed.contains(&sound) {
                None
            } else if let Some(&id) = cache.sound_ids.get(&sound) {
                Some(id)
            } else if let Some(id) = Self::load_sound_id(path) {
                cache.sound_ids.insert(sound, id);
                Some(id)
            } else {
                cache.failed.insert(sound);
                None
            }
        };

        if let Some(id) = sound_id {
            #[allow(unsafe_code)]
            // SAFETY: `id` was returned by `AudioServicesCreateSystemSoundID` and stays valid
            // until `MacOsSoundPlayer::drop` disposes it.
            unsafe {
                AudioServicesPlaySystemSound(id);
            }
        } else {
            Self::play_fallback(path);
        }
    }
}

impl Drop for MacOsSoundPlayer {
    fn drop(&mut self) {
        let cache = lock_unpoisoned(&self.cache);
        for &id in cache.sound_ids.values() {
            #[allow(unsafe_code)]
            // SAFETY: Each cached `SystemSoundID` was created by `AudioServicesCreateSystemSoundID`
            // and is disposed exactly once during `Drop`.
            unsafe {
                let _ = AudioServicesDisposeSystemSoundID(id);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coreaudio_loads_and_disposes_system_sound_id() {
        let id = MacOsSoundPlayer::load_sound_id(SoundName::Ready.macos_system_path())
            .expect("macOS Tink.aiff should load via AudioServices");
        let player = MacOsSoundPlayer::new();
        lock_unpoisoned(&player.cache)
            .sound_ids
            .insert(SoundName::Ready, id);
        drop(player);

        assert!(
            MacOsSoundPlayer::load_sound_id("/System/Library/Sounds/DoesNotExist.aiff").is_none()
        );
    }
}
