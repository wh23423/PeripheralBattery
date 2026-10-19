//! Rust 侧托盘状态镜像：直接解析 collector 的 NDJSON（hello/device/gone/tick），
//! 维护「在线设备中 level 最小者」，变化时更新托盘图标，不依赖 WebView。
//!
//! 背景（Lead 联合验证发现）：隐藏 300ms 后对 WebView2 做 TrySuspendAsync，JS 被冻结，
//! 前端 syncTray 在隐藏期间无法调用 set_tray_state → 托盘长期停在旧值。托盘是隐藏态的
//! 唯一环境指示器，必须由 Rust 侧独立驱动。前端 set_tray_state 保留（可见态/兜底），
//! 两路更新都走 `AppState::update_tray_from_devices`，按 payload 去重，天然幂等。
//!
//! 口径与 ui/app.js `syncTray()` 一致：
//! - 只在 devices.size>0 之后开始下发（首次数据前保留默认「未知」图标）；
//! - online = online !== false（缺省视为在线）；
//! - 在线设备中取 level 最小者（level=null 参与排序时视为 Infinity，可被选为 target 并透传 null）；
//! - 全部离线 → {level:null, charging:false, online:false, kind:""}。

use std::collections::HashMap;

use serde_json::Value;
use tauri::{AppHandle, Manager};

use crate::state::AppState;
use crate::tray::TrayState;

#[derive(Clone, Debug)]
struct DeviceLite {
    level: Option<i64>,
    charging: bool,
    online: bool,
    kind: String,
}

impl Default for DeviceLite {
    fn default() -> Self {
        Self {
            level: None,
            charging: false,
            online: true,
            kind: String::new(),
        }
    }
}

#[derive(Default)]
pub struct TrayMirror {
    devices: HashMap<String, DeviceLite>,
    order: Vec<String>,
    primed: bool,
    last_payload: Option<String>,
}

impl TrayMirror {
    fn reset(&mut self) {
        self.devices.clear();
        self.order.clear();
        self.primed = false;
    }

    fn upsert(&mut self, d: &Value) {
        let Some(key) = d.get("key").and_then(|v| v.as_str()) else {
            return;
        };
        if !self.devices.contains_key(key) {
            self.order.push(key.to_string());
        }
        let level = d
            .get("level")
            .and_then(|v| v.as_i64())
            .map(|v| v.clamp(0, 100));
        let online = d.get("online").and_then(|v| v.as_bool()).unwrap_or(true);
        let charging = d.get("charging").and_then(|v| v.as_bool()).unwrap_or(false);
        let kind = d
            .get("kind")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        self.devices.insert(
            key.to_string(),
            DeviceLite {
                level,
                charging,
                online,
                kind,
            },
        );
    }

    fn remove(&mut self, key: &str) {
        self.devices.remove(key);
        self.order.retain(|k| k != key);
    }

    fn upsert_list(&mut self, devices: &[Value]) {
        for d in devices {
            self.upsert(d);
        }
    }

    /// tick 是全量对齐：upsert 所有设备，删除不在列表里的 key（保留首次出现顺序）。
    fn apply_tick(&mut self, devices: &[Value]) {
        let mut keys = std::collections::HashSet::new();
        for d in devices {
            if let Some(k) = d.get("key").and_then(|v| v.as_str()) {
                keys.insert(k.to_string());
            }
            self.upsert(d);
        }
        let stale: Vec<String> = self
            .order
            .iter()
            .filter(|k| !keys.contains(*k))
            .cloned()
            .collect();
        for k in stale {
            self.remove(&k);
        }
    }

    fn compute(&mut self) -> Option<TrayState> {
        if self.devices.is_empty() && !self.primed {
            return None;
        }
        if !self.devices.is_empty() {
            self.primed = true;
        }
        let mut online: Vec<(&String, &DeviceLite)> = self
            .order
            .iter()
            .filter_map(|k| self.devices.get(k).map(|d| (k, d)))
            .filter(|(_, d)| d.online)
            .collect();
        let payload = if online.is_empty() {
            TrayState {
                level: None,
                charging: false,
                online: false,
                kind: String::new(),
            }
        } else {
            // 稳定：order[0] 起步，严格更小才替换（与前端一致）
            let mut target = online.remove(0).1;
            let mut best = target.level.unwrap_or(i64::MAX);
            for (_, d) in online {
                let v = d.level.unwrap_or(i64::MAX);
                if v < best {
                    best = v;
                    target = d;
                }
            }
            TrayState {
                level: target.level,
                charging: target.charging,
                online: true,
                kind: target.kind.clone(),
            }
        };
        let json = serde_json::to_string(&serde_json::json!({
            "level": payload.level,
            "charging": payload.charging,
            "online": payload.online,
            "kind": payload.kind,
        }))
        .unwrap_or_default();
        if self.last_payload.as_deref() == Some(json.as_str()) {
            return None;
        }
        self.last_payload = Some(json);
        Some(payload)
    }
}

/// 处理一条 collector 事件；只在 hello/device/gone/tick 时触发托盘镜像。
pub fn apply_message(app: &AppHandle, value: &Value) {
    let t = value.get("t").and_then(|v| v.as_str()).unwrap_or("");
    if !matches!(t, "hello" | "device" | "gone" | "tick") {
        return;
    }
    let Some(state) = app.try_state::<AppState>() else {
        return;
    };
    let payload = {
        let mut mirror = state.tray_mirror.lock().unwrap();
        match t {
            "hello" => {
                mirror.reset();
                if let Some(arr) = value.get("devices").and_then(|v| v.as_array()) {
                    mirror.upsert_list(arr);
                }
            }
            "device" => {
                if let Some(d) = value.get("device") {
                    mirror.upsert(d);
                }
            }
            "gone" => {
                if let Some(key) = value.get("key").and_then(|v| v.as_str()) {
                    mirror.remove(key);
                }
            }
            "tick" => {
                if let Some(arr) = value.get("devices").and_then(|v| v.as_array()) {
                    mirror.apply_tick(arr);
                }
            }
            _ => return,
        }
        mirror.compute()
    };
    if let Some(payload) = payload {
        state.update_tray_from_devices(app, payload, "rust");
    }
}

#[cfg(test)]
mod tests {
    use super::TrayMirror;
    use serde_json::json;

    fn dev(key: &str, level: Option<i64>, online: bool, charging: bool, kind: &str) -> serde_json::Value {
        json!({
            "key": key,
            "name": key,
            "level": level,
            "charging": charging,
            "online": online,
            "kind": kind,
        })
    }

    #[test]
    fn picks_lowest_online_level() {
        let mut m = TrayMirror::default();
        m.upsert(&dev("a", Some(68), true, false, "mouse"));
        m.upsert(&dev("b", Some(12), true, false, "headset"));
        m.upsert(&dev("c", Some(43), true, false, "bluetooth"));
        let p = m.compute().expect("payload");
        assert_eq!(p.level, Some(12));
        assert_eq!(p.kind, "headset");
        // 12% 充电后仍是同一 target，但 charging 变化要触发更新
        m.upsert(&dev("b", Some(12), true, true, "headset"));
        let p = m.compute().expect("payload");
        assert!(p.charging);
    }

    #[test]
    fn null_level_counts_as_infinity_and_can_be_selected() {
        let mut m = TrayMirror::default();
        m.upsert(&dev("a", None, true, false, "gamepad"));
        m.upsert(&dev("b", None, true, false, "keyboard"));
        let p = m.compute().expect("payload");
        assert_eq!(p.level, None);
        assert_eq!(p.kind, "gamepad"); // order[0]
        m.upsert(&dev("c", Some(50), true, false, "mouse"));
        let p = m.compute().expect("payload");
        assert_eq!(p.level, Some(50));
    }

    #[test]
    fn all_offline_reports_offline() {
        let mut m = TrayMirror::default();
        m.upsert(&dev("a", Some(10), false, false, "mouse"));
        let p = m.compute().expect("payload");
        assert!(!p.online);
        assert_eq!(p.level, None);
    }

    #[test]
    fn does_not_emit_before_first_device() {
        let mut m = TrayMirror::default();
        assert!(m.compute().is_none());
        m.upsert(&dev("a", Some(80), true, false, "mouse"));
        assert!(m.compute().is_some());
        assert!(m.compute().is_none()); // 去重
    }

    #[test]
    fn tick_is_full_alignment_and_keeps_first_seen_order() {
        let mut m = TrayMirror::default();
        m.upsert(&dev("a", Some(30), true, false, "mouse"));
        m.upsert(&dev("b", Some(90), true, false, "headset"));
        assert!(m.compute().is_some());
        // tick 删除 b，新增 c（level 20）→ target 变 c
        m.apply_tick(&[
            dev("a", Some(30), true, false, "mouse"),
            dev("c", Some(20), true, false, "keyboard"),
        ]);
        let p = m.compute().expect("payload");
        assert_eq!(p.level, Some(20));
        assert_eq!(p.kind, "keyboard");
        // 再 tick 删除 a/c → primed 后报离线
        m.apply_tick(&[]);
        let p = m.compute().expect("payload");
        assert!(!p.online);
    }
}

