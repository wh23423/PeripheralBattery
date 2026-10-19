//! WebView2 运行期预检（review P1-02 / P2-16）。
//!
//! 安装器已经用 `bundle.windows.webviewInstallMode = embedBootstrapper` +
//! `minimumWebview2Version = 118.0.0.0` 兜底，但离线/企业策略拦截/精简系统
//! 仍可能装不上 WebView2，或者残留低于 118 的旧运行时。此时 Tauri 创建窗口
//! 会失败，用户看到的是“双击没反应”。本模块在 `tauri::Builder` 之前调用
//! WebView2 Loader 的 `GetAvailableCoreWebView2BrowserVersionString`：
//!
//! - 未安装 WebView2：中文 MessageBoxW 提示 + 下载地址，退出码 2；
//! - 低于最低版本：中文提示升级，退出码 3；
//! - 可用：写一条 `webview2 runtime version=...` 日志后继续启动。
//!
//! 集成方式（main.rs，两行）：
//! ```ignore
//! mod webview2_check;
//! // ...在 logging::init 之后、tauri::Builder::default() 之前：
//! webview2_check::ensure_available_or_exit();
//! ```
//! 测试/排障逃生口：`PB_SKIP_WEBVIEW2_CHECK=1` 跳过本预检。

use std::env;
use std::ffi::c_void;
use std::ptr;

use webview2_com::Microsoft::Web::WebView2::Win32::GetAvailableCoreWebView2BrowserVersionString;
use windows::core::{PCWSTR, PWSTR};
use windows::Win32::System::Com::CoTaskMemFree;
use windows_sys::Win32::UI::WindowsAndMessaging::{
    MessageBoxW, MB_ICONERROR, MB_OK, MB_SETFOREGROUND, MB_TOPMOST,
};

/// 安装器配置的最低 WebView2 版本（与 tauri.conf.json 保持一致）。
pub const MIN_WEBVIEW2_VERSION: &str = "118.0.0.0";
/// WebView2 Evergreen Runtime 官方引导下载地址（安装器 embedBootstrapper 同源）。
pub const DOWNLOAD_URL: &str = "https://go.microsoft.com/fwlink/p/?LinkId=2124703";
/// 跳过预检的环境变量（仅测试/排障使用）。
pub const SKIP_ENV: &str = "PB_SKIP_WEBVIEW2_CHECK";

/// 返回已安装的 WebView2 Evergreen Runtime 版本（如 `"155.0.4283.62"`）。
/// 未安装 / Loader 不可用时返回 `None`。
pub fn installed_version() -> Option<String> {
    unsafe {
        let mut version = PWSTR::null();
        let result = GetAvailableCoreWebView2BrowserVersionString(PCWSTR::null(), &mut version);
        if let Err(err) = result {
            if !version.is_null() {
                CoTaskMemFree(Some(version.as_ptr() as *const c_void));
            }
            crate::log_warn!("webview2 GetAvailableCoreWebView2BrowserVersionString failed: {err}");
            return None;
        }
        if version.is_null() {
            return None;
        }
        let text = version.to_string().ok();
        CoTaskMemFree(Some(version.as_ptr() as *const c_void));
        text.filter(|s| !s.trim().is_empty())
    }
}

/// 解析版本号前 4 段数字（缺失段按 0；不合法段按 0）。仅用于本地比较，
/// 不追求兼容所有 semver 语法（WebView2 版本形如 `155.0.4283.62`）。
fn parse_version(version: &str) -> [u64; 4] {
    let mut parts = [0u64; 4];
    for (i, segment) in version.split('.').take(4).enumerate() {
        let digits: String = segment
            .trim()
            .chars()
            .take_while(|c| c.is_ascii_digit())
            .collect();
        parts[i] = digits.parse::<u64>().unwrap_or(0);
    }
    parts
}

/// `version >= minimum` 时返回 true（按 4 段数字比较）。
pub fn is_supported(version: &str) -> bool {
    parse_version(version) >= parse_version(MIN_WEBVIEW2_VERSION)
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

fn show_error(title: &str, body: &str) {
    let title_wide = wide(title);
    let body_wide = wide(body);
    unsafe {
        MessageBoxW(
            ptr::null_mut(),
            body_wide.as_ptr(),
            title_wide.as_ptr(),
            MB_OK | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST,
        );
    }
}

const TITLE: &str = "外设电量 PeripheralBattery";

fn missing_message() -> String {
    format!(
        "未检测到 Microsoft Edge WebView2 运行时，PeripheralBattery 无法显示界面。\n\n\
         WebView2 Runtime 是 Windows 10/11 的免费组件，可从以下地址安装后重新启动本应用：\n\
         {DOWNLOAD_URL}\n\n\
         如果你刚从安装包安装本应用，请重新运行安装包（安装器会尝试自动安装 WebView2）。\n\
         安装完成后重新启动 PeripheralBattery 即可。"
    )
}

fn too_old_message(version: &str) -> String {
    format!(
        "当前 Microsoft Edge WebView2 运行时版本过低（已安装 {version}，需要 {MIN_WEBVIEW2_VERSION} 或更高）。\n\n\
         请更新 WebView2 Runtime 后重新启动 PeripheralBattery：\n\
         {DOWNLOAD_URL}\n\n\
         也可以通过 Windows Update 或重新运行安装包来更新 WebView2。"
    )
}

/// 启动预检入口：不满足条件时弹中文错误框并退出，满足时记录日志后返回。
/// 必须在 `logging::init` 之后、`tauri::Builder::default()` 之前调用。
pub fn ensure_available_or_exit() {
    if env::var_os(SKIP_ENV).is_some() {
        crate::log_warn!("webview2 preflight skipped by {SKIP_ENV}");
        return;
    }
    let Some(version) = installed_version() else {
        crate::log_error!("webview2 runtime not found; showing error dialog and exiting");
        show_error(TITLE, &missing_message());
        std::process::exit(2);
    };
    if !is_supported(&version) {
        crate::log_error!(
            "webview2 runtime too old: installed={version} minimum={MIN_WEBVIEW2_VERSION}; exiting"
        );
        show_error(TITLE, &too_old_message(&version));
        std::process::exit(3);
    }
    crate::log_info!("webview2 runtime version={version} minimum={MIN_WEBVIEW2_VERSION}");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_release_versions_are_supported() {
        assert!(is_supported("118.0.0.0"));
        assert!(is_supported("118.0.1901.0"));
        assert!(is_supported("119.0.2151.44"));
        assert!(is_supported("155.0.4283.62"));
    }

    #[test]
    fn older_versions_are_rejected() {
        assert!(!is_supported("117.0.2045.60"));
        assert!(!is_supported("109.0.1518.78"));
        assert!(!is_supported("0.0.0.0"));
    }

    #[test]
    fn comparison_uses_numeric_segments() {
        // 99 段要按数字比，不能按字符串：99 < 118。
        assert!(!is_supported("99.0.0.0"));
        // 第 2 段更大则更大（118.0.1.0 > 118.0.0.0）。
        assert!(is_supported("118.0.1.0"));
        // 缺段按 0 补齐：118.0.0 与 118.0.0.0 数值等价。
        assert!(is_supported("118.0.0"));
        // 超出 4 段的尾部忽略（第 4 段为 0 时第 5 段不影响最低版本判定）。
        assert!(is_supported("118.0.0.0.99"));
    }

    #[test]
    fn parse_handles_suffixes_and_garbage() {
        assert_eq!(parse_version("118.0.0.0"), [118, 0, 0, 0]);
        assert_eq!(parse_version("155.0.4283.62"), [155, 0, 4283, 62]);
        assert_eq!(parse_version("118"), [118, 0, 0, 0]);
        assert_eq!(parse_version("abc"), [0, 0, 0, 0]);
        assert_eq!(parse_version(""), [0, 0, 0, 0]);
    }

    #[test]
    fn error_messages_are_actionable_chinese() {
        let missing = missing_message();
        assert!(missing.contains("WebView2"));
        assert!(missing.contains(DOWNLOAD_URL));
        let old = too_old_message("117.0.0.0");
        assert!(old.contains("117.0.0.0"));
        assert!(old.contains(MIN_WEBVIEW2_VERSION));
        assert!(old.contains(DOWNLOAD_URL));
    }
}
