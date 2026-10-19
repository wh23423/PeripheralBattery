//! F-03 系统通知：collector 的 `alert` 事件 → Windows Toast（WinRT）。
//!
//! 设计要点（FEATURE_PLAN §8）：
//! - Rust 侧直接弹通知，不依赖 WebView 是否可见（隐藏态/挂起时也能提醒）。
//! - 开关由 `app.json` 的 `notify_low` / `notify_full` 控制；关闭后不弹。
//! - 同一设备 + 同一告警类型 10 分钟节流；collector 自身已有「每放电周期一次、
//!   回升 > low+5 重新武装」的去重，这里只做展示层兜底。
//! - 通知失败（未签名/系统策略拦截）只记日志，绝不影响采集主循环。
//! - 面板内低电提示（toast/主卡变红）与系统通知相互独立，v0.2.0 不合并。
//!
//! 说明：未签名应用的 Toast 标题可能显示为通用名称；NSIS 安装后由系统按安装
//! 快捷方式推断 AppUserModelID，属已知限制（见 release notes）。

use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

use crate::state::AppState;

/// 同一设备 + 同一类型的系统通知最小间隔。
const THROTTLE: Duration = Duration::from_secs(10 * 60);
const TITLE: &str = "外设电量";

/// (kind, key) → 上次发送时间。使用 Vec 而不是 HashMap 以便 const 初始化；
/// 条目数 = 设备数 × 告警类型，最多几十条，线性查找足够快。
type SentEntry = ((String, String), Instant);
static LAST_SENT: Mutex<Vec<SentEntry>> = Mutex::new(Vec::new());

fn prune_expired(sent: &mut Vec<SentEntry>, now: Instant) {
    sent.retain(|(_, at)| now.duration_since(*at) < THROTTLE);
}

fn is_throttled(sent: &[SentEntry], kind: &str, key: &str) -> bool {
    sent.iter()
        .any(|((k, key2), _)| k == kind && key2 == key)
}

/// 只查不记：真正 show 成功后才占节流窗口（reviewer P3-1：show 失败不该白占 10 分钟）。
fn throttled_now(kind: &str, key: &str) -> bool {
    let now = Instant::now();
    let mut sent = match LAST_SENT.lock() {
        Ok(g) => g,
        Err(e) => e.into_inner(),
    };
    prune_expired(&mut sent, now);
    is_throttled(&sent, kind, key)
}

fn record_sent(kind: &str, key: &str) {
    let now = Instant::now();
    let mut sent = match LAST_SENT.lock() {
        Ok(g) => g,
        Err(e) => e.into_inner(),
    };
    prune_expired(&mut sent, now);
    sent.push(((kind.to_string(), key.to_string()), now));
}

/// collector `alert` 消息入口（sidecar.rs 调用）。
/// 只处理低电/充满；离线告警仍由面板内 toast 呈现（v0.2.0 范围）。
pub fn handle_alert(app: &AppHandle, parsed: &Value) {
    let kind = parsed.get("kind").and_then(|v| v.as_str()).unwrap_or("");
    let key = parsed.get("key").and_then(|v| v.as_str()).unwrap_or("");
    let level = parsed.get("level").and_then(|v| v.as_i64());
    crate::log_info!("collector alert kind={kind:?} key={key:?} level={level:?}");

    let Some(state) = app.try_state::<AppState>() else {
        return;
    };
    let (notify_low, notify_full) = {
        let cfg = match state.config.lock() {
            Ok(g) => g,
            Err(e) => e.into_inner(),
        };
        (cfg.notify_low, cfg.notify_full)
    };
    let enabled = match kind {
        "low" => notify_low,
        "full" => notify_full,
        _ => return,
    };
    if !enabled {
        crate::log_debug!("notification skipped (kind={kind} 开关关闭)");
        return;
    }
    if throttled_now(kind, key) {
        crate::log_debug!("notification throttled kind={kind} key={key}");
        return;
    }
    let body = match parsed.get("text").and_then(|v| v.as_str()) {
        Some(t) if !t.trim().is_empty() => t.trim().to_string(),
        _ => fallback_text(kind, key, level),
    };
    match app
        .notification()
        .builder()
        .title(TITLE)
        .body(body.as_str())
        .show()
    {
        Ok(()) => {
            // show 成功才占节流窗口：失败（未签名/系统策略）不阻塞下次重试。
            record_sent(kind, key);
            crate::log_info!("notification shown kind={kind} key={key} body={body}");
        }
        Err(e) => crate::log_warn!("notification show failed kind={kind} key={key}: {e}"),
    }
}

fn fallback_text(kind: &str, key: &str, level: Option<i64>) -> String {
    match kind {
        "full" => format!("{key} 已充满"),
        _ => match level {
            Some(lv) => format!("{key} 电量仅剩 {lv}%，建议充电"),
            None => format!("{key} 电量偏低，建议充电"),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(now: Instant, kind: &str, key: &str, age: Duration) -> SentEntry {
        let at = now.checked_sub(age).unwrap_or(now);
        ((kind.to_string(), key.to_string()), at)
    }

    #[test]
    fn prune_drops_entries_at_or_beyond_throttle_window() {
        let now = Instant::now();
        let mut sent = vec![
            entry(now, "low", "a", Duration::from_secs(60)),
            entry(now, "low", "b", THROTTLE), // 刚好到期 → 删除
            entry(now, "full", "c", THROTTLE + Duration::from_secs(1)),
        ];
        prune_expired(&mut sent, now);
        assert_eq!(sent.len(), 1);
        assert!(is_throttled(&sent, "low", "a"));
        assert!(!is_throttled(&sent, "low", "b"));
    }

    #[test]
    fn throttle_is_per_device_and_kind() {
        let now = Instant::now();
        let sent = vec![entry(now, "low", "logi:046d:c547", Duration::from_secs(5))];
        assert!(is_throttled(&sent, "low", "logi:046d:c547"));
        assert!(!is_throttled(&sent, "full", "logi:046d:c547"));
        assert!(!is_throttled(&sent, "low", "xinput:045e:0b00"));
    }

    #[test]
    fn failed_show_does_not_consume_throttle_window() {
        // P3-1 修复后的语义：查询不记账；只有 show 成功（record_sent）才进入节流窗口。
        let now = Instant::now();
        let mut sent: Vec<SentEntry> = Vec::new();
        assert!(!is_throttled(&sent, "low", "k"));
        sent.push(entry(now, "low", "k", Duration::ZERO));
        assert!(is_throttled(&sent, "low", "k"));
    }

    #[test]
    fn fallback_text_matches_product_copy() {
        assert_eq!(fallback_text("full", "鼠标", Some(100)), "鼠标 已充满");
        assert_eq!(
            fallback_text("low", "鼠标", Some(12)),
            "鼠标 电量仅剩 12%，建议充电"
        );
        assert_eq!(fallback_text("low", "鼠标", None), "鼠标 电量偏低，建议充电");
    }
}
