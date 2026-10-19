//! 面板显示/隐藏：隐藏四步（IsVisible → 事件 → window.hide）、托盘附近定位、
//! 失焦 270ms 抑制 + GetForegroundWindow 校验、DPI 变化重设尺寸（协议 §4.3 / BUILD_PLAN §3.1）。
//!
//! A14（备选B）：窗口 = 面板 720×380 DIP，四周不再保留 30px 安全边（实测
//! SetWindowRgn + acrylic 会让 DWM 把 acrylic 降级成纯灰，方案 A 弃用）；
//! acrylic 不可用时降级为无窗口效果并由 `app-state.acrylic` 通知前端。
//! A4：系统级最小化（Win+↓/Win+D）会走完整隐藏四步，`toggle_panel` 以原生窗口状态校准。

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use serde_json::json;
use tauri::{
    AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, WebviewWindow, WindowEvent,
};

use crate::platform;
use crate::state::AppState;

fn log_window_metrics(win: &WebviewWindow, phase: &str) {
    let scale = win.scale_factor().unwrap_or(1.0);
    match (win.outer_position(), win.outer_size()) {
        (Ok(pos), Ok(size)) => crate::log_info!(
            "window {phase} origin=({},{}) size={}x{} scale={scale:.3} dip=({:.1}x{:.1})",
            pos.x,
            pos.y,
            size.width,
            size.height,
            size.width as f64 / scale,
            size.height as f64 / scale
        ),
        _ => crate::log_warn!("window {phase} metrics unavailable"),
    }
}

/// A14：acrylic 可用性判定与降级。
///
/// `HKCU\...\Themes\Personalize\EnableTransparency=0`（Win11 会把 acrylic 降级为纯灰）、
/// `PB_DISABLE_ACRYLIC=1`、远程会话、Win10 任一命中时 `set_effects(None)`，
/// 日志 `acrylic disabled: <reason>`；否则保持 acrylic，日志 `acrylic enabled ...`。
/// 结果写入 `AppState.acrylic` 并随 `app-state` 同步给前端。
fn apply_acrylic_policy(
    app: &AppHandle,
    win: &WebviewWindow,
    windows11: bool,
    windows_build: u32,
) -> bool {
    let transparency = platform::transparency_enabled();
    let remote = platform::is_remote_session();
    let disable_reason = if !windows11 {
        Some(format!("windows10 build={windows_build}"))
    } else if crate::state::env_flag("PB_DISABLE_ACRYLIC") {
        Some("PB_DISABLE_ACRYLIC=1".to_string())
    } else if transparency == Some(false) {
        Some("transparency off (HKCU EnableTransparency=0)".to_string())
    } else if remote {
        Some("remote session (SM_REMOTESESSION)".to_string())
    } else {
        None
    };
    let state = app.state::<AppState>();
    match disable_reason {
        Some(reason) => {
            // P2-18：set_effects 失败不能吞掉——日志必须反映真实结果。
            match win.set_effects::<Option<tauri::utils::config::WindowEffectsConfig>>(None) {
                Ok(()) => crate::log_info!("acrylic disabled: {reason}"),
                Err(e) => crate::log_warn!("acrylic disabled: {reason} (set_effects 失败: {e})"),
            }
            state.set_acrylic(false);
            false
        }
        None => {
            crate::log_info!(
                "acrylic enabled build={windows_build} transparency={transparency:?} remote={remote}"
            );
            state.set_acrylic(true);
            true
        }
    }
}

/// 平台窗口装饰：Win11 DWM 圆角；acrylic 不可用时降级为无窗口效果；窗口=面板（备选B）。
pub fn apply_platform(app: &AppHandle, windows11: bool, windows_build: u32) {
    let Some(win) = app.get_webview_window("main") else {
        crate::log_error!("apply_platform: main window 不存在");
        return;
    };
    // 缓存 DPI scale：侧车线程渲染托盘图标时只读缓存，不跨线程查窗口。
    let scale = win.scale_factor().unwrap_or(1.0);
    app.state::<AppState>().set_scale_factor(scale);
    crate::log_info!("scale factor cached = {scale:.3}");
    if windows11 {
        match platform::window_hwnd(&win) {
            Some(hwnd) => {
                let hr = platform::set_dwm_round_corner(platform::raw_hwnd(hwnd));
                crate::log_info!(
                    "DWM round corner build={windows_build} hwnd=0x{hwnd:x} hresult=0x{:08x}",
                    hr as u32
                );
            }
            None => crate::log_warn!("DWM round corner: 无法获取 HWND"),
        }
    }
    // A14：acrylic 降级（含 Win10 回退），结果写入 state 供 app-state 下发。
    apply_acrylic_policy(app, &win, windows11, windows_build);
    // A9：wry/Tauri 层根治 WebView2 默认右键菜单与浏览器加速键（前端 JS 拦截保留兜底）。
    platform::harden_webview2(&win);
    // A14 备选B：窗口 = 面板 720×380 DIP，无安全边、无窗口区域裁剪；圆角由 CSS + DWM 负责。
    let geometry = platform::panel_geometry(scale);
    crate::log_info!(
        "panel geometry window={:.0}x{:.0} dip panel={:.0}x{:.0} dip (备选B：窗口=面板，无安全边) outer={}x{} scale={scale:.3}",
        platform::WINDOW_OUTER_WIDTH_DIP,
        platform::WINDOW_OUTER_HEIGHT_DIP,
        platform::PANEL_WIDTH_DIP,
        platform::PANEL_HEIGHT_DIP,
        geometry.outer_width,
        geometry.outer_height
    );
    log_window_metrics(&win, "created");
}

/// 优先用托盘图标矩形定位；`tray.rect()` 不可用时退回光标附近。
fn query_tray_rect(app: &AppHandle, scale: f64) -> Option<(i32, i32, i32, i32)> {
    let state = app.state::<AppState>();
    // 克隆后立即释放 tray 锁：tray.rect() 不能在持锁时调用（避免跨线程互相等待）。
    let tray = state.tray_clone()?;
    match tray.rect() {
        Ok(Some(rect)) => {
            let pos = rect.position.to_physical::<i32>(scale);
            let size = rect.size.to_physical::<u32>(scale);
            if size.width == 0 || size.height == 0 {
                crate::log_debug!("tray.rect() 尺寸为 0，视为不可用 → 光标兜底");
                return None;
            }
            crate::log_debug!(
                "tray.rect() = ({},{} {}x{})",
                pos.x,
                pos.y,
                size.width,
                size.height
            );
            Some((pos.x, pos.y, size.width as i32, size.height as i32))
        }
        Ok(None) => {
            crate::log_debug!("tray.rect() 不可用 → 光标兜底");
            None
        }
        Err(e) => {
            crate::log_debug!("tray.rect() 失败({e}) → 光标兜底");
            None
        }
    }
}

/// 窗口/面板的物理像素位置（备选B：窗口=面板，origin 即面板 origin）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PanelPlacement {
    x: i32,
    y: i32,
    width: i32,
    height: i32,
}

/// clamp 单轴：工作区比面板还小时退化为居中（不再硬夹，避免面板卡到一边）。
fn clamp_axis(value: i32, min: i32, max: i32, centered: i32) -> i32 {
    if max >= min {
        value.clamp(min, max)
    } else {
        centered
    }
}

/// 按「面板矩形」计算窗口位置（物理像素）——A14 备选B：窗口=面板，无安全边偏移。
///
/// - 面板水平中心对齐锚点（托盘图标/光标）中心；
/// - 垂直优先贴锚点上方，放不下放下方，再不行贴工作区底部；
/// - clamp 对象是面板矩形（=窗口矩形），四周仍留 14 DIP 工作区间距。
fn compute_panel_window_position(
    anchor: (i32, i32, i32, i32),
    work: (i32, i32, i32, i32),
    scale: f64,
) -> PanelPlacement {
    // 与 platform::panel_geometry 共用同一套非法 scale 兜底，避免尺寸/间距不一致。
    let scale = if scale.is_finite() && scale > 0.0 {
        scale
    } else {
        1.0
    };
    let geometry = platform::panel_geometry(scale);
    let margin = (platform::PANEL_ANCHOR_GAP_DIP * scale).round() as i32;
    let (ax, ay, aw, ah) = anchor;
    let (wl, wt, wr, wb) = work;

    let panel_x = ax + aw / 2 - geometry.width / 2;
    let above = ay - margin - geometry.height;
    let below = ay + ah + margin;
    let panel_y = if above >= wt + margin {
        above
    } else if below + geometry.height <= wb - margin {
        below
    } else {
        wb - margin - geometry.height
    };

    let centered_x = wl + (wr - wl - geometry.width) / 2;
    let centered_y = wt + (wb - wt - geometry.height) / 2;
    let x = clamp_axis(
        panel_x,
        wl + margin,
        wr - margin - geometry.width,
        centered_x,
    );
    let y = clamp_axis(
        panel_y,
        wt + margin,
        wb - margin - geometry.height,
        centered_y,
    );

    PanelPlacement {
        x,
        y,
        width: geometry.width,
        height: geometry.height,
    }
}

/// 位置：优先托盘矩形（点击事件记录 / tray.rect()），其次光标附近；夹在所在显示器工作区内。
/// 备选B：窗口=面板 720×380 DIP（无安全边），窗口最小化时 `outer_size()` 会返回
/// 199×34 之类的垃圾值，不能用于定位（audit P1-1）。
fn position_panel(app: &AppHandle, win: &WebviewWindow) {
    let state = app.state::<AppState>();
    let scale = win.scale_factor().unwrap_or(1.0);
    let geometry = platform::panel_geometry(scale);
    let (anchor_src, anchor) = match state.tray_rect() {
        Some(rect) => ("tray-event", rect),
        None => match query_tray_rect(app, scale) {
            Some(rect) => ("tray-rect", rect),
            None => {
                let (x, y) = platform::cursor_pos();
                ("cursor", (x, y, 0, 0))
            }
        },
    };
    let (ax, ay, aw, ah) = anchor;
    let work = platform::work_area_for_point(ax + aw / 2, ay + ah / 2);
    let placement = compute_panel_window_position(
        anchor,
        (work.left, work.top, work.right, work.bottom),
        scale,
    );

    if let Err(e) = win.set_position(PhysicalPosition::new(placement.x, placement.y)) {
        crate::log_warn!("panel set_position 失败: {e}");
    }
    // verify 脚本按 `panel position origin=... size=... scale=...` 解析；
    // 备选B 下 origin==面板原点、size==面板尺寸（720×380 DIP 对应的物理像素）。
    crate::log_info!(
        "panel position origin=({},{}) size={}x{} scale={scale:.3} panel=({},{} {}x{}) work=({},{},{},{}) anchor_src={anchor_src} anchor=({ax},{ay},{aw}x{ah})",
        placement.x,
        placement.y,
        geometry.outer_width,
        geometry.outer_height,
        placement.x,
        placement.y,
        placement.width,
        placement.height,
        work.left,
        work.top,
        work.right,
        work.bottom
    );
}

/// 显示面板。顺序：恢复内存 → A7 虚拟桌面 → show() → **还原窗口尺寸** → 定位 →
/// IsVisible(TRUE) → 事件 → 焦点。
pub fn show_panel(app: &AppHandle, reason: &str) {
    let state = app.state::<AppState>();
    state.bump_hide_generation();
    let Some(win) = app.get_webview_window("main") else {
        crate::log_error!("show_panel: main window 不存在");
        return;
    };
    // 恢复 WebView2 内存模式（TrySuspend 之后必须 Resume 才能正常出帧）
    let mem = platform::set_webview_memory_mode(&win, false);
    crate::log_info!("webview memory mode normal: {mem}");
    // A7：窗口可能属于创建时的虚拟桌面。先判断/迁移到当前桌面再显示（失败只 WARN，不阻塞）。
    if let Some(hwnd) = platform::window_hwnd(&win) {
        let vd = platform::ensure_on_current_virtual_desktop(hwnd, platform::foreground_root());
        crate::log_info!("window virtual desktop reason={reason} result={vd}");
    }
    if let Err(e) = win.show() {
        crate::log_error!("window.show 失败: {e}");
    }
    // A4 修复（系统最小化后显示成 199×34 占位条）：窗口被 Win+↓/Win+D 最小化后，
    // hide 四步会把 IsIconic 清掉，而 show() 恢复的是「最小化占位尺寸」；此时
    // is_minimized()/IsIconic 都已为 false，不能再用条件判断跳过。必须在 show 之后
    // 无条件还原：unminimize（对正常窗口无害）+ set_size(720×380 DIP)（幂等）。
    let scale = win.scale_factor().unwrap_or(1.0);
    if let Err(e) = win.unminimize() {
        crate::log_warn!("window.unminimize 失败: {e}");
    }
    match win.set_size(LogicalSize::new(
        platform::WINDOW_OUTER_WIDTH_DIP,
        platform::WINDOW_OUTER_HEIGHT_DIP,
    )) {
        Ok(()) => crate::log_info!(
            "window size restored {:.0}x{:.0} dip scale={scale:.3}",
            platform::WINDOW_OUTER_WIDTH_DIP,
            platform::WINDOW_OUTER_HEIGHT_DIP
        ),
        Err(e) => crate::log_warn!("window size restore 失败: {e}"),
    }
    // 尺寸还原后再按面板矩形定位（position 不依赖 outer_size，但定位/日志需要正确的窗口尺寸）。
    position_panel(app, &win);
    let vis = platform::set_webview_visible(&win, true);
    crate::log_info!("window show reason={reason} webview IsVisible(true) -> {vis}");
    let _ = app.emit("window-visibility", json!({ "hidden": false }));
    // 长时间隐藏（TrySuspend）后，前端可能错过隐藏期间的 device/tick：
    // 主动让 collector 回一个全量 tick，保证打开面板 ≤200ms 内显示最新数据。
    let snap = state.sidecar.request_snapshot();
    crate::log_info!("window show reason={reason} requested collector snapshot={snap}");
    state.set_window_visible(true);
    if let Err(e) = win.set_focus() {
        crate::log_warn!("window.set_focus 失败: {e}");
    }
    // 托盘点击引起的显示：给失焦隐藏留出抑制窗口
    state.suppress_for_ms(450);
    // A14：同步 app-state（隐藏期间 pin/acrylic 等 Rust 侧变更在 Resume 后补发给前端）。
    state.emit_app_state(app);
    crate::log_info!("window shown reason={reason}");
    log_window_metrics(&win, "shown");
    crate::fps::arm_after_show(app);
    state.arm_pb_hide(app);
}

/// 隐藏后延迟降 WebView2 内存（TrySuspend 需要前端先处理完 window-visibility）。
fn schedule_memory_suspend(app: &AppHandle) {
    let handle = app.clone();
    let _ = std::thread::Builder::new()
        .name("webview-suspend".into())
        .spawn(move || {
            std::thread::sleep(Duration::from_millis(300));
            let h = handle.clone();
            let _ = handle.run_on_main_thread(move || {
                let Some(win) = h.get_webview_window("main") else {
                    return;
                };
                let state = h.state::<AppState>();
                if state.window_visible() {
                    return; // 300ms 内又被显示，放弃 suspend
                }
                let r = platform::set_webview_memory_mode(&win, true);
                crate::log_info!("webview memory mode low (hidden): {r}");
            });
        });
}

/// 隐藏面板。顺序：IsVisible(FALSE) → 事件 → window.hide() → 延迟 TrySuspend。
pub fn hide_panel(app: &AppHandle, reason: &str, force: bool) {
    let state = app.state::<AppState>();
    state.bump_hide_generation();
    if !force && state.pinned() {
        crate::log_debug!("window hide skipped (pinned) reason={reason}");
        return;
    }
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let vis = platform::set_webview_visible(&win, false);
    crate::log_info!("window hide reason={reason} webview IsVisible(false) -> {vis}");
    let _ = app.emit("window-visibility", json!({ "hidden": true }));
    if let Err(e) = win.hide() {
        crate::log_warn!("window.hide 失败: {e}");
    }
    state.set_window_visible(false);
    crate::log_info!("window hidden reason={reason}");
    schedule_memory_suspend(app);
}

/// A4：用原生窗口状态校准「面板当前是否真的可见」。
/// 系统级最小化（Win+↓/Win+D/显示桌面）不会触发 Focused(false)，`state.window_visible`
/// 可能滞留为 true；此时若按 state 切换会走 hide（用户看到第一次点击无效），
/// 因此切换/事件路径统一以 `IsWindowVisible && !IsIconic` 为准。
fn effective_visible(win: &WebviewWindow) -> (bool, bool, bool) {
    let native_visible = match win.is_visible() {
        Ok(v) => v,
        Err(_) => platform::window_hwnd(win)
            .map(platform::is_window_visible)
            .unwrap_or(true),
    };
    let native_iconic = match win.is_minimized() {
        Ok(v) => v,
        Err(_) => platform::window_hwnd(win)
            .map(platform::is_window_minimized)
            .unwrap_or(false),
    };
    (native_visible && !native_iconic, native_visible, native_iconic)
}

pub fn toggle_panel(app: &AppHandle, source: &str) {
    let state = app.state::<AppState>();
    let Some(win) = app.get_webview_window("main") else {
        crate::log_warn!("toggle_panel: main window 不存在");
        return;
    };
    let (effective, native_visible, native_iconic) = effective_visible(&win);
    let state_visible = state.window_visible();
    crate::log_info!(
        "toggle_panel source={source} state_visible={state_visible} native_visible={native_visible} iconic={native_iconic} effective={effective}"
    );
    if state_visible != effective {
        // 校准滞留状态：icontic（系统最小化）或窗口已被外部隐藏时直接 show，不能误走 hide。
        crate::log_warn!(
            "toggle_panel: window_visible 状态校准 {state_visible} -> {effective}（系统级最小化/外部状态变化）"
        );
        state.set_window_visible(effective);
    }
    if effective {
        hide_panel(app, source, true);
    } else {
        show_panel(app, source);
    }
}

static SYSTEM_MINIMIZE_HANDLING: AtomicBool = AtomicBool::new(false);

struct SystemMinimizeGuard;

impl Drop for SystemMinimizeGuard {
    fn drop(&mut self) {
        SYSTEM_MINIMIZE_HANDLING.store(false, Ordering::SeqCst);
    }
}

/// A4：检测到系统级最小化（Win+↓/Win+D）→ 走完整隐藏四步（IsVisible(false) →
/// emit window-visibility → hide → state=false → TrySuspend），避免隐藏态优化失效与
/// 下一次点托盘无效。用模块级 guard 防止 hide() 产生的嵌套窗口事件递归进入。
fn handle_system_minimize(app: &AppHandle) {
    if SYSTEM_MINIMIZE_HANDLING.swap(true, Ordering::SeqCst) {
        return;
    }
    let _guard = SystemMinimizeGuard;
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let (_, native_visible, native_iconic) = effective_visible(&win);
    if !native_visible || !native_iconic {
        return;
    }
    let state = app.state::<AppState>();
    if !state.window_visible() {
        crate::log_debug!("system-minimize detected but already hidden (state_visible=false)");
        return;
    }
    crate::log_info!("window system-minimize detected (Win+↓/Win+D) -> full hide four-step");
    hide_panel(app, "system-minimize", true);
}

/// 窗口事件：失焦延迟隐藏、系统最小化识别、DPI 变化重设尺寸、关闭按钮 = 隐藏。
pub fn attach_window_events(app: &AppHandle) {
    let Some(win) = app.get_webview_window("main") else {
        crate::log_error!("attach_window_events: main window 不存在");
        return;
    };
    let handle = app.clone();
    win.on_window_event(move |event| match event {
        WindowEvent::CloseRequested { api, .. } => {
            api.prevent_close();
            crate::log_info!("window close requested -> hide");
            hide_panel(&handle, "close-requested", true);
        }
        WindowEvent::Focused(false) => {
            // A4：系统级最小化可能先发失焦；命中时立即走完整隐藏四步，否则才延迟失焦隐藏。
            let minimized = handle
                .get_webview_window("main")
                .map(|w| effective_visible(&w).2)
                .unwrap_or(false);
            if minimized {
                handle_system_minimize(&handle);
            } else {
                schedule_auto_hide(&handle, 270);
            }
        }
        WindowEvent::Focused(true) => {
            let state = handle.state::<AppState>();
            state.bump_hide_generation();
        }
        // tao 没有独立的 Minimized 事件：WM_SIZE（最小化/还原）→ Resized，
        // Win+↓/Win+D 也会触发 WM_WINDOWPOSCHANGED → Moved；两处都做一次图标化检测。
        WindowEvent::Resized(_) | WindowEvent::Moved(_) => {
            handle_system_minimize(&handle);
        }
        WindowEvent::ScaleFactorChanged { scale_factor, .. } => {
            crate::log_info!("scaleFactorChanged scale={scale_factor:.3}");
            let state = handle.state::<AppState>();
            state.set_scale_factor(*scale_factor);
            if let Some(w) = handle.get_webview_window("main") {
                // A14 备选B：窗口=面板，仅按新 scale 重设 720×380 DIP，无窗口区域可重建。
                if let Err(e) = w.set_size(LogicalSize::new(
                    platform::WINDOW_OUTER_WIDTH_DIP,
                    platform::WINDOW_OUTER_HEIGHT_DIP,
                )) {
                    crate::log_warn!("scaleFactorChanged: set_size 失败: {e}");
                }
                log_window_metrics(&w, "scale-changed");
            }
            crate::tray::update_tray_icon(&handle, "dpi");
            if state.window_visible() {
                if let Some(w) = handle.get_webview_window("main") {
                    position_panel(&handle, &w);
                }
            }
        }
        _ => {}
    });

    maybe_test_tray_click_hook(app);
}

/// [debug-only] 托盘点击注入钩子（A4/A14 实机验收用）：默认启动 2.5s 后等价于一次托盘左键点击。
/// `PB_TEST_TRAY_CLICK=<ms>`（≥500）可自定义延迟，便于"先系统最小化、再点击恢复尺寸"场景
/// （例如 PB_SHOW_ON_START=1 + PB_TEST_TRAY_CLICK=6000：2s 内最小化，6s 时点击显示）。
/// 与真实托盘点击走完全相同的 `toggle_panel` 分支；release 构建不编译此代码。
#[cfg(debug_assertions)]
fn maybe_test_tray_click_hook(app: &AppHandle) {
    let Ok(raw) = std::env::var("PB_TEST_TRAY_CLICK") else {
        return;
    };
    let delay_ms = raw
        .trim()
        .parse::<u64>()
        .ok()
        .filter(|v| *v >= 500)
        .unwrap_or(2500);
    let handle = app.clone();
    let spawned = std::thread::Builder::new()
        .name("test-tray-click".into())
        .spawn(move || {
            std::thread::sleep(Duration::from_millis(delay_ms));
            let h = handle.clone();
            let _ = handle.run_on_main_thread(move || {
                crate::log_info!(
                    "[debug-only] PB_TEST_TRAY_CLICK={raw} -> toggle_panel (delay_ms={delay_ms})"
                );
                crate::panel::toggle_panel(&h, "PB_TEST_TRAY_CLICK");
            });
        });
    if spawned.is_err() {
        crate::log_warn!("[debug-only] PB_TEST_TRAY_CLICK 钩子线程创建失败");
    }
}

#[cfg(not(debug_assertions))]
fn maybe_test_tray_click_hook(_app: &AppHandle) {}

/// ui_ready 且默认不显示时：让 WebView2 停止出帧（隐藏四步第 4 项）。
pub fn mark_startup_hidden(app: &AppHandle) {
    let state = app.state::<AppState>();
    state.bump_hide_generation();
    if let Some(win) = app.get_webview_window("main") {
        let vis = platform::set_webview_visible(&win, false);
        crate::log_info!("window startup hidden webview IsVisible(false) -> {vis}");
        let _ = app.emit("window-visibility", json!({ "hidden": true }));
        let _ = win.hide();
        log_window_metrics(&win, "startup-hidden");
    }
    state.set_window_visible(false);
    crate::log_info!("window hidden reason=startup-default");
    schedule_memory_suspend(app);
}

fn schedule_auto_hide(app: &AppHandle, delay_ms: u64) {
    let state = app.state::<AppState>();
    if state.pinned() || !state.window_visible() {
        return;
    }
    let generation = state.bump_hide_generation();
    let handle = app.clone();
    let _ = std::thread::Builder::new()
        .name("auto-hide".into())
        .spawn(move || {
            std::thread::sleep(Duration::from_millis(delay_ms));
            {
                let state = handle.state::<AppState>();
                if state.hide_generation() != generation {
                    crate::log_debug!("auto-hide cancelled (generation)");
                    return;
                }
                if state.pinned() || !state.window_visible() || state.is_suppressed() {
                    crate::log_debug!("auto-hide skipped (pinned/visible/suppressed)");
                    return;
                }
            }
            let h2 = handle.clone();
            let _ = handle.run_on_main_thread(move || {
                let state = h2.state::<AppState>();
                if state.hide_generation() != generation
                    || state.pinned()
                    || !state.window_visible()
                    || state.is_suppressed()
                {
                    return;
                }
                let ours = h2
                    .get_webview_window("main")
                    .and_then(|w| platform::window_hwnd(&w));
                let fg = platform::foreground_root();
                if let (Some(ours), Some(fg)) = (ours, fg) {
                    if ours == fg {
                        crate::log_debug!("auto-hide skipped: foreground is self");
                        return;
                    }
                }
                crate::log_info!("auto-hide triggered foreground={fg:?} ours={ours:?}");
                hide_panel(&h2, "focus-lost", false);
            });
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    const BOTTOM_WORK: (i32, i32, i32, i32) = (0, 0, 1920, 1040);

    #[test]
    fn placement_centers_panel_on_tray_horizontally() {
        // 托盘在底部中间：面板中心 = 托盘中心，面板底边在托盘上方（留 14px 间距）
        // 备选B：窗口=面板 720×380，placement 的 x/y 同时是窗口与面板原点。
        let tray = (900, 1000, 32, 32);
        let p = compute_panel_window_position(tray, BOTTOM_WORK, 1.0);
        assert_eq!((p.width, p.height), (720, 380));
        assert_eq!(p.x + p.width / 2, tray.0 + tray.2 / 2);
        assert_eq!(p.x, 916 - 360);
        assert_eq!(p.y, 1000 - 14 - 380);
        assert_eq!(p.y + p.height, tray.1 - 14);
    }

    #[test]
    fn placement_clamps_panel_rect_to_work_area() {
        // 托盘贴右下角：面板右边缘贴工作区（留 14px），窗口=面板所以窗口也在工作区内
        let tray = (1800, 1000, 32, 32);
        let p = compute_panel_window_position(tray, BOTTOM_WORK, 1.0);
        assert_eq!(p.x + p.width, BOTTOM_WORK.2 - 14);
        assert_eq!(p.x, 1186);
        assert_eq!(p.y, 606);
        assert!(p.x + p.width <= BOTTOM_WORK.2);
        assert!(p.x >= BOTTOM_WORK.0 + 14);
        assert!(p.y >= BOTTOM_WORK.1 + 14);
        assert!(p.y + p.height <= BOTTOM_WORK.3);
    }

    #[test]
    fn placement_puts_panel_below_top_taskbar() {
        // 顶部任务栏：上方放不下 → 面板放托盘下方（至少留 14px 工作区边距）
        let work = (0, 40, 1920, 1080);
        let tray = (1800, 4, 32, 32);
        let p = compute_panel_window_position(tray, work, 1.0);
        assert_eq!(p.y, 54); // clamp 到 work.top + 14
        assert!(p.y > tray.1 + tray.3);
    }

    #[test]
    fn placement_handles_negative_coordinate_monitor() {
        // 副屏在主屏左侧（负坐标），托盘贴该屏右下角
        let work = (-1920, 0, 0, 1040);
        let tray = (-96, 1000, 32, 32);
        let p = compute_panel_window_position(tray, work, 1.0);
        assert_eq!(p.x + p.width, work.2 - 14);
        assert_eq!(p.x, -734);
        assert!(p.x >= work.0 + 14);
    }

    #[test]
    fn placement_125_percent_uses_physical_metrics() {
        // 125%：窗口=面板 = 900×475 物理像素，间距 margin = round(14*1.25) = 18
        let tray = (900, 1000, 32, 32);
        let p = compute_panel_window_position(tray, BOTTOM_WORK, 1.25);
        assert_eq!((p.width, p.height), (900, 475));
        assert_eq!(p.y + p.height, 1000 - 18);
        assert_eq!(p.x + p.width / 2, 916);
    }

    #[test]
    fn placement_centers_when_work_area_smaller_than_panel() {
        // 工作区比面板还小：水平方向退化为居中，不能 panic 或把面板挤到负边界外
        let work = (0, 0, 800, 600);
        let tray = (400, 560, 32, 32);
        let p = compute_panel_window_position(tray, work, 1.25);
        assert_eq!(p.x, (800 - 900) / 2);
        // 垂直方向仍优先贴托盘上方（67 = 560 - 18 - 475），放得下就不居中
        assert_eq!(p.y, 67);
        assert!(p.y + p.height <= work.3);
    }

    #[test]
    fn placement_garbage_scale_falls_back_to_100_percent() {
        let tray = (900, 1000, 32, 32);
        let normal = compute_panel_window_position(tray, BOTTOM_WORK, 1.0);
        for bad in [0.0, -2.0, f64::NAN] {
            assert_eq!(
                compute_panel_window_position(tray, BOTTOM_WORK, bad),
                normal
            );
        }
    }
}
