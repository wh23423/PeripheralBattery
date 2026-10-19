//! 单实例：命名互斥体 + 激活事件（BUILD_PLAN §1.3）。
//!
//! 约定（review P1-05）：
//! - 默认对象名 `Local\PeripheralBattery.single`：`Local\` 命名空间天然按登录会话隔离，
//!   RDP / 快速用户切换下不同会话各有一个实例互不干扰；同会话内仍保持单实例。
//!   旧版本的 `Global\` 会在跨会话时静默激活别人桌面的面板，或（DACL 拒绝时）静默起
//!   第二个实例/第二个 collector。
//! - `PB_SINGLE_INSTANCE_NAME` 可覆盖（并行自动化测试使用；注意它只隔离单实例对象，
//!   并行测试还需独立的 `LOCALAPPDATA` 与独立安装副本，见 README）。
//! - 已存在实例时：先 SetEvent 通知其“激活面板”（`<name>.activate`），失败再
//!   ShowWindow/SetForegroundWindow 兜底，然后本进程直接退出——**不**创建托盘、
//!   **不** spawn 第二个 collector。
//! - **权限不一致不静默降级**：`CreateMutexW` 因权限被拒（`ERROR_ACCESS_DENIED`）时，
//!   若 `find_existing_window()` 能找到本程序窗口，说明已有另一权限级别的实例在运行
//!   （典型：UAC 提权/非提权混用）——弹中文提示后退出；只有连窗口都找不到时才 WARN
//!   继续（无法确认已有实例，不能凭空阻止启动）。

use std::ffi::c_void;
use std::mem::size_of;

use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_ACCESS_DENIED, ERROR_ALREADY_EXISTS, HANDLE, WAIT_OBJECT_0,
};
use windows_sys::Win32::Security::{
    GetTokenInformation, TokenElevation, TOKEN_ELEVATION, TOKEN_QUERY,
};
use windows_sys::Win32::System::Threading::{
    CreateEventW, CreateMutexW, GetCurrentProcess, OpenEventW, OpenProcessToken, SetEvent,
    WaitForSingleObject, EVENT_MODIFY_STATE, INFINITE,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    MessageBoxW, MB_ICONWARNING, MB_OK, MB_SETFOREGROUND, MB_TOPMOST,
};

use tauri::AppHandle;

use crate::platform;

/// 默认单实例对象名：`Local\` 前缀 = 当前登录会话命名空间（按会话隔离）。
pub const DEFAULT_SINGLE_INSTANCE_NAME: &str = r"Local\PeripheralBattery.single";

pub enum AcquireResult {
    Acquired(SingleInstanceGuard),
    AlreadyRunning { pid: Option<u32> },
    /// 同名互斥体已存在但当前权限打不开（UAC 提权/跨权限实例）。`pid` 是
    /// `find_existing_window()` 找到的本程序窗口进程；`None` = 没找到任何本程序窗口。
    AccessDenied { pid: Option<u32> },
    Disabled(String),
}

/// 持有互斥体 + 激活事件句柄；进程生命周期内不能 drop。
pub struct SingleInstanceGuard {
    mutex: HANDLE,
    event: HANDLE,
    name: String,
}

impl SingleInstanceGuard {
    pub fn activation_event_raw(&self) -> Option<isize> {
        if self.event.is_null() {
            None
        } else {
            Some(self.event as isize)
        }
    }

    pub fn name(&self) -> &str {
        &self.name
    }
}

impl Drop for SingleInstanceGuard {
    fn drop(&mut self) {
        unsafe {
            if !self.event.is_null() {
                CloseHandle(self.event);
            }
            if !self.mutex.is_null() {
                CloseHandle(self.mutex);
            }
        }
    }
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

// ---------------------------------------------------------------------------
// 会话 / 权限语义（仅用于日志，便于排查 RDP、快速用户切换、UAC 混用）
// ---------------------------------------------------------------------------

/// 当前进程是否以提升后的令牌运行（UAC elevation）。
fn is_elevated() -> bool {
    unsafe {
        let mut token: HANDLE = std::ptr::null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            return false;
        }
        let mut elevation: TOKEN_ELEVATION = std::mem::zeroed();
        let mut ret_len: u32 = 0;
        let ok = GetTokenInformation(
            token,
            TokenElevation,
            &mut elevation as *mut TOKEN_ELEVATION as *mut c_void,
            size_of::<TOKEN_ELEVATION>() as u32,
            &mut ret_len,
        );
        CloseHandle(token);
        ok != 0 && elevation.TokenIsElevated != 0
    }
}

/// `session=<SESSIONNAME> user=<DOMAIN\USER> elevated=<bool>`：权限冲突日志的公共片段。
///
/// 注：精确的 SessionId 需要 `ProcessIdToSessionId`（Win32_System_RemoteDesktop feature），
/// 本 crate 未启用该 feature；`SESSIONNAME` 足以区分 Console / RDP-Tcp#n 会话语义。
fn permission_context() -> String {
    let session = std::env::var("SESSIONNAME")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| "unknown".to_string());
    let user = match (std::env::var("USERDOMAIN"), std::env::var("USERNAME")) {
        (Ok(d), Ok(u)) => format!("{d}\\{u}"),
        (_, Ok(u)) => u,
        _ => "unknown".to_string(),
    };
    format!(
        "session={session} user={user} elevated={}",
        is_elevated()
    )
}

pub fn acquire(name: &str) -> AcquireResult {
    let mutex_name = wide(name);
    let ctx = permission_context();
    unsafe {
        let mutex = CreateMutexW(std::ptr::null(), 0, mutex_name.as_ptr());
        if mutex.is_null() {
            let err = GetLastError();
            if err == ERROR_ACCESS_DENIED {
                // 同名对象存在但当前权限无法打开：不要静默降级成第二个实例。
                let existing = platform::find_existing_window();
                crate::log_warn!(
                    "single-instance: CreateMutexW ERROR_ACCESS_DENIED name={name} err={err} {ctx} existing_window_pid={:?}",
                    existing.as_ref().map(|(_, pid)| *pid)
                );
                return AcquireResult::AccessDenied {
                    pid: existing.map(|(_, pid)| pid),
                };
            }
            return AcquireResult::Disabled(format!(
                "single-instance: CreateMutexW 失败 name={name} err={err} {ctx}，放弃单实例保护（不影响启动）"
            ));
        }
        if GetLastError() == ERROR_ALREADY_EXISTS {
            CloseHandle(mutex);
            let pid = platform::find_existing_window().map(|(_, pid)| pid);
            crate::log_info!(
                "single-instance: already running name={name} {ctx} existing_window_pid={pid:?}"
            );
            return AcquireResult::AlreadyRunning { pid };
        }

        let event_name = wide(&format!("{name}.activate"));
        let event = CreateEventW(std::ptr::null(), 0, 0, event_name.as_ptr());
        if event.is_null() {
            crate::log_warn!(
                "single-instance: CreateEventW 失败 name={name}.activate err={}（退化为 ShowWindow 激活）",
                GetLastError()
            );
        }
        crate::log_info!(
            "single-instance: mutex acquired name={name} pid={} {ctx}",
            std::process::id()
        );
        AcquireResult::Acquired(SingleInstanceGuard {
            mutex,
            event,
            name: name.to_string(),
        })
    }
}

/// 通知已有实例激活面板。返回是否成功 SetEvent。
pub fn signal_existing(name: &str) -> bool {
    let event_name = wide(&format!("{name}.activate"));
    unsafe {
        let event = OpenEventW(EVENT_MODIFY_STATE, 0, event_name.as_ptr());
        if event.is_null() {
            return false;
        }
        let ok = SetEvent(event) != 0;
        CloseHandle(event);
        ok
    }
}

/// 权限冲突提示（review P1-05）：可见的中文 MessageBox，避免“启动没反应/双实例”。
/// 返回是否真正弹了框（MessageBoxW 失败时至少日志可见）。
pub fn show_permission_conflict_message(existing_pid: Option<u32>) -> bool {
    let pid_line = match existing_pid {
        Some(pid) => format!("\n\n已有实例 pid={pid}。"),
        None => String::new(),
    };
    let text = format!(
        "已有另一权限级别实例在运行，请退出后重试。\n\n\
         本程序同一登录会话只允许运行一个实例。若你以管理员身份运行过本程序，\
         请先退出那个实例，或用相同权限级别重新启动。{pid_line}"
    );
    let title = "外设电量";
    let text_w = wide(&text);
    let title_w = wide(title);
    unsafe {
        let ret = MessageBoxW(
            std::ptr::null_mut(),
            text_w.as_ptr(),
            title_w.as_ptr(),
            MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST,
        );
        crate::log_warn!(
            "single-instance: permission conflict dialog shown ret={ret} {}",
            permission_context()
        );
    }
    true
}

/// 在已有实例里等待激活事件 → 走正常的 show_panel（含 IsVisible(true) 与事件）。
pub fn spawn_activation_listener(app: AppHandle, event_raw: isize) {
    let spawned = std::thread::Builder::new()
        .name("single-instance-activate".into())
        .spawn(move || loop {
            let r = unsafe { WaitForSingleObject(event_raw as HANDLE, INFINITE) };
            if r != WAIT_OBJECT_0 {
                break;
            }
            let handle = app.clone();
            let dispatched = app.run_on_main_thread(move || {
                crate::log_info!("single-instance: activation event received, showing panel");
                crate::panel::show_panel(&handle, "single-instance");
            });
            if dispatched.is_err() {
                break;
            }
        });
    if spawned.is_err() {
        crate::log_warn!("single-instance: 激活监听线程创建失败");
    }
}

#[cfg(test)]
mod tests {
    use super::{acquire, AcquireResult, DEFAULT_SINGLE_INSTANCE_NAME};

    #[test]
    fn default_name_is_session_local() {
        // P1-05：默认必须是 Local\ 命名空间（按登录会话隔离），不能再用 Global\。
        assert!(
            DEFAULT_SINGLE_INSTANCE_NAME.starts_with(r"Local\"),
            "{DEFAULT_SINGLE_INSTANCE_NAME}"
        );
        assert!(DEFAULT_SINGLE_INSTANCE_NAME.ends_with(".single"));
    }

    #[test]
    fn second_acquire_reports_already_running() {
        // 真实 Windows 命名互斥体路径（无需 GUI）：同一名字第二次 acquire → AlreadyRunning；
        // guard drop 后可以重新获取。
        let name = format!(
            r"Local\PeripheralBattery.test.{}.{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        );
        let first = match acquire(&name) {
            AcquireResult::Acquired(guard) => guard,
            _ => panic!("first acquire should succeed"),
        };
        match acquire(&name) {
            AcquireResult::AlreadyRunning { .. } => {}
            _ => panic!("second acquire should report AlreadyRunning"),
        }
        drop(first);
        match acquire(&name) {
            AcquireResult::Acquired(_) => {}
            _ => panic!("acquire after drop should succeed"),
        }
    }
}
