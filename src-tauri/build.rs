use std::process::Command;

fn main() {
    // 构建号：优先取 git short sha，失败则 unknown。只用于诊断展示，不参与逻辑。
    let sha = Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "unknown".to_string());
    println!("cargo:rustc-env=APP_BUILD={sha}");
    println!("cargo:rerun-if-env-changed=APP_BUILD");

    tauri_build::build()
}
