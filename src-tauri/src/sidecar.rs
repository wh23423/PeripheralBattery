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
            Ok(b) if b.is_empty() => return Ok(LineRead::Eof),
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
// PID 文件（残留清理）
// ---------------------------------------------------------------------------

#[derive(Serialize, Deserialize)]
struct PidRecord {
    pid: u32,
    creation: u64,
    exe: String,
    kind: String,
    started_unix_ms: u64,
}

fn unix_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
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
}

pub struct Sidecar {
    app: AppHandle,
    paths: Paths,
    inner: Mutex<Inner>,
    generation: AtomicU64,
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
            }),
            generation: AtomicU64::new(0),
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

    pub fn start(self: &Arc<Self>) -> bool {
        crate::log_info!("sidecar start requested");
        self.shutdown(Duration::from_millis(300), false);
        self.cleanup_residual();

        let spec = match self.resolve_command() {
            Ok(s) => s,
            Err(msg) => {
                crate::log_error!("sidecar spawn aborted: {msg}");
                self.publish_state(CollectorState::error(msg));
                return false;
            }
        };
        crate::log_info!(
            "sidecar spawn path={} args={:?} cwd={} kind={}",
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
        }

        self.write_pid_file(pid, spec.kind);
        self.publish_state(CollectorState {
            state: "starting".to_string(),
            pid: Some(pid),
            ..Default::default()
        });
        crate::log_info!("sidecar spawned pid={pid}");

        let me = self.clone();
        let _ = std::thread::Builder::new()
            .name("collector-stdout".into())
            .spawn(move || me.stdout_loop(stdout));
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

    pub fn restart(self: &Arc<Self>) -> bool {
        let me = self.clone();
        std::thread::Builder::new()
            .name("collector-restart".into())
            .spawn(move || {
                crate::log_info!("collector restart requested");
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

    fn stdout_loop(self: Arc<Self>, stdout: ChildStdout) {
        let mut reader = BufReader::with_capacity(64 * 1024, stdout);
        let mut buf = Vec::with_capacity(8192);
        loop {
            match read_line_limited(&mut reader, &mut buf, MAX_LINE) {
                Ok(LineRead::Line) => {
                    if buf.is_empty() {
                        continue;
                    }
                    let text = String::from_utf8_lossy(&buf).to_string();
                    self.on_line(&text);
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

    fn on_line(&self, line: &str) {
        let line = line.trim();
        if line.is_empty() {
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
            });
            return;
        }
        {
            let mut inner = self.inner.lock().unwrap();
            inner.hello = true;
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

    fn on_process_exit(&self, pid: u32, gen: u64, code: Option<i32>, source: &str) {
        {
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
        }

        let (child, job) = {
            let mut inner = self.inner.lock().unwrap();
            let child = inner.child.take();
            inner.stdin.take();
            let job = inner.job.take();
            (child, job)
        };
        if let Some(mut child) = child {
            let _ = child.wait();
        }
        // 先发布 exited / 记录退出码，再做 Job 轮询：app 退出时这两行必须落盘。
        crate::log_info!("sidecar exit(code={code:?}) pid={pid} source={source}");
        self.publish_state(CollectorState {
            state: "exited".to_string(),
            pid: Some(pid),
            code,
            ..Default::default()
        });
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
        let _ = fs::remove_file(&self.paths.collector_pid);
    }

    /// 退出握手：发 shutdown → 等 timeout → 超时 TerminateProcess/TerminateJobObject。
    pub fn shutdown(&self, timeout: Duration, send_shutdown: bool) -> bool {
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
            exe: info.map(|i| i.exe).unwrap_or_default(),
            kind: kind.to_string(),
            started_unix_ms: unix_ms(),
        };
        if let Ok(text) = serde_json::to_string(&rec) {
            let _ = fs::write(&self.paths.collector_pid, text);
        }
    }

    fn cleanup_residual(&self) {
        if let Ok(text) = fs::read_to_string(&self.paths.collector_pid) {
            if let Ok(rec) = serde_json::from_str::<PidRecord>(&text) {
                if let Some(info) = platform::process_info(rec.pid) {
                    let creation_match = rec.creation == 0 || rec.creation == info.creation;
                    let exe_l = info.exe.to_ascii_lowercase();
                    let name_match =
                        exe_l.ends_with("python.exe") || exe_l.ends_with("collector.exe");
                    if creation_match && name_match {
                        crate::log_warn!(
                            "cleanup residual collector pid={} exe={}",
                            rec.pid,
                            info.exe
                        );
                        platform::kill_process(rec.pid);
                    }
                }
            }
            let _ = fs::remove_file(&self.paths.collector_pid);
        }

        let expected = self.packaged_collector_exe();
        if let Some(expected) = expected {
            let want = normalize_path(&expected.to_string_lossy());
            for pid in platform::find_processes_by_exe_name("collector.exe") {
                if pid == std::process::id() {
                    continue;
                }
                if let Some(info) = platform::process_info(pid) {
                    if normalize_path(&info.exe) == want {
                        crate::log_warn!("cleanup residual packaged collector pid={pid}");
                        platform::kill_process(pid);
                    }
                }
            }
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
    use super::{format_stat_summary, normalize_path, validate_outgoing, MAX_LINE};
    use serde_json::json;

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
