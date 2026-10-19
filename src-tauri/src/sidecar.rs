//! sidecar 管理：`collector.exe` / Python collector 的启动、stdio 行协议、Job Object、
//! stderr 日志轮转、残留清理、退出握手（协议 §1/§2/§5）。
//!
//! 不依赖 tauri-plugin-shell：进程生命周期要自己控制（Job Object / TerminateProcess /
//! 残留 PID 校验），插件给不了这些控制点。

use std::env;
use std::fs;
use std::io;
use std::io::{BufRead, BufReader, Write};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStderr, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};

use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
use windows_sys::Win32::System::Threading::{
    GetExitCodeProcess, OpenProcess, WaitForSingleObject, CREATE_NO_WINDOW, INFINITE,
    PROCESS_QUERY_LIMITED_INFORMATION,
};

use crate::logging::{MAX_LOG_FILES, MAX_LOG_SIZE};
use crate::paths::Paths;
use crate::platform::{self, JobHandle};

const MAX_LINE: usize = 1024 * 1024;
const SYNCHRONIZE_ACCESS: u32 = 0x0010_0000;

// ---------------------------------------------------------------------------
// 自动重启策略（review P1-13）
// ---------------------------------------------------------------------------

/// 自动重启默认上限（PB_COLLECTOR_RESTART_MAX 可覆盖）。
pub const DEFAULT_RESTART_MAX: u32 = 5;
/// 指数退避：1s → 3s → 10s，之后封顶 10s（最多 5 次）。
const RESTART_BACKOFF_MS: [u64; 3] = [1_000, 3_000, 10_000];
/// 环境变量覆盖值的合理上限（防止误配置成无限重试）。
const RESTART_MAX_CAP: u32 = 50;

/// 第 `attempt` 次（从 1 开始）自动重启前的等待毫秒数；超出序列后封顶 10s。
pub fn restart_backoff_ms(attempt: u32) -> u64 {
    match attempt {
        0 | 1 => RESTART_BACKOFF_MS[0],
        2 => RESTART_BACKOFF_MS[1],
        _ => RESTART_BACKOFF_MS[2],
    }
}

/// 解析 `PB_COLLECTOR_RESTART_MAX`（纯函数，便于单测）：非法/缺失回落默认，0 = 禁用。
pub fn parse_restart_max(value: Option<&str>) -> u32 {
    match value.map(str::trim) {
        Some(v) if !v.is_empty() => match v.parse::<u32>() {
            Ok(n) => n.min(RESTART_MAX_CAP),
            Err(_) => DEFAULT_RESTART_MAX,
        },
        _ => DEFAULT_RESTART_MAX,
    }
}

fn restart_max_from_env() -> u32 {
    parse_restart_max(env::var("PB_COLLECTOR_RESTART_MAX").ok().as_deref())
}

/// 自动重启状态机输出。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RestartDecision {
    /// shutdown / 退出流程触发的预期退出：不重启。
    NoRestart,
    /// 第 `attempt` 次自动重启，等待 `delay_ms` 后重新 spawn。
    Restart { attempt: u32, delay_ms: u64 },
    /// 重试用尽：停止自动重启，保留手动重启入口。
    Exhausted { attempts: u32 },
}

/// 退出后的重启决策（纯函数）：
/// - 只有**非 shutdown 路径**的退出才自动重启（含 code 0 / code None）；
/// - `attempt_before` 是本进程已自动重启的次数，达到 `max` 即 Exhausted；
/// - `max == 0` 表示显式禁用自动重启。
pub fn plan_restart(shutdown_requested: bool, attempt_before: u32, max: u32) -> RestartDecision {
    if shutdown_requested {
        return RestartDecision::NoRestart;
    }
    if max == 0 || attempt_before >= max {
        return RestartDecision::Exhausted {
            attempts: attempt_before,
        };
    }
    let attempt = attempt_before + 1;
    RestartDecision::Restart {
        attempt,
        delay_ms: restart_backoff_ms(attempt),
    }
}

/// 健康存活阈值（review P1-13 运行验收修正）：
/// 采集器存活 ≥ 该时长后退出，视为“跑过一段健康时间”，清零自动重启预算；
/// **不能靠 hello 清零**——坏驱动/坏配置可能每次都能完成握手，但 1–2s 内崩溃，
/// 那样退避会永远停在 1s、永远用不尽预算，形成无限 1s 重启循环。
pub const HEALTHY_UPTIME: Duration = Duration::from_secs(60);

/// 自动重启预算（纯状态机，单测覆盖）：
/// - 非 shutdown 且 `uptime < HEALTHY_UPTIME` → 视为 crash-loop，attempt 逐次 +1；
/// - 非 shutdown 且 `uptime >= HEALTHY_UPTIME` → 健康运行过，预算清零并从 attempt=1 重新退避；
/// - shutdown / 退出流程 → 不重启（预算不变）；
/// - 手动“重启采集器” → 用户显式要求重来，预算清零。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct RestartBudget {
    /// 当前 crash-loop 已消耗的自动重启次数。
    pub attempt: u32,
}

impl RestartBudget {
    /// 进程退出后的预算迁移：返回 `(新预算, 决策, 是否因健康存活而清零)`。
    pub fn on_exit(
        &self,
        shutdown_requested: bool,
        uptime: Option<Duration>,
        max: u32,
    ) -> (RestartBudget, RestartDecision, bool) {
        let healthy = !shutdown_requested
            && uptime
                .map(|u| u >= HEALTHY_UPTIME)
                .unwrap_or(false);
        let attempt_before = if healthy { 0 } else { self.attempt };
        let decision = plan_restart(shutdown_requested, attempt_before, max);
        let next = match decision {
            RestartDecision::Restart { attempt, .. } => RestartBudget { attempt },
            RestartDecision::Exhausted { attempts } => RestartBudget { attempt: attempts },
            RestartDecision::NoRestart => RestartBudget {
                attempt: attempt_before,
            },
        };
        (next, decision, healthy)
    }

    /// 手动重启：用户显式要求重来，预算清零（下一次崩溃重新从 1s 退避开始）。
    pub fn manual_restart(self) -> RestartBudget {
        RestartBudget::default()
    }
}

// ---------------------------------------------------------------------------
// 状态（序列化后即 collector-state 事件 payload，协议 §4.1）
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct CollectorState {
    pub state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub protocol: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session: Option<String>,
    /// 自动重启信息（review P1-13）：第几次自动重启 / 上限，前端可展示“第 n/m 次重试”。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub restart_attempt: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub restart_max: Option<u32>,
}

impl Default for CollectorState {
    fn default() -> Self {
        Self {
            state: "starting".to_string(),
            pid: None,
            code: None,
            message: None,
            protocol: None,
            session: None,
            restart_attempt: None,
            restart_max: None,
        }
    }
}

impl CollectorState {
    pub fn error(message: impl Into<String>) -> Self {
        Self {
            state: "error".to_string(),
            message: Some(message.into()),
            ..Default::default()
        }
    }
}

// ---------------------------------------------------------------------------
// 行读取（≤ max，超限丢弃并重新同步）
// ---------------------------------------------------------------------------

enum LineRead {
    Line,
    TooLong,
    Eof,
}

fn read_line_limited<R: BufRead>(
    reader: &mut R,
    out: &mut Vec<u8>,
    max: usize,
) -> io::Result<LineRead> {
    out.clear();
    let mut overflow = false;
    loop {
        let buf = match reader.fill_buf() {
            Ok([]) => return Ok(LineRead::Eof),
            Ok(b) => b,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        };
        match buf.iter().position(|&b| b == b'\n') {
            Some(idx) => {
                let take = idx + 1;
                if !overflow {
                    if out.len() + take > max {
                        overflow = true;
                    } else {
                        out.extend_from_slice(&buf[..take]);
                    }
                }
                reader.consume(take);
                if overflow {
                    return Ok(LineRead::TooLong);
                }
                while matches!(out.last(), Some(b'\n') | Some(b'\r')) {
                    out.pop();
                }
                return Ok(LineRead::Line);
            }
            None => {
                let take = buf.len();
                if !overflow {
                    if out.len() + take > max {
                        overflow = true;
                    } else {
                        out.extend_from_slice(buf);
                    }
                }
                reader.consume(take);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// 启动命令解析
// ---------------------------------------------------------------------------

struct CmdSpec {
    program: PathBuf,
    args: Vec<String>,
    cwd: PathBuf,
    kind: &'static str,
}

fn split_command_line(s: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut quote: Option<char> = None;
    let mut has = false;
    for c in s.chars() {
        match c {
            '"' | '\'' if quote.is_none() => {
                quote = Some(c);
                has = true;
            }
            '"' if quote == Some('"') => quote = None,
            '\'' if quote == Some('\'') => quote = None,
            c if c.is_whitespace() && quote.is_none() => {
                if has || !cur.is_empty() {
                    out.push(std::mem::take(&mut cur));
                    has = false;
                }
            }
            c => {
                cur.push(c);
                has = true;
            }
        }
    }
    if has || !cur.is_empty() {
        out.push(cur);
    }
    out
}

/// 规范化 Windows 路径用于比较：
/// - `/` → `\`
/// - 去掉 `\\?\` / `\??\` verbatim 前缀（resource_dir() 来自 current_exe()，会带 `\\?\`，
///   而 QueryFullProcessImageNameW 不带，审计 P3-1）
/// - `\\?\UNC\server\share` → `\\server\share`
/// - 大小写不敏感、去尾部反斜杠
fn normalize_path(p: &str) -> String {
    let mut s = p.replace('/', "\\");
    let lower = s.to_ascii_lowercase();
    if lower.starts_with(r"\\?\unc\") {
        s = format!(r"\\{}", &s[8..]);
    } else if s.starts_with(r"\\?\") || s.starts_with(r"\??\") {
        s = s[4..].to_string();
    }
    s.trim_end_matches('\\').to_ascii_lowercase()
}

/// 出站命令校验（协议 §0：单行 ≤ 1MiB；防换行注入多条 NDJSON 命令）。
fn validate_outgoing(line: &str) -> Result<&str, String> {
    let trimmed = line.trim_end_matches(['\r', '\n']);
    if trimmed.trim().is_empty() {
        return Err("空行".to_string());
    }
    if trimmed.len() > MAX_LINE {
        return Err(format!("长度 {} 超过 1MiB 上限", trimmed.len()));
    }
    if trimmed.contains('\n') || trimmed.contains('\r') {
        return Err("包含内嵌换行符（可能注入多条命令）".to_string());
    }
    Ok(trimmed)
}

// ---------------------------------------------------------------------------
// PID 文件（残留清理，review P1-06）
//
// 旧实现：读到共享的 collector.pid.json 就直接 kill，并按安装路径全盘扫描同名
// collector.exe 再 kill —— 第二个实例启动会误杀第一个健康实例的 collector。
// 现在 pid 记录绑定「哪个 app 实例 / 哪个数据目录 / 哪个 exe」，清理前先做归属判定：
// - data_dir 与本实例一致；
// - exe 属于本安装 / 本 collector；
// - 记录所属的 app 实例已退出（不是另一个活实例）；
// 三条同时满足才 kill，否则最多只删除 pid 文件。
// 不再按安装路径全盘 kill：Job Object 的 KILL_ON_JOB_CLOSE 已能带走 app 的 collector 级联。
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PidRecord {
    pub pid: u32,
    #[serde(default)]
    pub creation: u64,
    /// 写入该记录的 app 进程 pid：用于判断记录所属 app 实例是否仍存活。
    #[serde(default)]
    pub app_pid: u32,
    /// app 实例随机 token：区分「本进程上一次 start 的残留」与「上一个已退出实例的残留」。
    #[serde(default)]
    pub instance: String,
    /// app 数据目录（归属判定的第一道门槛；并行测试/多安装副本据此隔离）。
    #[serde(default)]
    pub data_dir: String,
    /// 采集器可执行文件全路径（spawn 后由 process_info 读取）。
    #[serde(default)]
    pub exe: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub started_unix_ms: u64,
}

/// 清理时对「记录里的 pid 当前对应进程」的只读观测值（与 Windows API 解耦，便于单测）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ObservedProcess {
    pub creation: u64,
    pub exe: String,
}

/// 归属判定上下文（纯数据）。
pub struct ResidualContext<'a> {
    /// 本实例数据目录（原始路径，比较时 normalize）。
    pub data_dir: &'a str,
    /// 本实例计划 spawn 的 collector exe（原始路径）；None = 无法确定。
    pub expected_exe: Option<&'a str>,
    /// 本 app 进程 pid。
    pub app_pid: u32,
    /// 本 app 实例随机 token。
    pub instance: &'a str,
    /// 记录中的 app_pid 当前是否仍存活（调用方探测；app_pid==本进程时恒 true）。
    pub recorded_app_alive: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ResidualAction {
    /// 无 pid 文件（或读不到）：什么都不做。
    None,
    /// 仅删除 pid 文件，不 kill（字段缺失/不匹配/进程已退出/PID 复用/外来 exe）。
    DeleteFileOnly { reason: &'static str },
    /// 记录所属 app 实例仍存活（且不是本进程）：另一个活实例拥有该 collector，
    /// 不 kill，也不删除它的 pid 记录。
    LeaveAlone { app_pid: u32 },
    /// 归属本安装、属于已退出实例的残留 → kill；`same_run` = 本进程上一次 start 的残留。
    Kill { same_run: bool },
}

/// 归属判定（纯函数，review P1-06 单测覆盖）：
/// 只有「data_dir 一致 + exe 属于本 collector + 记录 app 已退出 + creation 匹配」才 Kill。
pub fn classify_residual(
    ctx: &ResidualContext<'_>,
    record: Option<&PidRecord>,
    observed: Option<&ObservedProcess>,
) -> ResidualAction {
    let Some(rec) = record else {
        return ResidualAction::None;
    };
    if rec.pid == 0 {
        return ResidualAction::DeleteFileOnly {
            reason: "invalid pid",
        };
    }
    // 旧版本记录没有 data_dir 字段：无法证明归属，只删文件不 kill。
    if rec.data_dir.trim().is_empty() {
        return ResidualAction::DeleteFileOnly {
            reason: "missing data_dir (legacy record)",
        };
    }
    if normalize_path(&rec.data_dir) != normalize_path(ctx.data_dir) {
        return ResidualAction::DeleteFileOnly {
            reason: "foreign data_dir",
        };
    }
    // 另一个活实例仍持有该 collector：禁止 kill（这正是 P1-06 的误杀场景）。
    if rec.app_pid != 0 && rec.app_pid != ctx.app_pid && ctx.recorded_app_alive {
        return ResidualAction::LeaveAlone {
            app_pid: rec.app_pid,
        };
    }
    let Some(obs) = observed else {
        return ResidualAction::DeleteFileOnly {
            reason: "collector not running",
        };
    };
    // PID 复用：creation 不匹配 → 记录里的 pid 已不是当初的采集器。
    if rec.creation != 0 && obs.creation != 0 && rec.creation != obs.creation {
        return ResidualAction::DeleteFileOnly {
            reason: "pid reused (creation mismatch)",
        };
    }
    let Some(expected) = ctx.expected_exe else {
        return ResidualAction::DeleteFileOnly {
            reason: "expected collector exe unknown",
        };
    };
    let want = normalize_path(expected);
    let observed_exe = normalize_path(&obs.exe);
    let recorded_exe_ok = rec.exe.is_empty() || normalize_path(&rec.exe) == want;
    if observed_exe != want || !recorded_exe_ok {
        return ResidualAction::DeleteFileOnly {
            reason: "exe mismatch (foreign install)",
        };
    }
    // app_pid=0 = 无法证明记录所属 app 已退出 → 保守不 kill。
    if rec.app_pid == 0 {
        return ResidualAction::DeleteFileOnly {
            reason: "unknown owner app (legacy record)",
        };
    }
    // instance token 相同 = 本进程上一次 start 的残留（owner=current-run）；
    // 不同 = 上一个已退出实例的残留（owner=previous-run）。只影响日志/统计口径，都安全可 kill。
    let same_run = rec.app_pid == ctx.app_pid
        && (rec.instance.is_empty()
            || ctx.instance.is_empty()
            || rec.instance == ctx.instance);
    ResidualAction::Kill { same_run }
}

fn unix_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// app 实例随机 token：pid + 时间纳秒 + 栈地址（ASLR）混合，避免同一毫秒/pid 复用撞车。
fn new_instance_token() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let stack = &nanos as *const u128 as usize;
    format!("{:x}-{nanos:x}-{stack:x}", std::process::id())
}

// ---------------------------------------------------------------------------
// Sidecar
// ---------------------------------------------------------------------------

struct Inner {
    child: Option<Child>,
    stdin: Option<Arc<Mutex<ChildStdin>>>,
    job: Option<JobHandle>,
    pid: Option<u32>,
    state: CollectorState,
    hello: bool,
    generation: u64,
    /// 退出事件只发布一次（wait 线程与 shutdown terminate 路径会并发到达）
    exit_published: bool,
    /// 是否处于 app 主动 shutdown/退出流程：true 时进程退出属于预期退出，不自动重启。
    shutdown_requested: bool,
    /// 自动重启预算（crash-loop 计数；健康存活 ≥60s 或手动重启后清零，review P1-13 修正）。
    restart_budget: RestartBudget,
    /// 本轮 spawn 的即时时刻：退出时计算 uptime，决定是否属于 crash-loop。
    spawned_at: Option<Instant>,
    /// 自动重启定时器代际：调度/取消时 +1，旧定时器醒来后据此放弃（防并发双 spawn）。
    restart_token: u64,
}

pub struct Sidecar {
    app: AppHandle,
    paths: Paths,
    inner: Mutex<Inner>,
    generation: AtomicU64,
    /// 本 app 进程的随机实例 token（写入 pid 记录，用于归属判定/日志）。
    instance: String,
    /// 自动重启上限（PB_COLLECTOR_RESTART_MAX 覆盖，0 = 禁用）。
    restart_max: u32,
    /// 串行化所有 spawn 请求（首次启动 / 手动重启 / 自动重启），防并发双 spawn。
    spawn_lock: Mutex<()>,
}

impl Sidecar {
    pub fn new(app: AppHandle, paths: Paths) -> Self {
        Self {
            app,
            paths,
            inner: Mutex::new(Inner {
                child: None,
                stdin: None,
                job: None,
                pid: None,
                state: CollectorState {
                    state: "starting".to_string(),
                    ..Default::default()
                },
                hello: false,
                generation: 0,
                exit_published: false,
                shutdown_requested: false,
                restart_budget: RestartBudget::default(),
                spawned_at: None,
                restart_token: 0,
            }),
            generation: AtomicU64::new(0),
            instance: new_instance_token(),
            restart_max: restart_max_from_env(),
            spawn_lock: Mutex::new(()),
        }
    }

    pub fn state(&self) -> CollectorState {
        self.inner.lock().unwrap().state.clone()
    }

    fn publish_state(&self, st: CollectorState) {
        {
            let mut inner = self.inner.lock().unwrap();
            inner.state = st.clone();
        }
        let _ = self.app.emit("collector-state", &st);
        crate::log_info!(
            "collector-state state={} pid={:?} code={:?} protocol={:?} session={:?} message={:?}",
            st.state,
            st.pid,
            st.code,
            st.protocol,
            st.session,
            st.message
        );
    }

    // -- 启动 --------------------------------------------------------------

    /// 采集器不可用时立即把托盘切成离线（review P1-13）：托盘是隐藏态唯一环境指示器，
    /// 不能停在崩溃前的旧电量上。异常退出 / 启动失败都会走这里。
    fn publish_offline_tray(&self, reason: &str) {
        crate::tray_mirror::publish_collector_offline(&self.app, reason);
    }

    /// 公共入口：序列化所有 spawn 请求（首次启动 / 手动重启 / 自动重启），防并发双 spawn。
    pub fn start(self: &Arc<Self>) -> bool {
        let guard = self
            .spawn_lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let ok = self.start_locked();
        drop(guard);
        ok
    }

    /// 已持有 `spawn_lock` 的启动实现（自动重启在锁内做代际校验后直接调用，避免检查-启动竞态）。
    fn start_locked(self: &Arc<Self>) -> bool {
        let attempt = { self.inner.lock().unwrap().restart_budget.attempt };
        crate::log_info!(
            "sidecar start requested instance={} attempt={attempt} restart_max={} data_dir={}",
            self.instance,
            self.restart_max,
            self.paths.data_dir.display()
        );
        // 先 shutdown 清理：该调用会把 shutdown_requested 置 true 并取消排队中的自动重启；
        // 真正 spawn 成功后会重新置 false（新一轮运行中的非正常退出仍需自动重启）。
        self.shutdown(Duration::from_millis(300), false);

        let spec = match self.resolve_command() {
            Ok(s) => s,
            Err(msg) => {
                crate::log_error!("sidecar spawn aborted: {msg}");
                self.publish_offline_tray("spawn aborted");
                self.publish_state(CollectorState::error(msg));
                return false;
            }
        };
        // 残留清理必须在 resolve 之后：用本次真实要 spawn 的 exe 做归属判定的 exe 基准。
        self.cleanup_residual(&spec);
        crate::log_info!(
            "sidecar spawn path={} args={:?} cwd={} kind={} attempt={attempt}",
            spec.program.display(),
            spec.args,
            spec.cwd.display(),
            spec.kind
        );

        let mut cmd = Command::new(&spec.program);
        cmd.args(&spec.args)
            .current_dir(&spec.cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .creation_flags(CREATE_NO_WINDOW);
        // 只在用户未设置时补默认值，绝不覆盖已有环境变量。
        if env::var_os("PYTHONIOENCODING").is_none() {
            cmd.env("PYTHONIOENCODING", "utf-8");
        }
        if env::var_os("PYTHONUNBUFFERED").is_none() {
            cmd.env("PYTHONUNBUFFERED", "1");
        }

        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                let msg = format!("启动采集器失败: {e}");
                crate::log_error!("{msg} (path={})", spec.program.display());
                self.publish_offline_tray("spawn failed");
                self.publish_state(CollectorState::error(msg));
                return false;
            }
        };
        let pid = child.id();
        let (Some(stdout), Some(stderr), Some(stdin)) =
            (child.stdout.take(), child.stderr.take(), child.stdin.take())
        else {
            crate::log_error!("sidecar pipe 获取失败 pid={pid}");
            let _ = child.kill();
            self.publish_offline_tray("stdio pipe failed");
            self.publish_state(CollectorState::error("无法建立采集器 stdio 管道"));
            return false;
        };

        let job = JobHandle::create();
        let assigned = match &job {
            Some(j) => {
                let ok = j.assign(child.as_raw_handle() as HANDLE);
                if !ok {
                    crate::log_warn!(
                        "AssignProcessToJobObject 失败 pid={pid} err={}",
                        std::io::Error::last_os_error()
                    );
                }
                ok
            }
            None => {
                crate::log_warn!("CreateJobObject 失败，sidecar 无法保证随 app 退出");
                false
            }
        };
        crate::log_info!("sidecar job assigned={assigned}");

        let gen = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
        {
            let mut inner = self.inner.lock().unwrap();
            inner.child = Some(child);
            inner.stdin = Some(Arc::new(Mutex::new(stdin)));
            inner.job = job;
            inner.pid = Some(pid);
            inner.hello = false;
            inner.generation = gen;
            inner.exit_published = false;
            // 新一轮运行开始：记录 spawn 时刻（退出时算 uptime），此后的退出重新按“非正常退出”判定。
            inner.spawned_at = Some(Instant::now());
            inner.shutdown_requested = false;
        }

        self.write_pid_file(pid, spec.kind);
        self.publish_state(CollectorState {
            state: "starting".to_string(),
            pid: Some(pid),
            restart_attempt: (attempt > 0).then_some(attempt),
            restart_max: (attempt > 0).then_some(self.restart_max),
            ..Default::default()
        });
        crate::log_info!(
            "sidecar spawned pid={pid} instance={} attempt={attempt}",
            self.instance
        );

        let me = self.clone();
        let _ = std::thread::Builder::new()
            .name("collector-stdout".into())
            .spawn(move || me.stdout_loop(stdout, gen));
        let me = self.clone();
        let _ = std::thread::Builder::new()
            .name("collector-stderr".into())
            .spawn(move || me.stderr_loop(stderr));
        let me = self.clone();
        let _ = std::thread::Builder::new()
            .name("collector-wait".into())
            .spawn(move || me.wait_loop(pid, gen));
        let me = self.clone();
        let _ = std::thread::Builder::new()
            .name("collector-hello-timeout".into())
            .spawn(move || me.hello_timeout(pid, gen));
        true
    }

    /// 手动重启：重置自动重启预算（用户显式要求重来），并取消排队中的自动重启定时器。
    pub fn restart(self: &Arc<Self>) -> bool {
        let me = self.clone();
        std::thread::Builder::new()
            .name("collector-restart".into())
            .spawn(move || {
                crate::log_info!("collector restart requested (manual)");
                {
                    let mut inner = me.inner.lock().unwrap();
                    // 手动重启 = 用户显式要求重来：预算清零（下一次崩溃重新从 1s 退避开始）。
                    inner.restart_budget = inner.restart_budget.manual_restart();
                    inner.restart_token = inner.restart_token.wrapping_add(1);
                }
                me.shutdown(Duration::from_secs(2), true);
                me.start();
            })
            .is_ok()
    }

    // -- stdio -------------------------------------------------------------

    pub fn send_line(&self, line: &str) -> bool {
        let trimmed = match validate_outgoing(line) {
            Ok(t) => t,
            Err(reason) => {
                crate::log_warn!("collector 命令被拒绝（{reason}），长度={}", line.len());
                return false;
            }
        };
        let stdin = { self.inner.lock().unwrap().stdin.clone() };
        let Some(stdin) = stdin else {
            crate::log_warn!("collector stdin 不可用，丢弃命令: {trimmed}");
            return false;
        };
        let mut guard = match stdin.lock() {
            Ok(g) => g,
            Err(poisoned) => poisoned.into_inner(),
        };
        let ok = guard.write_all(trimmed.as_bytes()).is_ok()
            && guard.write_all(b"\n").is_ok()
            && guard.flush().is_ok();
        if ok {
            crate::log_debug!("collector << {trimmed}");
        } else {
            crate::log_warn!("collector stdin 写入失败: {trimmed}");
        }
        ok
    }

    pub fn send_command(&self, value: Value) -> bool {
        self.send_line(&value.to_string())
    }

    pub fn refresh(&self) -> bool {
        self.send_command(json!({ "t": "refresh" }))
    }

    /// 让 collector 立即回一个全量 tick（显示面板前补齐隐藏期间的数据）。
    pub fn request_snapshot(&self) -> bool {
        self.send_command(json!({ "t": "get", "what": "snapshot" }))
    }

    fn stdout_loop(self: Arc<Self>, stdout: ChildStdout, gen: u64) {
        let mut reader = BufReader::with_capacity(64 * 1024, stdout);
        let mut buf = Vec::with_capacity(8192);
        loop {
            match read_line_limited(&mut reader, &mut buf, MAX_LINE) {
                Ok(LineRead::Line) => {
                    if buf.is_empty() {
                        continue;
                    }
                    let text = String::from_utf8_lossy(&buf).to_string();
                    self.on_line(&text, gen);
                }
                Ok(LineRead::TooLong) => {
                    crate::log_warn!("collector 单行超过 1MiB，已丢弃并重新同步");
                }
                Ok(LineRead::Eof) => {
                    crate::log_info!("collector stdout EOF");
                    break;
                }
                Err(e) => {
                    crate::log_warn!("collector stdout 读取失败: {e}");
                    break;
                }
            }
        }
    }

    fn stderr_loop(self: Arc<Self>, stderr: ChildStderr) {
        let mut file = match crate::logging::RotatingFile::open(
            self.paths.collector_log.clone(),
            MAX_LOG_SIZE,
            MAX_LOG_FILES,
        ) {
            Ok(f) => f,
            Err(e) => {
                crate::log_warn!("collector.log 打开失败({e})，stderr 仅排空不入库");
                let mut sink = io::sink();
                let mut reader = BufReader::new(stderr);
                let _ = io::copy(&mut reader, &mut sink);
                return;
            }
        };
        let mut reader = BufReader::with_capacity(64 * 1024, stderr);
        let mut buf = Vec::with_capacity(8192);
        loop {
            match read_line_limited(&mut reader, &mut buf, 256 * 1024) {
                Ok(LineRead::Line) => {
                    if !buf.is_empty() {
                        let text = String::from_utf8_lossy(&buf);
                        file.write_line(text.trim_end());
                    }
                }
                Ok(LineRead::TooLong) => file.write_line("[collector stderr 单行过长，已截断]"),
                Ok(LineRead::Eof) => break,
                Err(_) => break,
            }
        }
        crate::log_info!("collector stderr EOF");
    }

    fn on_line(&self, line: &str, gen: u64) {
        let line = line.trim();
        if line.is_empty() {
            return;
        }
        // 已退出/已被新一轮 spawn 取代的采集器的残留输出：丢弃。
        // 否则崩溃瞬间仍在管道里的 device/tick 会把刚发布的 offline 托盘又翻回在线（P1-13）。
        let (current_gen, exited) = {
            let inner = self.inner.lock().unwrap();
            (inner.generation, inner.exit_published)
        };
        if current_gen != gen || exited {
            crate::log_debug!(
                "stale collector line ignored gen={gen} current={current_gen} exited={exited}: {}",
                truncate(line, 160)
            );
            return;
        }
        let _ = self.app.emit("collector-message", line.to_string());
        let parsed: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(e) => {
                let preview: String = line.chars().take(200).collect();
                crate::log_warn!("collector 输出非 JSON: {e}: {preview}");
                return;
            }
        };
        // Rust 侧托盘镜像：TrySuspend 冻结 WebView 后，隐藏态仍需正确更新托盘。
        crate::tray_mirror::apply_message(&self.app, &parsed);
        match parsed.get("t").and_then(|v| v.as_str()).unwrap_or("") {
            "hello" => self.handle_hello(&parsed),
            "alert" => crate::log_info!(
                "collector alert kind={:?} key={:?} level={:?}",
                parsed.get("kind"),
                parsed.get("key"),
                parsed.get("level")
            ),
            "stat" => {
                // F7：providers 字典很长，原先整行截断 400 字符导致 timers_per_s /
                // key_migrations 永远落在截断之后。改为先打一行关键字段摘要（info 级，
                // 默认日志级别可见），原始行只在 debug 级保留。
                crate::log_info!("collector stat: {}", format_stat_summary(&parsed));
                crate::log_debug!("collector stat raw: {}", truncate(line, 400));
            }
            "gone" => crate::log_debug!(
                "collector gone key={:?}",
                parsed.get("key").and_then(|v| v.as_str())
            ),
            "device" => crate::log_debug!(
                "collector device key={:?}",
                parsed
                    .get("device")
                    .and_then(|d| d.get("key"))
                    .and_then(|v| v.as_str())
            ),
            _ => {}
        }
    }

    fn handle_hello(&self, v: &Value) {
        let protocol = v.get("protocol").and_then(|x| x.as_u64()).unwrap_or(0);
        let session = v
            .get("session")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        let build = v
            .get("build")
            .and_then(|x| x.as_str())
            .unwrap_or("unknown")
            .to_string();
        let devices = v
            .get("devices")
            .and_then(|x| x.as_array())
            .map(|a| a.len())
            .unwrap_or(0);
        let caps = v
            .get("capabilities")
            .and_then(|x| x.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_str())
                    .collect::<Vec<_>>()
                    .join(",")
            })
            .unwrap_or_default();
        let pid = self.state().pid;
        if protocol != 1 {
            crate::log_warn!(
                "hello protocol mismatch got={protocol} expected=1 session={session} build={build}"
            );
            self.publish_state(CollectorState {
                state: "error".to_string(),
                pid,
                message: Some("采集器版本过期".to_string()),
                protocol: Some(protocol as u32),
                session: Some(session),
                code: None,
                ..Default::default()
            });
            return;
        }
        {
            let mut inner = self.inner.lock().unwrap();
            inner.hello = true;
            // 注意（review P1-13 运行验收修正）：这里**不能**重置自动重启预算。
            // 坏驱动/坏配置的 collector 可能每次都能握手、但 1–2s 内就崩溃，若 hello 清零，
            // 退避永远停在 1s、预算永远用不尽 → 无限重启循环。预算只在 on_process_exit
            // 按 uptime（≥ HEALTHY_UPTIME）重置，或手动重启时清零。
        }
        crate::log_info!(
            "hello protocol={protocol} session={session} build={build} devices={devices} capabilities=[{caps}]"
        );
        self.publish_state(CollectorState {
            state: "ready".to_string(),
            pid,
            protocol: Some(1),
            session: Some(session),
            ..Default::default()
        });
    }

    // -- 退出 --------------------------------------------------------------

    fn wait_loop(self: Arc<Self>, pid: u32, gen: u64) {
        let code = wait_for_process(pid);
        self.on_process_exit(pid, gen, code, "wait");
    }

    fn hello_timeout(self: Arc<Self>, pid: u32, gen: u64) {
        std::thread::sleep(Duration::from_secs(5));
        let (cur_pid, hello, cur_state, cur_gen) = {
            let inner = self.inner.lock().unwrap();
            (
                inner.pid,
                inner.hello,
                inner.state.state.clone(),
                inner.generation,
            )
        };
        if cur_gen == gen && cur_pid == Some(pid) && !hello && cur_state == "starting" {
            crate::log_warn!("hello timeout 5s pid={pid}");
            self.publish_state(CollectorState {
                state: "error".to_string(),
                pid: Some(pid),
                message: Some("采集器 5 秒内未完成握手".to_string()),
                ..Default::default()
            });
        }
    }

    /// 进程退出统一处理（wait 线程 / shutdown / terminate 路径都会到达）。
    ///
    /// review P1-13：
    /// - 非 shutdown 路径的退出（含 code 0 / code None）→ 立即发布 offline 托盘状态并退避自动重启；
    /// - shutdown / 退出流程 → `shutdown_requested=true`，绝不重启；
    /// - 重试用尽 → error“采集器多次退出，已停止自动重启”，保留手动重启；
    /// - 预算按**存活时长**重置：uptime ≥ HEALTHY_UPTIME 才算健康运行过（不能靠 hello 清零）；
    /// - `restart_token` + `generation` 防并发双 spawn。
    fn on_process_exit(self: &Arc<Self>, pid: u32, gen: u64, code: Option<i32>, source: &str) {
        let (expected_exit, budget, spawned_at) = {
            let mut inner = self.inner.lock().unwrap();
            if inner.generation != gen {
                crate::log_debug!(
                    "stale sidecar exit ignored pid={pid} gen={gen} current={}",
                    inner.generation
                );
                return;
            }
            if inner.pid != Some(pid) || inner.exit_published {
                return;
            }
            // 先占位，避免 wait 线程与 shutdown/terminate 路径重复发布 exited。
            inner.exit_published = true;
            (
                inner.shutdown_requested,
                inner.restart_budget,
                inner.spawned_at,
            )
        };
        let uptime = spawned_at.map(|t| t.elapsed());
        let (next_budget, decision, healthy_reset) =
            budget.on_exit(expected_exit, uptime, self.restart_max);
        if healthy_reset {
            // 健康运行过才重置预算（review P1-13 运行验收修正：hello 清零会导致 1s 无限重启）。
            crate::log_info!(
                "auto-restart budget reset (healthy uptime={:?})",
                uptime.unwrap_or_default()
            );
        }

        let (child, job) = {
            let mut inner = self.inner.lock().unwrap();
            let child = inner.child.take();
            inner.stdin.take();
            inner.spawned_at = None;
            let job = inner.job.take();
            (child, job)
        };
        if let Some(mut child) = child {
            let _ = child.wait();
        }
        // 1) 立即发布 offline 托盘状态（先于 collector-state，用户马上看到离线）。
        if !matches!(decision, RestartDecision::NoRestart) {
            self.publish_offline_tray(if code.is_some() {
                "collector exited unexpectedly"
            } else {
                "collector exited without exit code"
            });
        }
        // 2) 发布 exited / error + restart 信息。
        // 先发布 exited / 记录退出码，再做 Job 轮询：app 退出时这两行必须落盘。
        let uptime_ms = uptime.map(|u| u.as_millis() as u64).unwrap_or(0);
        crate::log_info!(
            "sidecar exit(code={code:?}) pid={pid} source={source} expected={expected_exit} restart_attempt={} uptime_ms={uptime_ms} healthy_reset={healthy_reset} code_known={}",
            budget.attempt,
            code.is_some()
        );
        match decision {
            RestartDecision::NoRestart => {
                self.publish_state(CollectorState {
                    state: "exited".to_string(),
                    pid: Some(pid),
                    code,
                    ..Default::default()
                });
            }
            RestartDecision::Restart { attempt, delay_ms } => {
                crate::log_warn!(
                    "auto-restart attempt={attempt}/{} pid={pid} code={code:?} source={source} delay_ms={delay_ms} gen={gen}",
                    self.restart_max
                );
                self.publish_state(CollectorState {
                    state: "exited".to_string(),
                    pid: Some(pid),
                    code,
                    message: Some(format!(
                        "采集器异常退出，{}ms 后自动重启（第 {attempt}/{} 次）",
                        delay_ms, self.restart_max
                    )),
                    restart_attempt: Some(attempt),
                    restart_max: Some(self.restart_max),
                    ..Default::default()
                });
            }
            RestartDecision::Exhausted { attempts } => {
                crate::log_error!(
                    "auto-restart exhausted attempts={attempts} max={} pid={pid} code={code:?} source={source}；已停止自动重启，保留手动重启",
                    self.restart_max
                );
                self.publish_state(CollectorState {
                    state: "error".to_string(),
                    pid: Some(pid),
                    code,
                    message: Some("采集器多次退出，已停止自动重启".to_string()),
                    restart_attempt: Some(attempts),
                    restart_max: Some(self.restart_max),
                    ..Default::default()
                });
            }
        }
        // 3) Job 轮询落盘 + 删除 pid 文件（归属记录已由本实例处理完毕）。
        if let Some(job) = job {
            let mut active = job.active_processes();
            for _ in 0..6 {
                if matches!(active, Some(0) | None) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(30));
                active = job.active_processes();
            }
            crate::log_info!("job object released active_processes={active:?}（应为 0）");
            drop(job);
        }
        // 只有该代际仍是“当前”时才删 pid 文件：若并发的手动重启已经写了新记录，不能误删。
        let still_current = {
            let inner = self.inner.lock().unwrap();
            inner.generation == gen && inner.pid == Some(pid)
        };
        if still_current {
            let _ = fs::remove_file(&self.paths.collector_pid);
        } else {
            crate::log_debug!(
                "sidecar exit pid={pid} gen={gen} 已被新一轮 spawn 取代，保留新 pid 记录"
            );
        }
        // 4) 调度退避重启（pid 文件清理之后；定时器唤醒时校验 token/generation）。
        if let RestartDecision::Restart { attempt, delay_ms } = decision {
            let token = {
                let mut inner = self.inner.lock().unwrap();
                // 极端并发（用户手动重启/退出已接管）时不再调度，避免覆盖手动重启重置的计数。
                if inner.generation != gen || inner.child.is_some() || inner.shutdown_requested {
                    None
                } else {
                    inner.restart_budget = next_budget;
                    inner.restart_token = inner.restart_token.wrapping_add(1);
                    Some(inner.restart_token)
                }
            };
            let Some(token) = token else {
                crate::log_info!(
                    "auto-restart attempt={attempt} not scheduled (sidecar already replaced or shutting down)"
                );
                return;
            };
            let me = self.clone();
            let spawned = std::thread::Builder::new()
                .name(format!("collector-auto-restart-{attempt}"))
                .spawn(move || {
                    std::thread::sleep(Duration::from_millis(delay_ms));
                    me.auto_restart_fire(gen, token, attempt);
                });
            match spawned {
                Ok(_) => crate::log_info!(
                    "auto-restart scheduled attempt={attempt} delay_ms={delay_ms} token={token} gen={gen}"
                ),
                Err(e) => crate::log_error!(
                    "auto-restart thread spawn failed attempt={attempt} delay_ms={delay_ms}: {e}"
                ),
            }
        }
    }

    /// 自动重启定时器唤醒：只有“未 shutdown、token/generation 未变、当前没有 collector”
    /// 才真正 spawn；持有 `spawn_lock` 内完成校验 + 启动，避免与手动重启并发双 spawn。
    fn auto_restart_fire(self: Arc<Self>, gen: u64, token: u64, attempt: u32) {
        let guard = self
            .spawn_lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let reason = {
            let inner = self.inner.lock().unwrap();
            if inner.shutdown_requested {
                Some("shutdown")
            } else if inner.restart_token != token {
                Some("superseded")
            } else if inner.generation != gen {
                Some("generation changed")
            } else if inner.child.is_some() {
                Some("collector already running")
            } else {
                None
            }
        };
        if let Some(reason) = reason {
            crate::log_info!("auto-restart attempt={attempt} cancelled reason={reason}");
            return;
        }
        crate::log_info!("auto-restart spawning attempt={attempt} gen={gen} token={token}");
        let ok = self.start_locked();
        crate::log_info!("auto-restart spawn result={ok} attempt={attempt} gen={gen}");
        drop(guard);
    }

    /// 退出握手：发 shutdown → 等 timeout → 超时 TerminateProcess/TerminateJobObject。
    pub fn shutdown(self: &Arc<Self>, timeout: Duration, send_shutdown: bool) -> bool {
        // 任何 shutdown 调用都视为“预期退出”：取消排队中的自动重启，随后发生的进程退出不重启。
        // （即使当前没有 child——例如崩溃后正处于退避等待——也要取消定时器。）
        {
            let mut inner = self.inner.lock().unwrap();
            inner.shutdown_requested = true;
            inner.restart_token = inner.restart_token.wrapping_add(1);
        }
        let (pid, running) = {
            let inner = self.inner.lock().unwrap();
            (inner.pid, inner.child.is_some())
        };
        if !running {
            return true;
        }
        let Some(pid) = pid else { return true };
        if send_shutdown {
            let sent = self.send_line(&json!({ "t": "shutdown" }).to_string());
            crate::log_info!("shutdown handshake sent={sent} pid={pid}");
        }
        let deadline = Instant::now() + timeout;
        loop {
            let status = {
                let mut inner = self.inner.lock().unwrap();
                match inner.child.as_mut() {
                    Some(c) => match c.try_wait() {
                        Ok(Some(s)) => Some(Some(s)),
                        Ok(None) => Some(None),
                        Err(_) => Some(None),
                    },
                    None => None,
                }
            };
            match status {
                None => {
                    // wait 线程已接管 child：等 exited 状态发布（≤1s），保证日志顺序。
                    let wait_until = Instant::now() + Duration::from_millis(1000);
                    while Instant::now() < wait_until {
                        if self.state().state == "exited" {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(20));
                    }
                    return true;
                }
                Some(Some(st)) => {
                    crate::log_info!("sidecar exited during shutdown pid={pid} code={:?}", st.code());
                    let gen = { self.inner.lock().unwrap().generation };
                    self.on_process_exit(pid, gen, st.code(), "shutdown");
                    return true;
                }
                Some(None) => {}
            }
            if Instant::now() >= deadline {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        crate::log_warn!("shutdown handshake timeout pid={pid} ({timeout:?}), terminate");
        self.terminate(pid);
        let exited = platform::wait_process_exit(pid, 1500);
        crate::log_info!("terminate pid={pid} exited={exited}");
        let gen = { self.inner.lock().unwrap().generation };
        self.on_process_exit(pid, gen, None, "terminate");
        true
    }

    /// 超时路径：优先 TerminateJobObject，一次带走 collector 及其 PowerShell/conhost 子孙，
    /// 而不是只 TerminateProcess(collector) 让子孙变孤儿（audit collector P1）。
    fn terminate(&self, pid: u32) {
        let job = { self.inner.lock().unwrap().job.take() };
        match job {
            Some(job) => {
                let before = job.active_processes();
                job.terminate(1);
                let mut active = job.active_processes();
                for _ in 0..40 {
                    if matches!(active, Some(0) | None) {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(50));
                    active = job.active_processes();
                }
                let terminated = before
                    .unwrap_or(0)
                    .saturating_sub(active.unwrap_or(0));
                crate::log_warn!(
                    "shutdown timeout: TerminateJobObject 终止 {terminated} 个进程（before={before:?} remaining={active:?}）"
                );
                drop(job);
            }
            None => {
                crate::log_warn!(
                    "shutdown timeout: job handle 已被 wait 线程接管，退化为 TerminateProcess pid={pid}"
                );
            }
        }
        // 兜底：collector 自身（理论上已随 Job 终止，但可能不在 Job 中——例如 assign 失败）
        let killed = platform::kill_process(pid);
        crate::log_warn!("shutdown timeout: TerminateProcess pid={pid} ok={killed}");
    }

    // -- 残留清理 / 命令解析 ------------------------------------------------

    fn packaged_collector_exe(&self) -> Option<PathBuf> {
        self.app
            .path()
            .resource_dir()
            .ok()
            .map(|d| d.join("collector").join("collector.exe"))
    }

    fn write_pid_file(&self, pid: u32, kind: &str) {
        let info = platform::process_info(pid);
        let rec = PidRecord {
            pid,
            creation: info.as_ref().map(|i| i.creation).unwrap_or(0),
            app_pid: std::process::id(),
            instance: self.instance.clone(),
            data_dir: self.paths.data_dir.to_string_lossy().to_string(),
            exe: info.map(|i| i.exe).unwrap_or_default(),
            kind: kind.to_string(),
            started_unix_ms: unix_ms(),
        };
        let text = match serde_json::to_string(&rec) {
            Ok(t) => t,
            Err(e) => {
                crate::log_warn!("collector pid 记录序列化失败（仅影响残留清理，不影响运行）: {e}");
                return;
            }
        };
        match fs::write(&self.paths.collector_pid, text) {
            Ok(()) => crate::log_debug!(
                "collector pid file written pid={pid} app_pid={} instance={}",
                rec.app_pid,
                rec.instance
            ),
            // 写失败不 kill 任何进程、不阻塞启动；下次 start 因读不到记录只会跳过清理。
            Err(e) => crate::log_warn!(
                "collector pid 文件写入失败（仅影响残留清理，不影响运行）: {e} path={}",
                self.paths.collector_pid.display()
            ),
        }
    }

    /// 残留清理（review P1-06）：先做归属判定，只有「本数据目录 + 本 collector exe +
    /// 记录所属 app 实例已退出」的残留才 kill；其余情况最多删除 pid 文件。
    ///
    /// 删除旧实现里“按安装路径全盘扫描 collector.exe 再 kill”的逻辑——它会误杀并行
    /// 实例/并行测试的 collector。stdout/job 级联已由 app 自己的 Job Object 负责。
    fn cleanup_residual(&self, spec: &CmdSpec) {
        let expected_exe_owned = spec.program.to_string_lossy().to_string();
        let data_dir_owned = self.paths.data_dir.to_string_lossy().to_string();
        let record = match fs::read_to_string(&self.paths.collector_pid) {
            Ok(text) => match serde_json::from_str::<PidRecord>(&text) {
                Ok(rec) => Some(rec),
                Err(e) => {
                    crate::log_warn!("cleanup residual: pid 文件不可解析（{e}），仅删除文件，不 kill");
                    None
                }
            },
            Err(_) => None,
        };
        let observed = record
            .as_ref()
            .and_then(|r| platform::process_info(r.pid))
            .map(|i| ObservedProcess {
                creation: i.creation,
                exe: i.exe,
            });
        let recorded_app_alive = record
            .as_ref()
            .map(|r| {
                if r.app_pid == 0 {
                    false
                } else if r.app_pid == std::process::id() {
                    true
                } else {
                    platform::process_info(r.app_pid).is_some()
                }
            })
            .unwrap_or(false);
        let ctx = ResidualContext {
            data_dir: &data_dir_owned,
            expected_exe: Some(&expected_exe_owned),
            app_pid: std::process::id(),
            instance: &self.instance,
            recorded_app_alive,
        };
        match classify_residual(&ctx, record.as_ref(), observed.as_ref()) {
            ResidualAction::None => {}
            ResidualAction::DeleteFileOnly { reason } => {
                crate::log_info!(
                    "cleanup residual: 仅删除 pid 文件 reason={reason} path={}",
                    self.paths.collector_pid.display()
                );
                let _ = fs::remove_file(&self.paths.collector_pid);
            }
            ResidualAction::LeaveAlone { app_pid } => {
                crate::log_warn!(
                    "cleanup residual: skip kill（另一个活实例 app_pid={app_pid} 仍持有 collector），保留其 pid 记录 path={}",
                    self.paths.collector_pid.display()
                );
            }
            ResidualAction::Kill { same_run } => {
                if let (Some(rec), Some(obs)) = (record.as_ref(), observed.as_ref()) {
                    crate::log_warn!(
                        "cleanup residual: kill stale collector pid={} exe={} owner={} data_dir={} instance={} creation={}",
                        rec.pid,
                        obs.exe,
                        if same_run { "current-run" } else { "previous-run" },
                        rec.data_dir,
                        rec.instance,
                        rec.creation
                    );
                    if !platform::kill_process(rec.pid) {
                        crate::log_warn!(
                            "cleanup residual: kill pid={} 未确认退出（可能已自行退出）",
                            rec.pid
                        );
                    }
                    let _ = fs::remove_file(&self.paths.collector_pid);
                }
            }
        }

        // 诊断（绝不 kill）：全盘扫描同名 collector.exe 只进 debug 日志，帮助确认“无孤儿”。
        // 正确的旧残留已由上面的 pid 记录一对一处理；本 app 的级联退出由 Job Object 保证。
        let packaged_norm = self
            .packaged_collector_exe()
            .as_ref()
            .map(|p| normalize_path(&p.to_string_lossy()));
        let others = platform::find_processes_by_exe_name("collector.exe");
        if !others.is_empty() {
            let same_install = match packaged_norm {
                Some(want) => others
                    .iter()
                    .filter_map(|pid| platform::process_info(*pid).map(|i| normalize_path(&i.exe) == want))
                    .filter(|m| *m)
                    .count(),
                None => 0,
            };
            crate::log_debug!(
                "cleanup residual diagnostic: other collector.exe pids={others:?} same_install={same_install}（只记录，不 kill）"
            );
        }
    }

    fn resolve_command(&self) -> Result<CmdSpec, String> {
        // 1) PB_COLLECTOR：整条命令
        if let Ok(full) = env::var("PB_COLLECTOR") {
            let full = full.trim();
            if !full.is_empty() {
                let parts = split_command_line(full);
                if parts.is_empty() {
                    return Err("PB_COLLECTOR 为空".to_string());
                }
                let cwd = Paths::project_root()
                    .or_else(|| env::current_dir().ok())
                    .unwrap_or_else(|| PathBuf::from("."));
                return Ok(CmdSpec {
                    program: PathBuf::from(&parts[0]),
                    args: parts[1..].to_vec(),
                    cwd,
                    kind: "env",
                });
            }
        }

        // 2) 打包资源：resource_dir()/collector/collector.exe
        if let Ok(res) = self.app.path().resource_dir() {
            let exe = res.join("collector").join("collector.exe");
            if exe.is_file() {
                let cwd = exe.parent().map(Path::to_path_buf).unwrap_or(res);
                return Ok(CmdSpec {
                    program: exe,
                    args: vec!["--stdio".to_string()],
                    cwd,
                    kind: "packaged",
                });
            }
        }

        // 3) 开发回退：项目根 .venv python -m collector --stdio
        let root = Paths::project_root()
            .ok_or_else(|| "未找到项目根目录（可用 PB_PROJECT_ROOT 覆盖）".to_string())?;
        let entry = root.join("collector").join("__main__.py");
        if !entry.is_file() {
            return Err(format!(
                "未找到采集器：{} 不存在（collector/ 尚未就绪）",
                entry.display()
            ));
        }
        let python = match env::var("PB_PYTHON") {
            Ok(p) if !p.trim().is_empty() => PathBuf::from(p),
            _ => {
                let venv = root.join(".venv").join("Scripts").join("python.exe");
                if venv.is_file() {
                    venv
                } else {
                    let sys = PathBuf::from(
                        r"C:\Users\28482\AppData\Local\Programs\Python\Python312\python.exe",
                    );
                    if sys.is_file() {
                        sys
                    } else {
                        PathBuf::from("python")
                    }
                }
            }
        };
        Ok(CmdSpec {
            program: python,
            args: vec![
                "-m".to_string(),
                "collector".to_string(),
                "--stdio".to_string(),
            ],
            cwd: root,
            kind: "dev",
        })
    }
}

fn wait_for_process(pid: u32) -> Option<i32> {
    unsafe {
        let h = OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE_ACCESS,
            0,
            pid,
        );
        if h.is_null() {
            return None;
        }
        WaitForSingleObject(h, INFINITE);
        let mut code: u32 = 0;
        let ok = GetExitCodeProcess(h, &mut code);
        CloseHandle(h);
        if ok != 0 {
            Some(code as i32)
        } else {
            None
        }
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        s.chars().take(max).collect::<String>()
    }
}

fn fmt_number(v: &Value) -> String {
    if let Some(i) = v.as_i64() {
        i.to_string()
    } else if let Some(f) = v.as_f64() {
        format!("{f}")
    } else {
        "?".to_string()
    }
}

/// `stat` 事件的关键字段摘要（P8 可观测性）：timers_per_s / key_migrations / 最慢 provider。
fn format_stat_summary(v: &Value) -> String {
    let timers = v
        .get("timers_per_s")
        .map(fmt_number)
        .unwrap_or_else(|| "?".to_string());
    let migrations = v
        .get("key_migrations")
        .map(fmt_number)
        .unwrap_or_else(|| "?".to_string());
    let mut count = 0usize;
    let mut slowest_name = "-".to_string();
    let mut slowest_ms = f64::NEG_INFINITY;
    if let Some(providers) = v.get("providers").and_then(|p| p.as_object()) {
        count = providers.len();
        for (name, stats) in providers {
            let ms = stats
                .get("avg_ms")
                .and_then(|x| x.as_f64())
                .or_else(|| stats.get("last_ms").and_then(|x| x.as_f64()));
            if let Some(ms) = ms {
                if ms > slowest_ms {
                    slowest_ms = ms;
                    slowest_name = name.clone();
                }
            }
        }
    }
    let slowest = if slowest_ms.is_finite() {
        format!("{slowest_name}:{slowest_ms:.1}ms")
    } else {
        "-".to_string()
    };
    format!(
        "timers_per_s={timers} key_migrations={migrations} providers={count} slowest={slowest}"
    )
}

#[cfg(test)]
mod tests {
    use super::{
        classify_residual, format_stat_summary, normalize_path, parse_restart_max, plan_restart,
        restart_backoff_ms, validate_outgoing, ObservedProcess, PidRecord, ResidualAction,
        ResidualContext, RestartBudget, RestartDecision, DEFAULT_RESTART_MAX, HEALTHY_UPTIME,
        MAX_LINE,
    };
    use serde_json::json;
    use std::time::Duration;

    // ------------------------------------------------------------------
    // P1-13：退避序列 / 重启状态机
    // ------------------------------------------------------------------

    #[test]
    fn backoff_sequence_is_1s_3s_10s_then_capped() {
        assert_eq!(restart_backoff_ms(1), 1_000);
        assert_eq!(restart_backoff_ms(2), 3_000);
        assert_eq!(restart_backoff_ms(3), 10_000);
        assert_eq!(restart_backoff_ms(4), 10_000);
        assert_eq!(restart_backoff_ms(5), 10_000);
        // 非法输入（0）按第一次处理
        assert_eq!(restart_backoff_ms(0), 1_000);
    }

    #[test]
    fn plan_restart_sequence_then_exhausted() {
        let max = DEFAULT_RESTART_MAX;
        // 第 1..5 次：1s/3s/10s/10s/10s，attempt 递增
        let delays: Vec<u64> = (0..max)
            .map(|before| match plan_restart(false, before, max) {
                RestartDecision::Restart { attempt, delay_ms } => {
                    assert_eq!(attempt, before + 1);
                    delay_ms
                }
                other => panic!("expected Restart, got {other:?}"),
            })
            .collect();
        assert_eq!(delays, vec![1_000, 3_000, 10_000, 10_000, 10_000]);
        // 第 6 次退出（已用满 5 次）→ 停止自动重启
        assert_eq!(
            plan_restart(false, max, max),
            RestartDecision::Exhausted { attempts: max }
        );
    }

    #[test]
    fn plan_restart_respects_shutdown_and_disabled_and_custom_max() {
        // shutdown / 退出流程：绝不重启
        assert_eq!(
            plan_restart(true, 0, DEFAULT_RESTART_MAX),
            RestartDecision::NoRestart
        );
        // PB_COLLECTOR_RESTART_MAX=0：禁用自动重启
        assert_eq!(plan_restart(false, 0, 0), RestartDecision::Exhausted { attempts: 0 });
        // 自定义上限 3：第 3 次后停止
        assert!(matches!(
            plan_restart(false, 2, 3),
            RestartDecision::Restart { attempt: 3, .. }
        ));
        assert_eq!(
            plan_restart(false, 3, 3),
            RestartDecision::Exhausted { attempts: 3 }
        );
    }

    #[test]
    fn restart_max_env_parsing() {
        assert_eq!(parse_restart_max(None), DEFAULT_RESTART_MAX);
        assert_eq!(parse_restart_max(Some("")), DEFAULT_RESTART_MAX);
        assert_eq!(parse_restart_max(Some("2")), 2);
        assert_eq!(parse_restart_max(Some(" 7 ")), 7);
        assert_eq!(parse_restart_max(Some("0")), 0);
        assert_eq!(parse_restart_max(Some("abc")), DEFAULT_RESTART_MAX);
        assert_eq!(parse_restart_max(Some("9999")), 50); // 上限保护
    }

    // ------------------------------------------------------------------
    // P1-13 运行验收修正：预算按存活时长重置（不能靠 hello 清零）
    // ------------------------------------------------------------------

    #[test]
    fn crash_loop_escalates_backoff_without_hello_reset() {
        // 典型坏驱动/坏配置：每次都能 hello，但 ~1.2s 后崩溃。预算必须累加，
        // 退避升级 1s→3s→10s，而不是每次 hello 清零后永远停在 1s。
        let mut budget = RestartBudget::default();
        let mut seen = Vec::new();
        for _ in 0..3 {
            let (next, decision, healthy) =
                budget.on_exit(false, Some(Duration::from_millis(1_200)), DEFAULT_RESTART_MAX);
            assert!(!healthy, "1.2s 存活必须判为 crash-loop，不能清零预算");
            match decision {
                RestartDecision::Restart { attempt, delay_ms } => {
                    seen.push((attempt, delay_ms));
                    budget = next;
                }
                other => panic!("expected Restart, got {other:?}"),
            }
        }
        assert_eq!(seen, vec![(1, 1_000), (2, 3_000), (3, 10_000)]);
        assert_eq!(budget.attempt, 3);
    }

    #[test]
    fn crash_loop_exhausts_budget_after_max_restarts() {
        let mut budget = RestartBudget::default();
        let mut attempts = Vec::new();
        for _ in 0..DEFAULT_RESTART_MAX {
            let (next, decision, _) =
                budget.on_exit(false, Some(Duration::from_millis(500)), DEFAULT_RESTART_MAX);
            match decision {
                RestartDecision::Restart { attempt, .. } => attempts.push(attempt),
                other => panic!("expected Restart, got {other:?}"),
            }
            budget = next;
        }
        assert_eq!(attempts, vec![1, 2, 3, 4, 5]);
        // 第 6 次崩溃：预算用尽 → 停止自动重启（保留手动重启）
        let (next, decision, _) =
            budget.on_exit(false, Some(Duration::from_millis(500)), DEFAULT_RESTART_MAX);
        assert_eq!(
            decision,
            RestartDecision::Exhausted { attempts: DEFAULT_RESTART_MAX }
        );
        assert_eq!(next.attempt, DEFAULT_RESTART_MAX);
    }

    #[test]
    fn healthy_uptime_resets_budget() {
        // 存活 ≥60s 后崩溃：健康运行过 → 预算清零，下一次重启从 attempt=1 / 1s 开始。
        let (next, decision, healthy) = RestartBudget { attempt: 4 }
            .on_exit(false, Some(Duration::from_secs(65)), DEFAULT_RESTART_MAX);
        assert!(healthy);
        assert_eq!(
            decision,
            RestartDecision::Restart {
                attempt: 1,
                delay_ms: 1_000
            }
        );
        assert_eq!(next.attempt, 1);
        // 恰好 60s 边界也算健康（>=）
        let (_, decision, healthy) =
            RestartBudget { attempt: 3 }.on_exit(false, Some(HEALTHY_UPTIME), DEFAULT_RESTART_MAX);
        assert!(healthy);
        assert!(matches!(
            decision,
            RestartDecision::Restart { attempt: 1, .. }
        ));
        // 59.999s 仍算 crash-loop，不清零
        let (_, decision, healthy) = RestartBudget { attempt: 3 }.on_exit(
            false,
            Some(HEALTHY_UPTIME - Duration::from_millis(1)),
            DEFAULT_RESTART_MAX,
        );
        assert!(!healthy);
        assert!(matches!(
            decision,
            RestartDecision::Restart { attempt: 4, .. }
        ));
    }

    #[test]
    fn shutdown_never_restarts_and_unknown_uptime_is_crash_loop() {
        // shutdown / 退出流程：即使刚健康运行过也不重启，预算不变。
        let (next, decision, healthy) = RestartBudget { attempt: 2 }
            .on_exit(true, Some(Duration::from_secs(600)), DEFAULT_RESTART_MAX);
        assert_eq!(decision, RestartDecision::NoRestart);
        assert!(!healthy);
        assert_eq!(next.attempt, 2);
        // uptime 未知（异常情况）按 crash-loop 保守处理，不清零。
        let (_, decision, healthy) =
            RestartBudget { attempt: 2 }.on_exit(false, None, DEFAULT_RESTART_MAX);
        assert!(!healthy);
        assert!(matches!(
            decision,
            RestartDecision::Restart {
                attempt: 3,
                delay_ms: 10_000
            }
        ));
    }

    #[test]
    fn manual_restart_resets_budget() {
        let reset = RestartBudget { attempt: 4 }.manual_restart();
        assert_eq!(reset, RestartBudget::default());
        // 手动重启后立刻崩溃：重新从 attempt=1 / 1s 开始退避
        let (_, decision, healthy) = reset.on_exit(
            false,
            Some(Duration::from_millis(800)),
            DEFAULT_RESTART_MAX,
        );
        assert!(!healthy);
        assert_eq!(
            decision,
            RestartDecision::Restart {
                attempt: 1,
                delay_ms: 1_000
            }
        );
    }

    #[test]
    fn disabled_auto_restart_exhausts_even_after_healthy_uptime() {
        // PB_COLLECTOR_RESTART_MAX=0：显式禁用优先，即使健康运行过也不再自动重启。
        let (next, decision, healthy) = RestartBudget { attempt: 2 }
            .on_exit(false, Some(Duration::from_secs(120)), 0);
        assert!(healthy); // 预算确实按健康重置了
        assert_eq!(decision, RestartDecision::Exhausted { attempts: 0 });
        assert_eq!(next.attempt, 0);
    }

    // ------------------------------------------------------------------
    // P1-06：cleanup_residual 归属判定
    // ------------------------------------------------------------------

    const DATA_DIR: &str = r"C:\Users\test\AppData\Local\PeripheralBattery";
    const COLLECTOR_EXE: &str = r"C:\Users\test\AppData\Local\PeripheralBattery\collector\collector.exe";
    const CURRENT_APP_PID: u32 = 4242;
    const CURRENT_INSTANCE: &str = "4242-aaaa-bbbb";

    fn record(pid: u32, app_pid: u32, instance: &str, data_dir: &str, exe: &str) -> PidRecord {
        PidRecord {
            pid,
            creation: 111,
            app_pid,
            instance: instance.to_string(),
            data_dir: data_dir.to_string(),
            exe: exe.to_string(),
            kind: "packaged".to_string(),
            started_unix_ms: 0,
        }
    }

    fn observed(creation: u64, exe: &str) -> ObservedProcess {
        ObservedProcess {
            creation,
            exe: exe.to_string(),
        }
    }

    fn ctx(recorded_app_alive: bool) -> ResidualContext<'static> {
        ResidualContext {
            data_dir: DATA_DIR,
            expected_exe: Some(COLLECTOR_EXE),
            app_pid: CURRENT_APP_PID,
            instance: CURRENT_INSTANCE,
            recorded_app_alive,
        }
    }

    #[test]
    fn no_record_is_noop() {
        assert_eq!(
            classify_residual(&ctx(false), None, None),
            ResidualAction::None
        );
    }

    #[test]
    fn foreign_data_dir_never_kills() {
        // 另一个数据目录/另一个安装副本的残留：即使 pid 存活也绝不 kill。
        let rec = record(999, 777, "prev", r"C:\other\data\PeripheralBattery", COLLECTOR_EXE);
        assert_eq!(
            classify_residual(&ctx(false), Some(&rec), Some(&observed(111, COLLECTOR_EXE))),
            ResidualAction::DeleteFileOnly {
                reason: "foreign data_dir"
            }
        );
    }

    #[test]
    fn legacy_record_without_ownership_fields_never_kills() {
        // 旧版本 pid 文件没有 data_dir → 无法证明归属，只删文件。
        let rec = record(999, 0, "", "", "");
        assert_eq!(
            classify_residual(&ctx(false), Some(&rec), Some(&observed(111, COLLECTOR_EXE))),
            ResidualAction::DeleteFileOnly {
                reason: "missing data_dir (legacy record)"
            }
        );
    }

    #[test]
    fn live_other_app_instance_is_left_alone() {
        // P1-06 核心回归：记录所属 app 仍在运行（不是本进程）→ 另一个活实例拥有 collector。
        let rec = record(999, 777, "other-run", DATA_DIR, COLLECTOR_EXE);
        assert_eq!(
            classify_residual(&ctx(true), Some(&rec), Some(&observed(111, COLLECTOR_EXE))),
            ResidualAction::LeaveAlone { app_pid: 777 }
        );
    }

    #[test]
    fn stale_collector_of_previous_exited_instance_is_killed() {
        let rec = record(999, 777, "previous-run", DATA_DIR, COLLECTOR_EXE);
        assert_eq!(
            classify_residual(&ctx(false), Some(&rec), Some(&observed(111, COLLECTOR_EXE))),
            ResidualAction::Kill { same_run: false }
        );
    }

    #[test]
    fn stale_collector_of_same_run_is_killed_and_marked_same_run() {
        let rec = record(999, CURRENT_APP_PID, CURRENT_INSTANCE, DATA_DIR, COLLECTOR_EXE);
        assert_eq!(
            classify_residual(&ctx(true), Some(&rec), Some(&observed(111, COLLECTOR_EXE))),
            ResidualAction::Kill { same_run: true }
        );
    }

    #[test]
    fn collector_already_gone_only_deletes_file() {
        let rec = record(999, 777, "previous-run", DATA_DIR, COLLECTOR_EXE);
        assert_eq!(
            classify_residual(&ctx(false), Some(&rec), None),
            ResidualAction::DeleteFileOnly {
                reason: "collector not running"
            }
        );
    }

    #[test]
    fn pid_reuse_only_deletes_file() {
        // pid 已复用于别的进程（creation 不同）→ 不能 kill。
        let rec = record(999, 777, "previous-run", DATA_DIR, COLLECTOR_EXE);
        assert_eq!(
            classify_residual(&ctx(false), Some(&rec), Some(&observed(222, COLLECTOR_EXE))),
            ResidualAction::DeleteFileOnly {
                reason: "pid reused (creation mismatch)"
            }
        );
    }

    #[test]
    fn foreign_exe_only_deletes_file() {
        // pid 记录属于本数据目录，但实际进程 exe 不是本安装的 collector → 不 kill。
        let rec = record(999, 777, "previous-run", DATA_DIR, COLLECTOR_EXE);
        assert_eq!(
            classify_residual(
                &ctx(false),
                Some(&rec),
                Some(&observed(111, r"C:\Windows\System32\python.exe"))
            ),
            ResidualAction::DeleteFileOnly {
                reason: "exe mismatch (foreign install)"
            }
        );
        // 记录里 exe 字段与本安装不一致（换过安装路径）同样不 kill。
        let rec2 = record(999, 777, "previous-run", DATA_DIR, r"D:\old\collector.exe");
        assert_eq!(
            classify_residual(&ctx(false), Some(&rec2), Some(&observed(111, COLLECTOR_EXE))),
            ResidualAction::DeleteFileOnly {
                reason: "exe mismatch (foreign install)"
            }
        );
    }

    #[test]
    fn invalid_or_unknown_owner_never_kills() {
        let zero_pid = record(0, 777, "previous-run", DATA_DIR, COLLECTOR_EXE);
        assert_eq!(
            classify_residual(&ctx(false), Some(&zero_pid), None),
            ResidualAction::DeleteFileOnly {
                reason: "invalid pid"
            }
        );
        // 新记录但 app_pid 缺失（0）：无法证明所属 app 已退出 → 保守不 kill。
        let no_owner = record(999, 0, "", DATA_DIR, COLLECTOR_EXE);
        assert_eq!(
            classify_residual(&ctx(false), Some(&no_owner), Some(&observed(111, COLLECTOR_EXE))),
            ResidualAction::DeleteFileOnly {
                reason: "unknown owner app (legacy record)"
            }
        );
    }

    #[test]
    fn normalize_strips_verbatim_prefix() {
        assert_eq!(
            normalize_path(r"\\?\D:\desktop\pb\collector\collector.exe"),
            r"d:\desktop\pb\collector\collector.exe"
        );
        assert_eq!(
            normalize_path(r"\??\D:\desktop\pb\collector\collector.exe"),
            r"d:\desktop\pb\collector\collector.exe"
        );
        assert_eq!(
            normalize_path(r"\\?\UNC\server\share\collector.exe"),
            r"\\server\share\collector.exe"
        );
        assert_eq!(
            normalize_path("D:/desktop/PB/collector/COLLECTOR.EXE"),
            r"d:\desktop\pb\collector\collector.exe"
        );
        assert_eq!(
            normalize_path(r"D:\desktop\pb\collector\collector.exe"),
            r"d:\desktop\pb\collector\collector.exe"
        );
    }

    #[test]
    fn outgoing_rejects_newline_and_oversize() {
        assert_eq!(
            validate_outgoing("{\"t\":\"refresh\"}\n").unwrap(),
            "{\"t\":\"refresh\"}"
        );
        assert!(validate_outgoing("{\"t\":\"refresh\"}\n{\"t\":\"shutdown\"}").is_err());
        assert!(validate_outgoing("{\"t\":\"refresh\"}\r\n").is_ok());
        assert!(validate_outgoing("a\rb").is_err());
        assert!(validate_outgoing("   ").is_err());
        let big = "x".repeat(MAX_LINE + 1);
        assert!(validate_outgoing(&big).is_err());
        let ok = "x".repeat(MAX_LINE);
        assert!(validate_outgoing(&ok).is_ok());
    }

    #[test]
    fn stat_summary_keeps_tail_fields_visible() {
        let v = json!({
            "t": "stat",
            "providers": {
                "logitech": {"polls": 12, "avg_ms": 31.0, "last_ms": 28.0},
                "xinput": {"polls": 3, "avg_ms": 9.5, "last_ms": 8.0},
                "keychron": {"polls": 4, "avg_ms": 2.0}
            },
            "timers_per_s": 0.99,
            "key_migrations": 2
        });
        let s = format_stat_summary(&v);
        assert!(s.contains("timers_per_s=0.99"), "{s}");
        assert!(s.contains("key_migrations=2"), "{s}");
        assert!(s.contains("providers=3"), "{s}");
        assert!(s.contains("slowest=logitech:31.0ms"), "{s}");
        // 不依赖整行截断：字段顺序里 timers/key_migrations 在 providers 之后也可见
        let empty = format_stat_summary(&json!({"t": "stat"}));
        assert!(empty.contains("timers_per_s=?"));
        assert!(empty.contains("providers=0"));
    }
}
