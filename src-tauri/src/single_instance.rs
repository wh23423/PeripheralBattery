//! 单实例：命名互斥体 + 激活事件（BUILD_PLAN §1.3）。
//!
//! 约定：
//! - 互斥体名默认 `Global\PeripheralBattery.single`，可用 `PB_SINGLE_INSTANCE_NAME` 覆盖
//!   （自动化测试在同一台机器上并行跑多个实例时使用）。
//! - 已存在实例时：先 SetEvent 通知其“激活面板”（`<name>.activate`），失败再
//!   ShowWindow/SetForegroundWindow 兜底，然后本进程直接退出——**不**创建托盘、**不** spawn
//!   第二个 collector。
//! - 互斥体创建失败（极端权限问题）时不阻塞启动，只 WARN。

use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, HANDLE, WAIT_OBJECT_0,
};
use windows_sys::Win32::System::Threading::{
    CreateEventW, CreateMutexW, OpenEventW, SetEvent, WaitForSingleObject, EVENT_MODIFY_STATE,
    INFINITE,
};

use tauri::AppHandle;

use crate::platform;

pub const DEFAULT_SINGLE_INSTANCE_NAME: &str = r"Global\PeripheralBattery.single";

pub enum AcquireResult {
    Acquired(SingleInstanceGuard),
    AlreadyRunning { pid: Option<u32> },
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

pub fn acquire(name: &str) -> AcquireResult {
    let mutex_name = wide(name);
    unsafe {
        let mutex = CreateMutexW(std::ptr::null(), 0, mutex_name.as_ptr());
        if mutex.is_null() {
            let err = GetLastError();
            return AcquireResult::Disabled(format!(
                "single-instance: CreateMutexW 失败 name={name} err={err}，放弃单实例保护（不影响启动）"
            ));
        }
        if GetLastError() == ERROR_ALREADY_EXISTS {
            CloseHandle(mutex);
            let pid = platform::find_existing_window().map(|(_, pid)| pid);
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
