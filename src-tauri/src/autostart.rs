//! F-01 开机静默启动：HKCU Run 注册表值 + StartupApproved 只读探测。
//!
//! 契约（FEATURE_PLAN §6）：
//! - `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 值名 `PeripheralBattery`，
//!   REG_SZ，数据为 `"<当前 exe 路径>" --autostart`（路径带引号）。
//! - `...\Explorer\StartupApproved\Run` 同名值 REG_BINARY，首字节
//!   0x02/0x06 = 启用、0x03/0x07 = 被系统启动项管理禁用、其它 = 未知。
//!   只读探测，不静默改写（只有用户切换开关时才删除它）。
//! - 纯函数 [`plan_reconcile`] 覆盖 6 行 reconcile 表，便于单测。
//!
//! 所有失败都返回 `Err(String)` 供 UI 回滚 + toast；绝不 panic、不阻塞启动。

use serde::Serialize;
use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
use windows_sys::Win32::System::Registry::{
    RegDeleteKeyValueW, RegGetValueW, RegSetKeyValueW, HKEY_CURRENT_USER, RRF_RT_REG_BINARY,
    RRF_RT_REG_SZ, REG_SZ,
};

pub const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
pub const RUN_VALUE: &str = "PeripheralBattery";
pub const APPROVED_KEY: &str =
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run";

/// 注册表实际状态（app-state 的 `autostart` 字段）。
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AutoStartStatus {
    /// Run 值存在且路径与当前 exe 匹配。
    pub enabled: bool,
    /// 被 Windows「启动项管理」禁用（StartupApproved 首字节为禁用）。
    pub os_disabled: bool,
    /// Run 值原始数据（诊断用）。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    /// 意图与注册表一致，无动作。
    Noop,
    /// Run 值缺失 → 重建。
    Write,
    /// Run 值存在但路径不匹配 → 重写。
    Rewrite,
    /// 意图关闭且 Run 值存在 → 删除。
    Delete,
    /// 意图开启、路径匹配，但被系统启动项管理禁用 → 不改写，仅 UI 提示。
    DisabledByOs,
}

impl Action {
    pub fn describe(self) -> &'static str {
        match self {
            Action::Noop => "noop",
            Action::Write => "write",
            Action::Rewrite => "rewrite",
            Action::Delete => "delete",
            Action::DisabledByOs => "disabled-by-os",
        }
    }
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn decode_utf16_buf(buf: &[u16]) -> String {
    let end = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
    String::from_utf16_lossy(&buf[..end])
}

/// 展开 `%NAME%` 环境变量（Explorer 会展开 Run 值里的变量；比较前必须对齐）。
pub fn expand_env_vars(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    let chars: Vec<char> = input.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '%' {
            if let Some(rel) = chars[i + 1..].iter().position(|&c| c == '%') {
                let name: String = chars[i + 1..i + 1 + rel].iter().collect();
                if !name.is_empty() {
                    if let Ok(value) = std::env::var(&name) {
                        out.push_str(&value);
                        i = i + rel + 2;
                        continue;
                    }
                }
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

/// 拆分 Run 命令为 (exe, 是否带 --autostart)。支持带引号/不带引号两种写法。
pub fn split_command(cmd: &str) -> (String, bool) {
    let cmd = cmd.trim();
    let (exe, rest) = if let Some(inner) = cmd.strip_prefix('"') {
        match inner.find('"') {
            Some(pos) => (&inner[..pos], &inner[pos + 1..]),
            None => (inner, ""),
        }
    } else {
        // 不带引号：取第一个空白前的 token（路径含空格时无法无歧义解析，
        // 此时按路径不匹配处理，由 Rewrite 自愈）。
        match cmd.find(char::is_whitespace) {
            Some(pos) => (&cmd[..pos], &cmd[pos..]),
            None => (cmd, ""),
        }
    };
    let has_flag = rest
        .split_whitespace()
        .any(|token| token.eq_ignore_ascii_case("--autostart"));
    (exe.to_string(), has_flag)
}

fn normalize_exe(exe: &str) -> String {
    let expanded = expand_env_vars(exe.trim());
    let unquoted = expanded
        .trim()
        .trim_matches('"')
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_string();
    unquoted.to_lowercase()
}

/// Run 值是否与期望命令一致（大小写不敏感、去引号、展开环境变量）。
pub fn command_matches(stored: &str, expected: &str) -> bool {
    let (stored_exe, stored_flag) = split_command(stored);
    let (expected_exe, expected_flag) = split_command(expected);
    stored_flag == expected_flag && normalize_exe(&stored_exe) == normalize_exe(&expected_exe)
}

/// StartupApproved 二进制 → 是否被系统禁用。无法识别返回 None（未知，按启用处理）。
pub fn disabled_from_approved_bytes(data: &[u8]) -> Option<bool> {
    match data.first().copied() {
        Some(0x02) | Some(0x06) => Some(false),
        Some(0x03) | Some(0x07) => Some(true),
        _ => None,
    }
}

/// reconcile 6 行表（FEATURE_PLAN §6.5），纯函数。
pub fn plan_reconcile(
    intent: bool,
    run_value: Option<&str>,
    approved_disabled: Option<bool>,
    expected: &str,
) -> Action {
    if intent {
        match run_value {
            None => Action::Write,
            Some(stored) if !command_matches(stored, expected) => Action::Rewrite,
            Some(_) if approved_disabled == Some(true) => Action::DisabledByOs,
            Some(_) => Action::Noop,
        }
    } else if run_value.is_some() {
        Action::Delete
    } else {
        Action::Noop
    }
}

/// 期望写入的启动命令：`"<exe>" --autostart`。
pub fn expected_command() -> Option<String> {
    std::env::current_exe()
        .ok()
        .map(|p| format!("\"{}\" --autostart", p.display()))
}

pub fn read_run_value() -> Result<Option<String>, String> {
    let sub = wide(RUN_KEY);
    let name = wide(RUN_VALUE);
    let mut buf = vec![0u16; 4096];
    let mut size = (buf.len() * std::mem::size_of::<u16>()) as u32;
    let mut ty = 0u32;
    let rc = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            sub.as_ptr(),
            name.as_ptr(),
            RRF_RT_REG_SZ,
            &mut ty,
            buf.as_mut_ptr().cast(),
            &mut size,
        )
    };
    match rc {
        ERROR_SUCCESS => Ok(Some(decode_utf16_buf(&buf))),
        ERROR_FILE_NOT_FOUND => Ok(None),
        other => Err(format!("读取启动项失败(error={other})")),
    }
}

pub fn read_approved_disabled() -> Result<Option<bool>, String> {
    let sub = wide(APPROVED_KEY);
    let name = wide(RUN_VALUE);
    let mut buf = [0u8; 32];
    let mut size = buf.len() as u32;
    let mut ty = 0u32;
    let rc = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            sub.as_ptr(),
            name.as_ptr(),
            RRF_RT_REG_BINARY,
            &mut ty,
            buf.as_mut_ptr().cast(),
            &mut size,
        )
    };
    match rc {
        ERROR_SUCCESS => {
            let len = (size as usize).min(buf.len());
            Ok(Some(
                disabled_from_approved_bytes(&buf[..len]).unwrap_or(false),
            ))
        }
        ERROR_FILE_NOT_FOUND => Ok(None),
        other => Err(format!("读取启动项管理状态失败(error={other})")),
    }
}

fn write_run_value(command: &str) -> Result<(), String> {
    let sub = wide(RUN_KEY);
    let name = wide(RUN_VALUE);
    let data = wide(command);
    let rc = unsafe {
        RegSetKeyValueW(
            HKEY_CURRENT_USER,
            sub.as_ptr(),
            name.as_ptr(),
            REG_SZ,
            data.as_ptr().cast(),
            (data.len() * std::mem::size_of::<u16>()) as u32,
        )
    };
    if rc == ERROR_SUCCESS {
        Ok(())
    } else {
        Err(format!("写入启动项失败(error={rc})，可能被安全软件拦截"))
    }
}

fn delete_value(key: &str, label: &str) -> Result<(), String> {
    let sub = wide(key);
    let name = wide(RUN_VALUE);
    let rc = unsafe { RegDeleteKeyValueW(HKEY_CURRENT_USER, sub.as_ptr(), name.as_ptr()) };
    match rc {
        ERROR_SUCCESS | ERROR_FILE_NOT_FOUND => Ok(()),
        other => Err(format!("删除{label}失败(error={other})")),
    }
}

fn delete_run_value() -> Result<(), String> {
    delete_value(RUN_KEY, "启动项")
}

fn delete_approved_value() -> Result<(), String> {
    delete_value(APPROVED_KEY, "启动项管理记录")
}

/// 当前注册表状态（不修改任何东西）。
pub fn status() -> AutoStartStatus {
    let run = read_run_value().unwrap_or_else(|e| {
        crate::log_warn!("autostart: {e}");
        None
    });
    let approved_disabled = read_approved_disabled().unwrap_or_else(|e| {
        crate::log_warn!("autostart: {e}");
        None
    });
    let expected = expected_command();
    let enabled = match (&run, &expected) {
        (Some(stored), Some(expected)) => command_matches(stored, expected),
        _ => false,
    };
    AutoStartStatus {
        enabled,
        os_disabled: approved_disabled == Some(true),
        path: run,
    }
}

/// 启动自愈：按 app.json 意图同步注册表，但**保留** Windows 启动项管理的禁用记录
/// （用户可能在任务管理器里主动禁用，启动时不能静默改回）。
/// 失败时返回中文错误（供 UI 回滚 + toast）。
pub fn apply(intent: bool) -> Result<AutoStartStatus, String> {
    apply_with(intent, false)
}

/// 按用户意图同步注册表：写入/重写/删除，并尽量自愈。
///
/// `clear_os_disabled` 区分两种调用场景（FEATURE_PLAN §6.5）：
/// - 用户显式切换开关（update_settings）传 `true`：开启时删掉 StartupApproved 禁用记录，
///   否则「在设置里打开」会被系统禁用记录吞掉、开关看起来无效（reviewer P1-1）。
/// - 启动自愈传 `false`：即使路径不匹配需要重写，也不动 StartupApproved，尊重用户在
///   任务管理器里的禁用选择（UI 会显示「被系统启动项管理禁用」三态）。
pub fn apply_with(intent: bool, clear_os_disabled: bool) -> Result<AutoStartStatus, String> {
    let expected = expected_command().ok_or_else(|| "无法获取当前程序路径".to_string())?;
    let run = read_run_value()?;
    let approved = read_approved_disabled()?;
    let action = plan_reconcile(intent, run.as_deref(), approved, &expected);
    crate::log_info!(
        "autostart apply intent={intent} action={} run={:?}",
        action.describe(),
        run
    );
    match action {
        Action::Noop => {}
        Action::DisabledByOs => {
            if clear_os_disabled {
                // 用户显式打开开关：清掉禁用记录，否则 Windows 仍不会启动它。
                delete_approved_value()?;
                crate::log_info!("autostart: 用户显式开启，已清除系统启动项管理禁用记录");
            } else {
                crate::log_warn!("autostart: 启动项被 Windows 启动项管理禁用，保留原值不改写");
            }
        }
        Action::Write | Action::Rewrite => {
            if let Err(e) = write_run_value(&expected) {
                let _ = delete_run_value(); // 尽力回滚半成品
                return Err(e);
            }
            // 读回校验：安全软件可能静默拦写。
            let readback = read_run_value()?;
            match readback.as_deref() {
                Some(stored) if command_matches(stored, &expected) => {}
                _ => {
                    let _ = delete_run_value();
                    return Err("启动项写入后读回校验失败（可能被安全软件拦截）".to_string());
                }
            }
            // 只在用户显式开启时清掉系统禁用记录；启动自愈保留用户的禁用选择。
            if clear_os_disabled {
                if let Err(e) = delete_approved_value() {
                    let _ = delete_run_value();
                    return Err(e);
                }
            } else if approved == Some(true) {
                crate::log_warn!(
                    "autostart: 注册表值已重建，但保留系统启动项管理禁用记录（由用户显式开启时清除）"
                );
            }
        }
        Action::Delete => {
            delete_run_value()?;
            // 系统禁用记录删不掉不影响「已关闭」语义，只记日志。
            if let Err(e) = delete_approved_value() {
                crate::log_warn!("autostart: {e}");
            }
        }
    }
    Ok(status())
}

#[cfg(test)]
mod tests {
    use super::*;

    const EXPECTED: &str = r#""C:\Users\me\AppData\Local\PeripheralBattery\peripheral-battery.exe" --autostart"#;

    #[test]
    fn reconcile_table_has_six_rows() {
        // 1) intent=true + Run 匹配 + 未禁用 → 无动作
        assert_eq!(
            plan_reconcile(true, Some(EXPECTED), None, EXPECTED),
            Action::Noop
        );
        // 2) intent=true + Run 路径不匹配 → 重写
        assert_eq!(
            plan_reconcile(true, Some(r#""D:\old\peripheral-battery.exe" --autostart"#), None, EXPECTED),
            Action::Rewrite
        );
        // 3) intent=true + Run 缺失 → 重建
        assert_eq!(plan_reconcile(true, None, None, EXPECTED), Action::Write);
        // 4) intent=true + Run 匹配 + 被系统禁用 → 不改写
        assert_eq!(
            plan_reconcile(true, Some(EXPECTED), Some(true), EXPECTED),
            Action::DisabledByOs
        );
        // 5) intent=false + Run 存在 → 删除
        assert_eq!(
            plan_reconcile(false, Some(EXPECTED), None, EXPECTED),
            Action::Delete
        );
        // 6) intent=false + Run 缺失 → 无动作
        assert_eq!(plan_reconcile(false, None, None, EXPECTED), Action::Noop);
    }

    #[test]
    fn reconcile_rewrites_when_path_mismatched_even_if_os_disabled() {
        // 路径变化必须自愈重写；是否清除 StartupApproved 由 apply_with 的
        // clear_os_disabled 决定（启动自愈保留禁用记录，用户显式开启才清除）。
        assert_eq!(
            plan_reconcile(
                true,
                Some(r#""D:\old\peripheral-battery.exe" --autostart"#),
                Some(true),
                EXPECTED
            ),
            Action::Rewrite
        );
    }

    #[test]
    fn command_matching_is_lenient_about_quotes_case_and_env_vars() {
        assert!(command_matches(EXPECTED, EXPECTED));
        assert!(command_matches(
            r#""c:\users\me\appdata\local\peripheralbattery\peripheral-battery.exe" --autostart"#,
            EXPECTED
        ));
        // 不带引号 + 大小写不同
        assert!(command_matches(
            r#"C:\Users\me\AppData\Local\PeripheralBattery\peripheral-battery.exe --autostart"#,
            EXPECTED
        ));
        // 缺 --autostart → 不匹配
        assert!(!command_matches(
            r#""C:\Users\me\AppData\Local\PeripheralBattery\peripheral-battery.exe""#,
            EXPECTED
        ));
        // 环境变量：%PB_TEST_ROOT% 展开后匹配
        std::env::set_var("PB_TEST_ROOT", r"C:\Users\me\AppData\Local\PeripheralBattery");
        assert!(command_matches(
            r#""%PB_TEST_ROOT%\peripheral-battery.exe" --autostart"#,
            EXPECTED
        ));
    }

    #[test]
    fn split_command_handles_quoted_and_unquoted_paths() {
        assert_eq!(
            split_command(EXPECTED),
            (
                r"C:\Users\me\AppData\Local\PeripheralBattery\peripheral-battery.exe".to_string(),
                true
            )
        );
        assert_eq!(
            split_command(r#"C:\App\pb.exe --hidden --autostart"#),
            (r"C:\App\pb.exe".to_string(), true)
        );
        assert_eq!(split_command(r#""C:\App\x.exe""#), (r"C:\App\x.exe".to_string(), false));
    }

    #[test]
    fn approved_bytes_decode() {
        assert_eq!(disabled_from_approved_bytes(&[0x02, 0, 0]), Some(false));
        assert_eq!(disabled_from_approved_bytes(&[0x06, 0, 0]), Some(false));
        assert_eq!(disabled_from_approved_bytes(&[0x03, 0, 0]), Some(true));
        assert_eq!(disabled_from_approved_bytes(&[0x07, 0, 0]), Some(true));
        assert_eq!(disabled_from_approved_bytes(&[0x01, 0, 0]), None);
        assert_eq!(disabled_from_approved_bytes(&[]), None);
    }

    #[test]
    fn env_expansion_keeps_unknown_vars() {
        assert_eq!(expand_env_vars("a%NO_SUCH_VAR_1234%b"), "a%NO_SUCH_VAR_1234%b");
        assert_eq!(expand_env_vars("%%"), "%%");
    }

    #[test]
    fn expected_command_is_quoted_and_has_autostart_flag() {
        if let Some(cmd) = expected_command() {
            assert!(cmd.starts_with('"'), "cmd={cmd}");
            assert!(cmd.ends_with("--autostart"), "cmd={cmd}");
        }
    }
}
