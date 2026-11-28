//! Build script for `pulsescribe-local-asr`.
//!
//! 1. Links Apple's `clang_rt.osx` on macOS whenever `local-whisper` or `local-coreml` is enabled,
//!    because Objective-C/Swift `@available(...)` checks lower to `___isPlatformVersionAtLeast`
//!    whenever `MACOSX_DEPLOYMENT_TARGET` is older than the SDK and `rustc` invokes the linker
//!    with `-nodefaultlibs`.
//! 2. Compiles and statically links `PulseScribeFluidBridge` (Swift wrapper around FluidAudio)
//!    on Apple Silicon macOS when `local-coreml` is enabled.

use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=MACOSX_DEPLOYMENT_TARGET");
    println!("cargo:rerun-if-env-changed=CC");
    println!("cargo:rerun-if-changed=swift/Package.swift");
    println!("cargo:rerun-if-changed=swift/Package@swift-6.2.swift");
    println!("cargo:rerun-if-changed=swift/Sources/PulseScribeFluidBridge/FluidBridge.swift");
    println!("cargo:rerun-if-env-changed=PULSESCRIBE_SWIFT_SCRATCH_DIR");

    let target_os = env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if target_os != "macos" {
        return;
    }

    let whisper_enabled = env::var_os("CARGO_FEATURE_LOCAL_WHISPER").is_some();
    let coreml_enabled = env::var_os("CARGO_FEATURE_LOCAL_COREML").is_some()
        && env::var("CARGO_CFG_TARGET_ARCH").as_deref() == Ok("aarch64");

    if whisper_enabled || coreml_enabled {
        link_darwin_clang_rt();
    }

    if coreml_enabled {
        build_coreml_fluid_bridge();
    }
}

fn find_darwin_clang_rt_dir(program: &str, prefix_args: &[&str]) -> Option<PathBuf> {
    let output = Command::new(program)
        .args(prefix_args)
        .arg("--print-resource-dir")
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let resource_dir = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    if resource_dir.is_empty() {
        return None;
    }
    let darwin_lib_dir = PathBuf::from(resource_dir).join("lib").join("darwin");
    darwin_lib_dir
        .join("libclang_rt.osx.a")
        .is_file()
        .then_some(darwin_lib_dir)
}

fn link_darwin_clang_rt() {
    let cc = env::var("CC").unwrap_or_else(|_| "clang".to_owned());
    let darwin_lib_dir = find_darwin_clang_rt_dir(&cc, &[])
        .or_else(|| find_darwin_clang_rt_dir("xcrun", &["clang"]));

    match darwin_lib_dir {
        Some(dir) => {
            println!("cargo:rustc-link-search=native={}", dir.display());
            println!("cargo:rustc-link-lib=static=clang_rt.osx");
        }
        None => {
            println!(
                "cargo:warning=could not locate libclang_rt.osx.a via `{cc}` or `xcrun clang`; macOS @available symbols may fail to link"
            );
        }
    }
}

fn build_coreml_fluid_bridge() {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let swift_pkg_dir = manifest_dir.join("swift");
    let scratch_dir = env::var_os("PULSESCRIBE_SWIFT_SCRATCH_DIR")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env::var("OUT_DIR").expect("OUT_DIR")).join("swift-build")
        });

    std::fs::create_dir_all(&scratch_dir).expect("failed to create SwiftPM scratch directory");

    let status = Command::new("swift")
        .args([
            "build",
            "-c",
            "release",
            "--product",
            "PulseScribeFluidBridge",
            "--scratch-path",
        ])
        .arg(&scratch_dir)
        .current_dir(&swift_pkg_dir)
        .status()
        .expect("failed to invoke `swift build` for PulseScribeFluidBridge");
    assert!(
        status.success(),
        "SwiftPM build of PulseScribeFluidBridge failed (status: {status})"
    );

    let lib_dir = resolve_swift_bin_dir(&swift_pkg_dir, &scratch_dir);
    let static_lib = lib_dir.join("libPulseScribeFluidBridge.a");
    assert!(
        static_lib.is_file(),
        "expected static library at {}",
        static_lib.display()
    );

    println!("cargo:rustc-link-search=native={}", lib_dir.display());
    println!("cargo:rustc-link-lib=static=PulseScribeFluidBridge");

    // On Swift 6.0/6.1 (where Package@swift-6.2.swift traits are unavailable), SwiftPM may
    // link FluidAudio's optional NemoTextProcessing static library from the xcframework.
    if let Some(nemo_dir) = find_nemo_macos_lib_dir(&scratch_dir) {
        println!("cargo:rustc-link-search=native={}", nemo_dir.display());
        println!("cargo:rustc-link-lib=static=nemo_text_processing");
    }

    for framework in [
        "Foundation",
        "AVFoundation",
        "CoreML",
        "Accelerate",
        "Metal",
        "MetalPerformanceShaders",
    ] {
        println!("cargo:rustc-link-lib=framework={framework}");
    }
    println!("cargo:rustc-link-lib=c++");

    for path in swift_runtime_library_paths() {
        println!("cargo:rustc-link-search=native={path}");
    }
    // Ensure dyld resolves macOS system Swift runtime libraries without bundling copies.
    println!("cargo:rustc-link-arg=-Wl,-rpath,/usr/lib/swift");
}

fn resolve_swift_bin_dir(swift_pkg_dir: &Path, scratch_dir: &Path) -> PathBuf {
    if let Ok(output) = Command::new("swift")
        .args([
            "build",
            "-c",
            "release",
            "--show-bin-path",
            "--scratch-path",
        ])
        .arg(scratch_dir)
        .current_dir(swift_pkg_dir)
        .output()
    {
        if output.status.success() {
            let bin_path = String::from_utf8_lossy(&output.stdout).trim().to_owned();
            if !bin_path.is_empty() {
                let candidate = PathBuf::from(bin_path);
                if candidate.join("libPulseScribeFluidBridge.a").is_file() {
                    return candidate;
                }
            }
        }
    }

    for rel in ["release", "arm64-apple-macosx/release"] {
        let candidate = scratch_dir.join(rel);
        if candidate.join("libPulseScribeFluidBridge.a").is_file() {
            return candidate;
        }
    }
    scratch_dir.join("release")
}

fn find_nemo_macos_lib_dir(scratch_dir: &Path) -> Option<PathBuf> {
    let base =
        scratch_dir.join("artifacts/fluidaudio/NemoTextProcessing/NemoTextProcessing.xcframework");
    if !base.is_dir() {
        return None;
    }
    for entry in std::fs::read_dir(&base).ok()? {
        let path = entry.ok()?.path();
        if path.is_dir()
            && path
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|name| name.starts_with("macos"))
            && path.join("libnemo_text_processing.a").is_file()
        {
            return Some(path);
        }
    }
    None
}

fn swift_runtime_library_paths() -> Vec<String> {
    let mut paths = vec!["/usr/lib/swift".to_owned()];
    if let Ok(output) = Command::new("swift").arg("-print-target-info").output() {
        if output.status.success() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let mut in_runtime_paths = false;
            for line in stdout.lines() {
                let trimmed = line.trim();
                if trimmed.starts_with("\"runtimeLibraryPaths\"") {
                    in_runtime_paths = true;
                    continue;
                }
                if in_runtime_paths {
                    if trimmed.starts_with(']') {
                        break;
                    }
                    let cleaned = trimmed.trim_matches(|c| c == '"' || c == ',');
                    if !cleaned.is_empty() && !paths.iter().any(|p| p == cleaned) {
                        paths.push(cleaned.to_owned());
                    }
                }
            }
        }
    }
    paths
}
