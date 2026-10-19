//! Windows 平台能力：版本检测、DWM 圆角、面板/窗口几何（A14 备选B）、透明效果/远程会话检测、
//! 工作区/光标、进程与 Job Object、虚拟桌面（A7）、WebView2 的 `put_IsVisible`
//! （经 `ICoreWebView2Controller::SetIsVisible`）、隐藏态内存优化与浏览器特性加固（A9）。

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
    RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, KEY_READ,
    REG_DWORD,
};
use windows_sys::Win32::System::SystemInformation::GetLocalTime;
use windows_sys::Win32::System::SystemInformation::{GetVersionExW, OSVERSIONINFOW};
use windows_sys::Win32::System::Threading::{
    GetExitCodeProcess, GetProcessTimes, OpenProcess, QueryFullProcessImageNameW, TerminateProcess,
    WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    EnumWindows, GetAncestor, GetClassNameW, GetCursorPos, GetForegroundWindow, GetSystemMetrics,
    GetWindowTextW, GetWindowThreadProcessId, IsIconic, IsWindowVisible, SetForegroundWindow,
    ShowWindow, GA_ROOT, SM_REMOTESESSION, SW_SHOWNORMAL,
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
// 面板/窗口几何（A14 备选B：可见窗口 = 面板 720×380，四周无安全边）
//
// 实机像素验收证明 SetWindowRgn + acrylic 会让 DWM 把 acrylic 降级成纯灰
// （方案 A 前提不成立）：改为窗口直接等于面板尺寸，acrylic 只铺面板，
// 30px 安全边/窗口区域裁剪整体移除；圆角由 CSS + DWM 负责。
// ---------------------------------------------------------------------------

/// 窗口外框逻辑宽度（DIP，与 tauri.conf.json 的 width 一致；备选B = 面板宽）。
pub const WINDOW_OUTER_WIDTH_DIP: f64 = 720.0;
/// 窗口外框逻辑高度（DIP，与 tauri.conf.json 的 height 一致；备选B = 面板高）。
pub const WINDOW_OUTER_HEIGHT_DIP: f64 = 380.0;
/// 可见面板逻辑宽度（DIP，ui/index.html 的 .panel；备选B 填满窗口）。
pub const PANEL_WIDTH_DIP: f64 = 720.0;
/// 可见面板逻辑高度（DIP；备选B 填满窗口）。
pub const PANEL_HEIGHT_DIP: f64 = 380.0;
/// 安全边（DIP）：备选B 下为 0（保留常量表达布局语义）。
pub const PANEL_SAFE_MARGIN_DIP: f64 = 0.0;
/// 面板相对托盘/工作区的垂直间距（DIP）。
pub const PANEL_ANCHOR_GAP_DIP: f64 = 14.0;

/// 窗口/面板几何的物理像素度量（纯函数结果，可单测）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PanelGeometry {
    /// 可见面板相对窗口外框的偏移（物理像素；备选B 恒为 0）。
    pub inset: i32,
    /// 可见面板宽度（物理像素）。
    pub width: i32,
    /// 可见面板高度（物理像素）。
    pub height: i32,
    /// 窗口外框宽度（物理像素）。
    pub outer_width: i32,
    /// 窗口外框高度（物理像素）。
    pub outer_height: i32,
}

/// 按 DPI scale 计算窗口/面板几何的物理像素度量。
/// 非法 scale（NaN/0/负数）按 100% 处理，保证不 panic。
pub fn panel_geometry(scale: f64) -> PanelGeometry {
    let scale = if scale.is_finite() && scale > 0.0 {
        scale
    } else {
        1.0
    };
    PanelGeometry {
        inset: (PANEL_SAFE_MARGIN_DIP * scale).round() as i32,
        width: (PANEL_WIDTH_DIP * scale).round() as i32,
        height: (PANEL_HEIGHT_DIP * scale).round() as i32,
        outer_width: (WINDOW_OUTER_WIDTH_DIP * scale).round() as i32,
        outer_height: (WINDOW_OUTER_HEIGHT_DIP * scale).round() as i32,
    }
}

// ---------------------------------------------------------------------------
// 原生窗口状态 / 系统设置（A4 状态校准、A14 acrylic 降级）
// ---------------------------------------------------------------------------

/// 原生 `IsIconic`：判断窗口是否被系统最小化（Win+↓/Win+D）。
pub fn is_window_minimized(hwnd: isize) -> bool {
    unsafe { IsIconic(hwnd as HWND) != 0 }
}

/// 原生 `IsWindowVisible`：判断窗口当前是否可见（不要求在前台）。
pub fn is_window_visible(hwnd: isize) -> bool {
    unsafe { IsWindowVisible(hwnd as HWND) != 0 }
}

/// `GetSystemMetrics(SM_REMOTESESSION)`：当前会话是否为远程桌面/串流会话。
pub fn is_remote_session() -> bool {
    unsafe { GetSystemMetrics(SM_REMOTESESSION) != 0 }
}

/// 读取 HKCU\...\Themes\Personalize\EnableTransparency。
/// - `Some(true)`：系统“透明效果”开启；
/// - `Some(false)`：关闭（Win11 会把 acrylic 降级成纯灰，必须关掉窗口效果）；
/// - `None`：键不存在/查询失败（旧系统或精简系统），调用方自行决定。
pub fn transparency_enabled() -> Option<bool> {
    unsafe {
        let sub = wide("Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize");
        let mut hkey: windows_sys::Win32::System::Registry::HKEY = std::ptr::null_mut();
        if RegOpenKeyExW(HKEY_CURRENT_USER, sub.as_ptr(), 0, KEY_READ, &mut hkey) != 0 {
            return None;
        }
        let name = wide("EnableTransparency");
        let mut data: u32 = 0;
        let mut size = size_of::<u32>() as u32;
        let mut ty: u32 = 0;
        let rc = RegQueryValueExW(
            hkey,
            name.as_ptr(),
            std::ptr::null(),
            &mut ty,
            &mut data as *mut u32 as *mut u8,
            &mut size,
        );
        let _ = RegCloseKey(hkey);
        if rc != 0 || ty != REG_DWORD || size < size_of::<u32>() as u32 {
            return None;
        }
        Some(data != 0)
    }
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
// 虚拟桌面（A7 / P1-07，尽力而为）：IVirtualDesktopManager
//
// 实机验证事实（Win11 26300，独立 COM 探针）：
// 1. `IsWindowOnCurrentVirtualDesktop` 在后台进程里是「进程相对语义」：用户切到别的
//    虚拟桌面后仍可能返回 TRUE —— 不能作为跳过迁移的依据，只保留作日志参考。
// 2. `GetWindowDesktopId(普通窗口)`（记事本/Chrome）返回真实桌面 GUID；本 Tauri 窗口
//    可能 hr=S_OK 但 GUID 全 0（未知），任务栏/Progman/WorkerW 则直接失败。
// 3. 必须由 app 自己调 `MoveWindowToDesktop`（其它进程调用返回 E_ACCESSDENIED）。
// 4. 可行启发式：前台窗口（用户刚置前的记事本等）所在桌面 = 当前桌面。
//
// 因此逻辑改为：用「前台窗口的 GetWindowDesktopId」确定 target，不再用
// IsWindowOnCurrentVirtualDesktop 早退；ours 有效且等于 target 才跳过，否则迁移；
// 取不到 target（tray 点击时前台可能是任务栏/桌面窗口）→ skipped，只 WARN 不阻塞显示。
// COM 初始化失败 / RPC_E_CHANGED_MODE / 任何 HRESULT 错误都只 WARN；
// `PB_DISABLE_VD=1` 完全关闭该逻辑。
// ---------------------------------------------------------------------------

use windows::Win32::Foundation::HWND as ComHwnd;
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_ALL, COINIT_APARTMENTTHREADED,
    COINIT_DISABLE_OLE1DDE,
};
use windows::Win32::UI::Shell::{IVirtualDesktopManager, VirtualDesktopManager};

/// COM 初始化兜底：返回 `(是否需要 CoUninitialize, 描述)`。
/// RPC_E_CHANGED_MODE 表示线程已被其它组件以其它套间模式初始化，此时仍可正常 CoCreateInstance。
fn com_init() -> Result<(bool, String), String> {
    unsafe {
        let hr = CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
        if hr.is_ok() {
            // S_OK / S_FALSE：每次成功调用都必须配对一次 CoUninitialize。
            Ok((true, format!("CoInitializeEx hr=0x{:08x}", hr.0 as u32)))
        } else if hr == windows::Win32::Foundation::RPC_E_CHANGED_MODE {
            Ok((
                false,
                "CoInitializeEx=RPC_E_CHANGED_MODE（沿用线程现有套间）".to_string(),
            ))
        } else {
            Err(format!("CoInitializeEx 失败 hr=0x{:08x}", hr.0 as u32))
        }
    }
}

fn vd_manager() -> Result<IVirtualDesktopManager, String> {
    unsafe {
        CoCreateInstance::<_, IVirtualDesktopManager>(
            &VirtualDesktopManager,
            None::<&windows_core::IUnknown>,
            CLSCTX_ALL,
        )
        .map_err(|e| format!("CoCreateInstance(IVirtualDesktopManager) 失败: {e}"))
    }
}

fn com_hwnd(hwnd: isize) -> ComHwnd {
    ComHwnd(hwnd as *mut c_void)
}

/// 全 0 GUID = 未知桌面 id（Tauri 窗口实测会返回这种值），不能当作有效桌面比较。
fn is_null_desktop_id(id: &windows_core::GUID) -> bool {
    *id == windows_core::GUID::zeroed()
}

/// 纯函数决策：ours 有效且等于 target → 已在当前桌面；否则（未知/不同）迁移到 target。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum VdAction {
    AlreadyOnCurrent,
    MoveTo(windows_core::GUID),
}

fn decide_vd_action(ours: Option<windows_core::GUID>, target: windows_core::GUID) -> VdAction {
    match ours {
        Some(id) if !is_null_desktop_id(&id) && id == target => VdAction::AlreadyOnCurrent,
        _ => VdAction::MoveTo(target),
    }
}

fn ensure_on_current_virtual_desktop_inner(
    hwnd: isize,
    foreground: Option<isize>,
) -> Result<String, String> {
    let manager = vd_manager()?;
    let our = com_hwnd(hwnd);
    // 仅作日志参考：后台进程里该接口是「进程相对语义」，不能作为跳过依据。
    let on_current_hint = match unsafe { manager.IsWindowOnCurrentVirtualDesktop(our) } {
        Ok(v) => {
            if v.as_bool() {
                "true(进程相对语义，仅供参考)".to_string()
            } else {
                "false".to_string()
            }
        }
        Err(e) => format!("err({e})"),
    };

    // 1) 当前桌面启发式 = 前台窗口所在桌面。
    let Some(fg) = foreground else {
        return Ok(format!(
            "skipped: cannot determine current desktop (no foreground window); IsWindowOnCurrentVirtualDesktop={on_current_hint}"
        ));
    };
    let target = match unsafe { manager.GetWindowDesktopId(com_hwnd(fg)) } {
        Ok(id) if !is_null_desktop_id(&id) => id,
        Ok(_) => {
            return Ok(format!(
                "skipped: cannot determine current desktop (foreground=0x{fg:x} returned null desktop id); IsWindowOnCurrentVirtualDesktop={on_current_hint}"
            ));
        }
        Err(e) => {
            return Ok(format!(
                "skipped: cannot determine current desktop (GetWindowDesktopId(foreground=0x{fg:x}) failed: {e}); IsWindowOnCurrentVirtualDesktop={on_current_hint}"
            ));
        }
    };

    // 2) 自己窗口的桌面 id：失败或全 0 GUID 一律视为「未知」（不能当有效 id 比较）。
    let ours = unsafe { manager.GetWindowDesktopId(our) }
        .ok()
        .filter(|id| !is_null_desktop_id(id));

    // 3) 已在当前桌面 / 4) 迁移到 target。
    match decide_vd_action(ours, target) {
        VdAction::AlreadyOnCurrent => Ok(format!(
            "already-on-current desktop_id={target:?} IsWindowOnCurrentVirtualDesktop={on_current_hint}"
        )),
        VdAction::MoveTo(target) => {
            let ours_desc = ours
                .map(|g| format!("{g:?}"))
                .unwrap_or_else(|| "unknown".to_string());
            match unsafe { manager.MoveWindowToDesktop(our, &target) } {
                Ok(()) => Ok(format!(
                    "moved-to-current desktop_id={target:?} ours={ours_desc} IsWindowOnCurrentVirtualDesktop={on_current_hint}"
                )),
                Err(e) => Err(format!(
                    "MoveWindowToDesktop(our=0x{hwnd:x} desktop_id={target:?}) 失败: {e}（E_ACCESSDENIED=0x80070005 表示系统拒绝跨桌面移动；继续正常显示）"
                )),
            }
        }
    }
}

/// 确保窗口在当前虚拟桌面（A7）。返回一句可日志化的结论；失败仅 WARN，不阻塞显示。
pub fn ensure_on_current_virtual_desktop(hwnd: isize, foreground: Option<isize>) -> String {
    if crate::state::env_flag("PB_DISABLE_VD") {
        return "skipped PB_DISABLE_VD=1".to_string();
    }
    let (initialized, init_desc) = match com_init() {
        Ok(v) => v,
        Err(e) => {
            crate::log_warn!("virtual desktop: {e}（继续正常显示）");
            return format!("skipped: {e}");
        }
    };
    crate::log_debug!("virtual desktop: {init_desc}");
    let result = ensure_on_current_virtual_desktop_inner(hwnd, foreground);
    if initialized {
        unsafe { CoUninitialize() };
    }
    match result {
        Ok(desc) => {
            // skipped（tray 点击时前台是任务栏/桌面窗口等）也 WARN，但不阻塞显示。
            if desc.starts_with("skipped") {
                crate::log_warn!("virtual desktop: {desc}");
            }
            desc
        }
        Err(e) => {
            crate::log_warn!("virtual desktop: {e}（继续正常显示）");
            format!("failed: {e}")
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

// ---------------------------------------------------------------------------
// WebView2 加固（A9 / P1-09）：关闭默认右键菜单与浏览器加速键（F5/Ctrl+R/Ctrl+P）
//
// wry 0.57 默认 browser_accelerator_keys=true / default_context_menus=true，Tauri 2.12.4
// 未暴露配置。这里在 controller.CoreWebView2().Settings() 上直接关：
// - ICoreWebView2Settings::SetAreDefaultContextMenusEnabled(FALSE)
// - ICoreWebView2Settings3::SetAreBrowserAcceleratorKeysEnabled(FALSE)（老运行时无此接口时 WARN）
// 前端 JS 拦截保留为兜底；`PB_DISABLE_WEBVIEW2_HARDENING=1` 跳过。
// ---------------------------------------------------------------------------

use webview2_com::Microsoft::Web::WebView2::Win32::{
    ICoreWebView2Settings, ICoreWebView2Settings3,
};

fn webview2_harden_settings(window: &WebviewWindow) -> String {
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
        let settings: ICoreWebView2Settings = match unsafe { core.Settings() } {
            Ok(s) => s,
            Err(e) => {
                *slot.lock().unwrap() = Some(format!("Settings ERR {e}"));
                return;
            }
        };
        // 1) 浏览器右键菜单（base settings，所有运行时都有）
        match unsafe { settings.SetAreDefaultContextMenusEnabled(false) } {
            Ok(()) => parts.push("context_menus=disabled".to_string()),
            Err(e) => parts.push(format!("context_menus ERR {e}")),
        }
        // 2) 浏览器加速键（Settings3，WebView2 Runtime 88+；老版本 cast 失败只 WARN）
        match settings.cast::<ICoreWebView2Settings3>() {
            Ok(s3) => match unsafe { s3.SetAreBrowserAcceleratorKeysEnabled(false) } {
                Ok(()) => parts.push("accelerator_keys=disabled".to_string()),
                Err(e) => parts.push(format!("accelerator_keys ERR {e}")),
            },
            Err(e) => parts.push(format!("ICoreWebView2Settings3 unavailable({e})")),
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

/// A9：关闭 WebView2 默认右键菜单与浏览器加速键。失败只 WARN，不阻塞启动。
pub fn harden_webview2(window: &WebviewWindow) -> String {
    if crate::state::env_flag("PB_DISABLE_WEBVIEW2_HARDENING") {
        let msg = "skipped PB_DISABLE_WEBVIEW2_HARDENING=1".to_string();
        crate::log_info!("webview2 hardening: {msg}");
        return msg;
    }
    let r = webview2_harden_settings(window);
    let failed = r.starts_with("DISPATCH_ERR")
        || r == "NOT_EXECUTED"
        || r.contains("ERR")
        || r.contains("unavailable");
    if failed {
        crate::log_warn!("webview2 hardening: {r}");
    } else {
        crate::log_info!("webview2 hardening: {r}");
    }
    r
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn panel_geometry_100_percent() {
        // 备选B：窗口=面板 720×380，无安全边
        let g = panel_geometry(1.0);
        assert_eq!((g.outer_width, g.outer_height), (720, 380));
        assert_eq!((g.width, g.height), (720, 380));
        assert_eq!(g.inset, 0);
    }

    #[test]
    fn panel_geometry_125_percent() {
        let g = panel_geometry(1.25);
        assert_eq!((g.outer_width, g.outer_height), (900, 475));
        assert_eq!((g.width, g.height), (900, 475));
        assert_eq!(g.inset, 0);
    }

    #[test]
    fn panel_geometry_150_and_200_percent() {
        let g150 = panel_geometry(1.5);
        assert_eq!((g150.outer_width, g150.outer_height), (1080, 570));
        assert_eq!((g150.width, g150.height), (1080, 570));
        let g200 = panel_geometry(2.0);
        assert_eq!((g200.outer_width, g200.outer_height), (1440, 760));
        assert_eq!((g200.width, g200.height), (1440, 760));
        assert_eq!(g200.inset, 0);
    }

    #[test]
    fn panel_geometry_invalid_scale_falls_back_to_100() {
        let expected = panel_geometry(1.0);
        for bad in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert_eq!(panel_geometry(bad), expected, "scale={bad}");
        }
    }

    fn guid(value: u128) -> windows_core::GUID {
        windows_core::GUID::from_u128(value)
    }

    #[test]
    fn null_desktop_id_detection() {
        assert!(is_null_desktop_id(&windows_core::GUID::zeroed()));
        assert!(!is_null_desktop_id(&guid(1)));
        // 只有部分字节非 0 也不是 null（GUID 按 16 字节整体比较）
        assert!(!is_null_desktop_id(&windows_core::GUID {
            data1: 0,
            data2: 0,
            data3: 0,
            data4: [1, 0, 0, 0, 0, 0, 0, 0],
        }));
    }

    #[test]
    fn vd_action_decides_move_or_already() {
        let target = guid(0x1111_2222_3333_4444_5555_6666_7777_8888);
        // ours 有效且与 target 相同 → 已在当前桌面
        assert_eq!(
            decide_vd_action(Some(target), target),
            VdAction::AlreadyOnCurrent
        );
        // ours 未知（取不到 / 全 0 GUID）→ 必须尝试迁移
        assert_eq!(decide_vd_action(None, target), VdAction::MoveTo(target));
        assert_eq!(
            decide_vd_action(Some(windows_core::GUID::zeroed()), target),
            VdAction::MoveTo(target)
        );
        // ours 是别的桌面 → 迁移到 target
        assert_eq!(
            decide_vd_action(Some(guid(2)), target),
            VdAction::MoveTo(target)
        );
    }
}

