//! Link Apple's `clang_rt.osx` when building `local-whisper` for macOS targets.
//!
//! `whisper-rs-sys`'s Metal backend (`ggml-metal-device.m`) uses Objective-C `@available(...)`
//! runtime checks that lower to `___isPlatformVersionAtLeast` whenever
//! `MACOSX_DEPLOYMENT_TARGET` is older than the SDK (such as Tauri's `minimumSystemVersion: 13.0`).
//! Because `rustc` invokes the linker with `-nodefaultlibs`, Clang does not auto-link
//! `libclang_rt.osx.a` unless explicitly requested.

use std::env;
use std::path::PathBuf;
use std::process::Command;

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

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=MACOSX_DEPLOYMENT_TARGET");
    println!("cargo:rerun-if-env-changed=CC");

    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("macos") {
        return;
    }
    if env::var_os("CARGO_FEATURE_LOCAL_WHISPER").is_none() {
        return;
    }

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
