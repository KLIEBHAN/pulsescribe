#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    let code = pulsescribe_desktop::run();
    if code != 0 {
        std::process::exit(code);
    }
}
