//! 托盘：resvg 运行时渲染状态色图标（电量/充电/离线/设备类型），左键切换面板，
//! 右键菜单（刷新 / 固定 / 设置 / 退出）。位置由 `tray.rect()` 提供（协议 §3.2）。

use serde_json::json;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager};

use crate::state::AppState;

#[derive(Clone, Debug, PartialEq)]
pub struct TrayState {
    pub level: Option<i64>,
    pub charging: bool,
    pub online: bool,
    pub kind: String,
}

impl Default for TrayState {
    fn default() -> Self {
        Self {
            level: None,
            charging: false,
            online: true,
            kind: "unknown".to_string(),
        }
    }
}

// ---------------------------------------------------------------------------
// 图标渲染
// ---------------------------------------------------------------------------

fn main_color(ts: &TrayState) -> &'static str {
    if !ts.online {
        return "#94a3b8";
    }
    if ts.charging {
        return "#38bdf8";
    }
    match ts.level {
        Some(l) if l >= 50 => "#34d399",
        Some(l) if l >= 20 => "#fbbf24",
        Some(_) => "#f87171",
        None => "#94a3b8",
    }
}

fn kind_color(kind: &str) -> &'static str {
    match kind {
        "mouse" => "#60a5fa",
        "keyboard" => "#a78bfa",
        "headset" => "#f472b6",
        "gamepad" => "#fb923c",
        "bluetooth" => "#38bdf8",
        _ => "#94a3b8",
    }
}

fn kind_glyph(kind: &str) -> &'static str {
    match kind {
        "mouse" => r#"<rect x="25.6" y="24.2" width="1.9" height="3.5" rx="0.95" fill='#0f172a'/>"#,
        "keyboard" => {
            r#"<circle cx="25.1" cy="25" r="0.62" fill='#0f172a'/><circle cx="26.6" cy="25" r="0.62" fill='#0f172a'/><circle cx="28.1" cy="25" r="0.62" fill='#0f172a'/><rect x="25.1" y="26.6" width="3.6" height="0.9" rx="0.45" fill='#0f172a'/>"#
        }
        "headset" => {
            r#"<path d="M24.9 26.7 v-1.3 a1.7 1.7 0 0 1 3.4 0 v1.3" fill="none" stroke='#0f172a' stroke-width="1.05" stroke-linecap="round"/>"#
        }
        "gamepad" => {
            r#"<path d="M26.6 24.3 v3.4 M24.9 26 h3.4" stroke='#0f172a' stroke-width="1.1" stroke-linecap="round"/>"#
        }
        "bluetooth" => {
            r#"<path d="M25.4 24.2 l2.4 1.9 -2.4 1.9 M25.4 27.9 l2.4-1.9 -2.4-1.9" fill="none" stroke='#0f172a' stroke-width="0.95" stroke-linejoin="round"/>"#
        }
        _ => r#"<circle cx="26.6" cy="26" r="0.9" fill='#0f172a'/>"#,
    }
}

fn build_svg(ts: &TrayState) -> String {
    let color = main_color(ts);
    let kind = kind_color(&ts.kind);
    let glyph = kind_glyph(&ts.kind);

    let known = ts.level.is_some() && ts.online;
    let (outline_extra, fill) = if known {
        let lvl = ts.level.unwrap().clamp(0, 100);
        let w = (16.5 * lvl as f64 / 100.0).max(0.0);
        (
            String::new(),
            format!(
                r#"<rect x="5.4" y="12.1" width="{w:.2}" height="7.8" rx="1.6" fill="{color}"/>"#
            ),
        )
    } else {
        (
            r#" stroke-dasharray="2.4 2.2""#.to_string(),
            String::new(),
        )
    };

    let charging = if ts.charging && ts.online {
        r#"<path d="M15.4 10.2 L11.4 17.1 h3.1 l-1.0 5.0 L18.6 15.2 h-3.1 z" fill='#ffffff' stroke='#0f172a' stroke-width="0.55" stroke-linejoin="round"/>"#
    } else {
        ""
    };

    let offline = if !ts.online {
        r#"<path d="M4.2 24.2 L28.2 6.2" stroke='#64748b' stroke-width="2.1" stroke-linecap="round" fill="none"/>"#
    } else {
        ""
    };

    format!(
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
  <rect x="3.1" y="9.6" width="21.2" height="12.8" rx="3.6" fill="none" stroke="{color}" stroke-width="2.0"{outline_extra}/>
  <rect x="25.1" y="13.4" width="3.3" height="5.2" rx="1.2" fill="{color}"/>
  {fill}
  {charging}
  {offline}
  <circle cx="26.5" cy="26.0" r="4.4" fill="{kind}" stroke='#0f172a' stroke-width="1.1"/>
  {glyph}
</svg>
"##
    )
}

fn render_tray_icon(ts: &TrayState, scale: f64) -> Option<Image<'static>> {
    let px = ((32.0 * scale).round() as u32).clamp(16, 96);
    let svg = build_svg(ts);
    let tree = resvg::usvg::Tree::from_str(&svg, &resvg::usvg::Options::default()).ok()?;
    let mut pixmap = resvg::tiny_skia::Pixmap::new(px, px)?;
    let k = px as f32 / 32.0;
    resvg::render(
        &tree,
        resvg::tiny_skia::Transform::from_scale(k, k),
        &mut pixmap.as_mut(),
    );
    Some(Image::new_owned(pixmap.data().to_vec(), px, px))
}

fn fallback_icon() -> Image<'static> {
    match Image::from_bytes(include_bytes!("../icons/32x32.png")) {
        Ok(img) => img,
        Err(_) => {
            // 4x4 全透明兜底，保证托盘一定创建成功
            let px: Vec<u8> = vec![0u8; 4 * 4 * 4];
            Image::new_owned(px, 4, 4)
        }
    }
}

fn tooltip_for(ts: &TrayState) -> String {
    if !ts.online {
        return "外设电量 · 离线".to_string();
    }
    match ts.level {
        Some(l) if ts.charging => format!("外设电量 · {l}% 充电中"),
        Some(l) => format!("外设电量 · {l}%"),
        None => "外设电量 · 未知".to_string(),
    }
}

/// 当前 DPI scale：优先读 AppState 缓存（侧车线程/主线程都安全），未缓存时回退查窗口
/// （窗口 API 只能在主线程调用；调用点均在主线程）。
fn current_scale(app: &AppHandle) -> f64 {
    if let Some(scale) = app.state::<AppState>().cached_scale_factor() {
        return scale;
    }
    app.get_webview_window("main")
        .and_then(|w| w.scale_factor().ok())
        .unwrap_or(1.0)
}

// ---------------------------------------------------------------------------
// 托盘创建 / 更新
// ---------------------------------------------------------------------------

pub fn create_tray(app: &AppHandle) -> tauri::Result<()> {
    let refresh = MenuItem::with_id(app, "refresh", "刷新", true, None::<&str>)?;
    let pin = MenuItem::with_id(app, "pin", "固定面板", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "设置", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&refresh, &pin, &settings, &separator, &quit])?;

    let state = app.state::<AppState>();
    let ts = state.tray_state.lock().unwrap().clone();
    let scale = current_scale(app);
    let icon = render_tray_icon(&ts, scale).unwrap_or_else(fallback_icon);

    let tray = TrayIconBuilder::with_id("peripheral-battery-tray")
        .icon(icon)
        .tooltip(tooltip_for(&ts))
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| handle_menu(app, event.id().as_ref()))
        .on_tray_icon_event(|tray, event| handle_tray_event(tray.app_handle(), event))
        .build(app)?;

    *state.tray.lock().unwrap() = Some(tray);
    crate::log_info!(
        "tray created id=peripheral-battery-tray scale={scale} icon_color={}",
        main_color(&ts)
    );
    Ok(())
}

/// 托盘更新统一入口：**非阻塞投递到主线程**执行。
///
/// 死锁根因（P0）：Sidecar 线程与主线程并发调用本函数时，若持有 `state.tray` 锁去调用
/// `set_icon/set_tooltip`（Shell_NotifyIcon 会同步到主线程托盘窗口），侧车线程持锁等主线程、
/// 主线程等同一把锁 → 互等。现在：
/// - 状态读取/图标渲染/句柄克隆都在主线程闭包里，且**克隆后立即释放 tray 锁**；
/// - tray API 调用期间不持任何锁；
/// - 主线程路径（run_on_main_thread 内联）与侧车路径（事件投递）天然串行。
pub fn update_tray_icon(app: &AppHandle, source: &str) {
    let handle = app.clone();
    let source_owned = source.to_string();
    let source_for_error = source.to_string();
    if let Err(e) =
        app.run_on_main_thread(move || update_tray_icon_on_main(&handle, &source_owned))
    {
        crate::log_warn!("tray-update dispatch failed source={source_for_error}: {e}");
    }
}

fn update_tray_icon_on_main(app: &AppHandle, source: &str) {
    let state = app.state::<AppState>();
    let ts = { state.tray_state.lock().unwrap().clone() };
    crate::log_info!(
        "tray-update begin source={source} level={:?} charging={} online={} kind={}",
        ts.level,
        ts.charging,
        ts.online,
        ts.kind
    );
    let scale = current_scale(app);
    let Some(icon) = render_tray_icon(&ts, scale) else {
        crate::log_warn!("tray-update end source={source} render_failed（保留旧图标）");
        return;
    };
    let tooltip = tooltip_for(&ts);
    // 关键：取克隆后立刻释放 tray 锁，后续 Shell_NotifyIcon 调用期间不持锁。
    let tray = state.tray_clone();
    let Some(tray) = tray else {
        crate::log_warn!("tray-update end source={source} tray_not_created");
        return;
    };
    let icon_ok = match tray.set_icon(Some(icon)) {
        Ok(()) => true,
        Err(e) => {
            crate::log_warn!("tray-update set_icon failed source={source}: {e}");
            false
        }
    };
    let tooltip_ok = match tray.set_tooltip(Some(tooltip.clone())) {
        Ok(()) => true,
        Err(e) => {
            crate::log_warn!("tray-update set_tooltip failed source={source}: {e}");
            false
        }
    };
    crate::log_info!(
        "tray-update end source={source} icon_ok={icon_ok} tooltip_ok={tooltip_ok} scale={scale} tooltip={tooltip}"
    );
}

fn handle_tray_event(app: &AppHandle, event: TrayIconEvent) {
    if let TrayIconEvent::Click {
        button: MouseButton::Left,
        button_state: MouseButtonState::Up,
        rect,
        ..
    } = event
    {
        let scale = current_scale(app);
        let pos = rect.position.to_physical::<i32>(scale);
        let size = rect.size.to_physical::<u32>(scale);
        let state = app.state::<AppState>();
        state.set_tray_rect((
            pos.x,
            pos.y,
            size.width as i32,
            size.height as i32,
        ));
        crate::log_info!(
            "tray left click rect=({},{} {}x{}) scale={scale}",
            pos.x,
            pos.y,
            size.width,
            size.height
        );
        // 点托盘 → 失焦 → 立即隐藏 的抖动抑制
        state.suppress_for_ms(350);
        crate::panel::toggle_panel(app, "tray-click");
    }
}

pub(crate) fn handle_menu(app: &AppHandle, id: &str) {
    match id {
        "refresh" => {
            let _ = app.emit("tray-action", json!({ "action": "refresh" }));
            let state = app.state::<AppState>();
            let ok = state.sidecar.refresh();
            crate::log_info!("tray action=refresh collector_refresh={ok}");
        }
        "pin" => {
            let _ = app.emit("tray-action", json!({ "action": "toggle-pin" }));
            crate::log_info!("tray action=toggle-pin");
        }
        "settings" => {
            // 默认启动是隐藏的：必须先走正常四步显示面板，否则抽屉开在隐藏窗口里，
            // 用户右键「设置」看不到任何反馈（audit P2-1）。
            crate::panel::show_panel(app, "tray-settings");
            let _ = app.emit("tray-action", json!({ "action": "settings" }));
            crate::log_info!("tray action=settings (panel shown before emit)");
        }
        "quit" => {
            let _ = app.emit("tray-action", json!({ "action": "quit" }));
            crate::log_info!("tray action=quit");
            crate::state::request_quit(app, "tray-menu");
        }
        _ => {}
    }
}
