//! Windows 平台能力：版本检测、DWM 圆角、工作区/光标、进程与 Job Object、
//! WebView2 的 `put_IsVisible`（经 `ICoreWebView2Controller::SetIsVisible`）。

use std::ffi::c_void;
use std::mem::{size_of, zeroed};
use std::sync::{Arc, Mutex};

use tauri::WebviewWindow;

use windows_sys::Win32::Foundation::{
    CloseHandle, FILETIME, HANDLE, HWND, INVALID_HANDLE_VALUE, POINT, RECT, SYSTEMTIME,
    WAIT_OBJECT_0,
};
use windows_sys::Win32::Graphics::Dwm::{
    DwmSetWindowAttribute, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND,
};
use windows_sys::Win32::Graphics::Gdi::{
    GetMonitorInfoW, MonitorFromPoint, MONITORINFO, MONITOR_DEFAULTTONEAREST,
};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, QueryInformationJobObject,
    SetInformationJobObject, TerminateJobObject, JobObjectBasicAccountingInformation,
    JobObjectExtendedLimitInformation, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Registry::{
    RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY_LOCAL_MACHINE, KEY_READ,
};
use windows_sys::Win32::System::SystemInformation::GetLocalTime;
use windows_sys::Win32::System::SystemInformation::{GetVersionExW, OSVERSIONINFOW};
use windows_sys::Win32::System::Threading::{
    GetExitCodeProcess, GetProcessTimes, OpenProcess, QueryFullProcessImageNameW, TerminateProcess,
    WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    EnumWindows, GetAncestor, GetClassNameW, GetCursorPos, GetForegroundWindow, GetWindowTextW,
    GetWindowThreadProcessId, SetForegroundWindow, ShowWindow, GA_ROOT, SW_SHOWNORMAL,
};
use windows_sys::Wdk::System::SystemServices::RtlGetVersion;

/// `SYNCHRONIZE` 进程访问权（与 FILE_ACCESS_RIGHTS 同值，不引入额外 feature）。
const SYNCHRONIZE_ACCESS: u32 = 0x0010_0000;

// ---------------------------------------------------------------------------
// 时间戳
// ---------------------------------------------------------------------------

pub fn local_timestamp() -> String {
    unsafe {
        let mut st: SYSTEMTIME = zeroed();
        GetLocalTime(&mut st);
        format!(
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02}.{:03}",
            st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, st.wMilliseconds
        )
    }
}

// ---------------------------------------------------------------------------
// Windows 版本
// ---------------------------------------------------------------------------

pub fn windows_build() -> u32 {
    // 1) RtlGetVersion：不受应用清单兼容性 shim 影响，最可靠。
    unsafe {
        let mut vi: OSVERSIONINFOW = zeroed();
        vi.dwOSVersionInfoSize = size_of::<OSVERSIONINFOW>() as u32;
        if RtlGetVersion(&mut vi) == 0 && vi.dwBuildNumber > 0 {
            return vi.dwBuildNumber;
        }
        // 2) GetVersionExW（老 API，仍可作二级兜底）
        let mut vi2: OSVERSIONINFOW = zeroed();
        vi2.dwOSVersionInfoSize = size_of::<OSVERSIONINFOW>() as u32;
        if GetVersionExW(&mut vi2) != 0 && vi2.dwBuildNumber > 0 {
            return vi2.dwBuildNumber;
        }
    }
    // 3) 注册表 CurrentBuildNumber
    registry_build().unwrap_or(0)
}

fn registry_build() -> Option<u32> {
    unsafe {
        let sub = wide("SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion");
        let mut hkey: windows_sys::Win32::System::Registry::HKEY = std::ptr::null_mut();
        if RegOpenKeyExW(HKEY_LOCAL_MACHINE, sub.as_ptr(), 0, KEY_READ, &mut hkey) != 0 {
            return None;
        }
        let name = wide("CurrentBuildNumber");
        let mut buf = [0u8; 64];
        let mut size = buf.len() as u32;
        let mut ty: u32 = 0;
        let rc = RegQueryValueExW(
            hkey,
            name.as_ptr(),
            std::ptr::null(),
            &mut ty,
            buf.as_mut_ptr(),
            &mut size,
        );
        let _ = RegCloseKey(hkey);
        if rc != 0 {
            return None;
        }
        let n = (size as usize).min(buf.len());
        let s = String::from_utf8_lossy(&buf[..n])
            .trim_end_matches('\0')
            .trim()
            .to_string();
        s.parse::<u32>().ok().filter(|v| *v > 0)
    }
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

// ---------------------------------------------------------------------------
// 窗口句柄 / DWM
// ---------------------------------------------------------------------------

pub fn window_hwnd(window: &WebviewWindow) -> Option<isize> {
    window.hwnd().ok().map(|h| h.0 as isize)
}

/// 在 Win11 上给窗口加 DWM 圆角；返回 HRESULT（S_OK = 0）。
pub fn set_dwm_round_corner(hwnd: HWND) -> i32 {
    let pref: i32 = DWMWCP_ROUND;
    unsafe {
        DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE as u32,
            &pref as *const i32 as *const c_void,
            size_of::<i32>() as u32,
        )
    }
}

pub fn raw_hwnd(h: isize) -> HWND {
    h as HWND
}

// ---------------------------------------------------------------------------
// 前台窗口 / 光标 / 工作区
// ---------------------------------------------------------------------------

/// 前台窗口的根祖先（GA_ROOT）；没有前台窗口时返回 None。
pub fn foreground_root() -> Option<isize> {
    unsafe {
        let fg = GetForegroundWindow();
        if fg.is_null() {
            return None;
        }
        let root = GetAncestor(fg, GA_ROOT);
        Some(if root.is_null() {
            fg as isize
        } else {
            root as isize
        })
    }
}

pub fn cursor_pos() -> (i32, i32) {
    unsafe {
        let mut pt: POINT = zeroed();
        if GetCursorPos(&mut pt) != 0 {
            (pt.x, pt.y)
        } else {
            (0, 0)
        }
    }
}

// ---------------------------------------------------------------------------
// 已有实例窗口（单实例）：EnumWindows 找 Tauri Window + 本程序 exe
// ---------------------------------------------------------------------------

struct FindWindowCtx {
    found: Option<(isize, u32)>,
}

unsafe extern "system" fn enum_window_cb(hwnd: HWND, lparam: windows_sys::Win32::Foundation::LPARAM) -> windows_sys::core::BOOL {
    let ctx = &mut *(lparam as *mut FindWindowCtx);
    let mut class_buf = [0u16; 256];
    let n = GetClassNameW(hwnd, class_buf.as_mut_ptr(), class_buf.len() as i32);
    let class = String::from_utf16_lossy(&class_buf[..n.max(0) as usize]);
    if class != "Tauri Window" {
        return 1;
    }
    let mut pid: u32 = 0;
    GetWindowThreadProcessId(hwnd, &mut pid);
    if pid == 0 {
        return 1;
    }
    // 同名类可能来自其它 Tauri 应用：优先匹配标题“外设电量”，否则匹配 exe 名
    let mut title_buf = [0u16; 256];
    let tn = GetWindowTextW(hwnd, title_buf.as_mut_ptr(), title_buf.len() as i32);
    let title = String::from_utf16_lossy(&title_buf[..tn.max(0) as usize]);
    let exe_match = process_info(pid)
        .map(|i| {
            let e = i.exe.to_ascii_lowercase();
            e.ends_with("peripheral-battery.exe")
        })
        .unwrap_or(false);
    if title == "外设电量" || exe_match {
        ctx.found = Some((hwnd as isize, pid));
        return 0; // stop enumeration
    }
    1
}

/// 找已有实例的主窗口（隐藏窗口也会被 EnumWindows 枚举到）。
pub fn find_existing_window() -> Option<(isize, u32)> {
    let mut ctx = FindWindowCtx { found: None };
    unsafe {
        EnumWindows(
            Some(enum_window_cb),
            &mut ctx as *mut FindWindowCtx as windows_sys::Win32::Foundation::LPARAM,
        );
    }
    ctx.found
}

/// 兜底：直接 ShowWindow + 置前（激活事件不可用时的降级路径）。
pub fn activate_existing_window() -> bool {
    match find_existing_window() {
        Some((hwnd, _)) => unsafe {
            ShowWindow(hwnd as HWND, SW_SHOWNORMAL);
            SetForegroundWindow(hwnd as HWND);
            true
        },
        None => false,
    }
}

/// 包含给定点的显示器工作区（物理像素）。
pub fn work_area_for_point(x: i32, y: i32) -> RECT {
    unsafe {
        let pt = POINT { x, y };
        let monitor = MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST);
        let mut mi: MONITORINFO = zeroed();
        mi.cbSize = size_of::<MONITORINFO>() as u32;
        if monitor.is_null() || GetMonitorInfoW(monitor, &mut mi) == 0 {
            RECT {
                left: 0,
                top: 0,
                right: 1920,
                bottom: 1080,
            }
        } else {
            mi.rcWork
        }
    }
}

// ---------------------------------------------------------------------------
// 进程工具
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
#[allow(dead_code)]
pub struct ProcInfo {
    pub pid: u32,
    /// 进程创建时间（FILETIME 100ns），用于 PID 复用校验。
    pub creation: u64,
    pub exe: String,
}

fn filetime_to_u64(ft: &FILETIME) -> u64 {
    ((ft.dwHighDateTime as u64) << 32) | ft.dwLowDateTime as u64
}

pub fn process_info(pid: u32) -> Option<ProcInfo> {
    unsafe {
        let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if h.is_null() {
            return None;
        }
        let mut creation: FILETIME = zeroed();
        let mut exit: FILETIME = zeroed();
        let mut kernel: FILETIME = zeroed();
        let mut user: FILETIME = zeroed();
        let mut out = ProcInfo {
            pid,
            creation: 0,
            exe: String::new(),
        };
        if GetProcessTimes(h, &mut creation, &mut exit, &mut kernel, &mut user) != 0 {
            out.creation = filetime_to_u64(&creation);
        }
        let mut buf = [0u16; 2048];
        let mut len = buf.len() as u32;
        if QueryFullProcessImageNameW(h, 0, buf.as_mut_ptr(), &mut len) != 0 {
            out.exe = String::from_utf16_lossy(&buf[..len as usize]);
        }
        CloseHandle(h);
        Some(out)
    }
}

/// 结束进程并等待最多 2s。返回是否确认退出。
pub fn kill_process(pid: u32) -> bool {
    unsafe {
        let h = OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if h.is_null() {
            return false;
        }
        let _ = TerminateProcess(h, 1);
        let r = WaitForSingleObject(h, 2000);
        CloseHandle(h);
        r == WAIT_OBJECT_0
    }
}

/// 按 exe 文件名（不含路径）枚举进程 PID。
pub fn find_processes_by_exe_name(name: &str) -> Vec<u32> {
    let mut out = Vec::new();
    unsafe {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snap == INVALID_HANDLE_VALUE || snap.is_null() {
            return out;
        }
        let mut pe: PROCESSENTRY32W = zeroed();
        pe.dwSize = size_of::<PROCESSENTRY32W>() as u32;
        if Process32FirstW(snap, &mut pe) != 0 {
            loop {
                let raw = String::from_utf16_lossy(&pe.szExeFile);
                let exe = raw.trim_end_matches('\0');
                if exe.eq_ignore_ascii_case(name) {
                    out.push(pe.th32ProcessID);
                }
                if Process32NextW(snap, &mut pe) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snap);
    }
    out
}

// ---------------------------------------------------------------------------
// Job Object（KILL_ON_JOB_CLOSE）
// ---------------------------------------------------------------------------

pub struct JobHandle {
    handle: isize,
}

unsafe impl Send for JobHandle {}
unsafe impl Sync for JobHandle {}

impl JobHandle {
    pub fn create() -> Option<Self> {
        unsafe {
            let h = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if h.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                h,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const c_void,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                CloseHandle(h);
                return None;
            }
            Some(Self { handle: h as isize })
        }
    }

    pub fn raw(&self) -> HANDLE {
        self.handle as HANDLE
    }

    pub fn assign(&self, process: HANDLE) -> bool {
        unsafe { AssignProcessToJobObject(self.raw(), process) != 0 }
    }

    pub fn terminate(&self, code: u32) {
        unsafe {
            TerminateJobObject(self.raw(), code);
        }
    }

    pub fn active_processes(&self) -> Option<u32> {
        unsafe {
            let mut acct: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = zeroed();
            let ok = QueryInformationJobObject(
                self.raw(),
                JobObjectBasicAccountingInformation,
                &mut acct as *mut _ as *mut c_void,
                size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                std::ptr::null_mut(),
            );
            if ok != 0 {
                Some(acct.ActiveProcesses)
            } else {
                None
            }
        }
    }
}

impl Drop for JobHandle {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.raw());
        }
    }
}

// ---------------------------------------------------------------------------
// WebView2 IsVisible（隐藏四步的第 1/2 步）
// ---------------------------------------------------------------------------

fn webview2_set_visible(window: &WebviewWindow, visible: bool) -> String {
    let result: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));
    let slot = result.clone();
    let dispatch = window.with_webview(move |platform_webview| {
        let controller = platform_webview.controller();
        let rc = unsafe { controller.SetIsVisible(visible) };
        let msg = match rc {
            Ok(()) => "S_OK".to_string(),
            Err(e) => format!("HRESULT_ERR {e}"),
        };
        *slot.lock().unwrap() = Some(msg);
    });
    match dispatch {
        Ok(()) => result
            .lock()
            .unwrap()
            .clone()
            .unwrap_or_else(|| "NOT_EXECUTED".to_string()),
        Err(e) => format!("DISPATCH_ERR {e}"),
    }
}

/// 执行 `put_IsVisible(FALSE/TRUE)`，返回结果描述字符串用于日志。
pub fn set_webview_visible(window: &WebviewWindow, visible: bool) -> String {
    webview2_set_visible(window, visible)
}

// ---------------------------------------------------------------------------
// WebView2 隐藏态降内存：ICoreWebView2_19::SetMemoryUsageTargetLevel(LOW)
// + ICoreWebView2_3::TrySuspendAsync/Resume
// ---------------------------------------------------------------------------

use webview2_com::Microsoft::Web::WebView2::Win32::{
    COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_LOW, COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_NORMAL,
    ICoreWebView2_19, ICoreWebView2_3,
};
use webview2_com::TrySuspendCompletedHandler;
use windows_core::Interface as _;

fn webview2_memory_mode(window: &WebviewWindow, low: bool) -> String {
    let result: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));
    let slot = result.clone();
    let dispatch = window.with_webview(move |platform_webview| {
        let controller = platform_webview.controller();
        let mut parts: Vec<String> = Vec::new();
        let core = match unsafe { controller.CoreWebView2() } {
            Ok(c) => c,
            Err(e) => {
                *slot.lock().unwrap() = Some(format!("CoreWebView2 ERR {e}"));
                return;
            }
        };

        match core.cast::<ICoreWebView2_19>() {
            Ok(v19) => {
                let level = if low {
                    COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_LOW
                } else {
                    COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_NORMAL
                };
                match unsafe { v19.SetMemoryUsageTargetLevel(level) } {
                    Ok(()) => parts.push(format!(
                        "MemoryUsageTargetLevel={}",
                        if low { "LOW" } else { "NORMAL" }
                    )),
                    Err(e) => parts.push(format!("MemoryUsageTargetLevel ERR {e}")),
                }
            }
            Err(e) => parts.push(format!("ICoreWebView2_19 unavailable({e})")),
        }

        match core.cast::<ICoreWebView2_3>() {
            Ok(v3) => {
                if low {
                    let handler = TrySuspendCompletedHandler::create(Box::new(
                        |res: windows_core::Result<()>, suspended: bool| {
                            match res {
                                Ok(()) => crate::log_info!(
                                    "webview TrySuspend completed suspended={suspended}"
                                ),
                                Err(e) => {
                                    crate::log_warn!("webview TrySuspend failed: {e}")
                                }
                            }
                            Ok::<(), windows_core::Error>(())
                        },
                    ));
                    match unsafe { v3.TrySuspend(&handler) } {
                        Ok(()) => parts.push("TrySuspend dispatched".to_string()),
                        Err(e) => parts.push(format!("TrySuspend ERR {e}")),
                    }
                } else {
                    match unsafe { v3.Resume() } {
                        Ok(()) => parts.push("Resume ok".to_string()),
                        Err(e) => parts.push(format!("Resume ERR {e}")),
                    }
                }
            }
            Err(e) => parts.push(format!("ICoreWebView2_3 unavailable({e})")),
        }

        *slot.lock().unwrap() = Some(parts.join("; "));
    });
    match dispatch {
        Ok(()) => result
            .lock()
            .unwrap()
            .clone()
            .unwrap_or_else(|| "NOT_EXECUTED".to_string()),
        Err(e) => format!("DISPATCH_ERR {e}"),
    }
}

/// low=true：隐藏态降内存（MemoryUsageTargetLevel=LOW + TrySuspend）；
/// low=false：显示前恢复（Resume + MemoryUsageTargetLevel=NORMAL）。
pub fn set_webview_memory_mode(window: &WebviewWindow, low: bool) -> String {
    webview2_memory_mode(window, low)
}

/// 等待进程退出（用于 sidecar 关闭握手），返回是否在超时前退出。
pub fn wait_process_exit(pid: u32, timeout_ms: u32) -> bool {
    unsafe {
        let h = OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE_ACCESS,
            0,
            pid,
        );
        if h.is_null() {
            return true;
        }
        let r = WaitForSingleObject(h, timeout_ms);
        let mut code: u32 = 0;
        let _ = GetExitCodeProcess(h, &mut code);
        CloseHandle(h);
        r == WAIT_OBJECT_0
    }
}
