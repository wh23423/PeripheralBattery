//! 构建脚本：注入构建号（commit sha），并让 release 在提交变化后重新编译。
//!
//! review P2-03：旧版 build.rs 只在首次编译时执行 `git rev-parse`，之后
//! `.git/HEAD` 变化不会触发重跑，release 日志里的 `build=` 永远是 `unknown`
//! 或旧值，安装包无法回溯 commit。这里：
//! - 监听 `.git/HEAD`、`.git/refs/heads`、`.git/packed-refs`，任何提交/branch
//!   切换都会让 build.rs 重跑；
//! - 支持 `PB_BUILD_SHA` / `PB_BUILD` 环境变量注入（CI / 无 .git 的源码包）；
//! - 同时输出 `APP_BUILD`（main.rs 既有用法）与 `PB_BUILD`（新契约）两个
//!   `cargo:rustc-env`，值缺省为 `unknown`。

use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;

/// 由 main.rs 通过 `env!("APP_BUILD")` 打印；保留旧名字避免改调用方。
const BUILD_ENV: &str = "APP_BUILD";
/// 新契约：build.rs 注入的构建号（`env!("PB_BUILD")` 可用）。
const PB_BUILD_ENV: &str = "PB_BUILD";

fn manifest_dir() -> PathBuf {
    env::var_os("CARGO_MANIFEST_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// 显式注入优先（CI 上通常没有 .git）：`PB_BUILD_SHA` > `PB_BUILD` > git。
fn build_sha() -> Option<String> {
    for key in ["PB_BUILD_SHA", "PB_BUILD"] {
        if let Ok(raw) = env::var(key) {
            let value = raw.trim();
            if !value.is_empty() {
                return Some(value.chars().take(40).collect());
            }
        }
    }
    let out = Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .current_dir(manifest_dir())
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let sha = String::from_utf8(out.stdout).ok()?;
    let sha = sha.trim();
    if sha.is_empty() {
        None
    } else {
        Some(sha.to_string())
    }
}

/// 提交变化触发重跑的监听路径。`.git` 在 worktree 里可能是文件（gitdir: ...），
/// 不存在时（源码包/CI）不输出，避免 cargo 对不存在路径告警。
fn watch_git_paths() {
    let git = manifest_dir().parent().map(|p| p.join(".git"));
    let Some(git) = git else { return };
    if !git.exists() {
        return;
    }
    let candidates = [git.join("HEAD"), git.join("packed-refs"), git.join("refs/heads")];
    for path in candidates {
        if path.exists() {
            if let Some(text) = path.to_str() {
                println!("cargo:rerun-if-changed={text}");
            }
        }
    }
    // worktree（.git 为文件）时把 gitdir 指向的目录也监听起来
    if git.is_file() {
        if let Ok(content) = std::fs::read_to_string(&git) {
            if let Some(rest) = content.trim().strip_prefix("gitdir:") {
                let real = Path::new(rest.trim());
                let real = if real.is_absolute() {
                    real.to_path_buf()
                } else {
                    manifest_dir().join(real)
                };
                for name in ["HEAD", "refs/heads"] {
                    let path = real.join(name);
                    if path.exists() {
                        if let Some(text) = path.to_str() {
                            println!("cargo:rerun-if-changed={text}");
                        }
                    }
                }
            }
        }
    }
}

fn main() {
    watch_git_paths();
    for key in ["PB_BUILD_SHA", "PB_BUILD", BUILD_ENV] {
        println!("cargo:rerun-if-env-changed={key}");
    }

    let sha = build_sha().unwrap_or_else(|| "unknown".to_string());
    println!("cargo:rustc-env={BUILD_ENV}={sha}");
    println!("cargo:rustc-env={PB_BUILD_ENV}={sha}");

    tauri_build::build()
}
