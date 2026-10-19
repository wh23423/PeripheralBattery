//! 极简日志：写 `%LOCALAPPDATA%\PeripheralBattery\logs\app.log`，5 × 2MB 轮转。
//!
//! 不引入 log/env_logger，保证启动最早阶段（窗口创建前）就能落盘。
//! 级别由环境变量 `PB_LOG_LEVEL` 控制（debug/info/warn/error，默认 info）。

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

pub const MAX_LOG_SIZE: u64 = 2 * 1024 * 1024;
pub const MAX_LOG_FILES: usize = 5;

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug)]
pub enum Level {
    Debug,
    Info,
    Warn,
    Error,
}

impl Level {
    pub fn as_str(self) -> &'static str {
        match self {
            Level::Debug => "DEBUG",
            Level::Info => "INFO",
            Level::Warn => "WARN",
            Level::Error => "ERROR",
        }
    }

    pub fn parse(s: &str) -> Level {
        match s.trim().to_ascii_lowercase().as_str() {
            "debug" | "trace" => Level::Debug,
            "warn" | "warning" => Level::Warn,
            "error" => Level::Error,
            _ => Level::Info,
        }
    }
}

/// 单文件轮转写入器：超过 max_size 时 app.log -> app.log.1 -> ... -> app.log.(keep-1)
pub struct RotatingFile {
    path: PathBuf,
    file: File,
    size: u64,
    max_size: u64,
    keep: usize,
}

impl RotatingFile {
    pub fn open(path: PathBuf, max_size: u64, keep: usize) -> std::io::Result<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)?;
        let size = file.metadata().map(|m| m.len()).unwrap_or(0);
        Ok(Self {
            path,
            file,
            size,
            max_size,
            keep,
        })
    }

    fn numbered(&self, n: usize) -> PathBuf {
        let mut s = self.path.as_os_str().to_os_string();
        s.push(format!(".{n}"));
        PathBuf::from(s)
    }

    fn rotate(&mut self) {
        let _ = self.file.flush();
        for i in (1..self.keep.saturating_sub(1)).rev() {
            let from = self.numbered(i);
            let to = self.numbered(i + 1);
            if from.exists() {
                let _ = fs::rename(&from, &to);
            }
        }
        if self.keep > 1 {
            let _ = fs::rename(&self.path, self.numbered(1));
        }
        match OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
        {
            Ok(f) => {
                self.file = f;
                self.size = 0;
            }
            Err(_) => {
                // 重开失败时保留旧句柄继续追加，避免丢日志
                self.size = 0;
            }
        }
    }

    pub fn write_line(&mut self, line: &str) {
        if self.size + line.len() as u64 + 1 > self.max_size && self.size > 0 {
            self.rotate();
        }
        if self.file.write_all(line.as_bytes()).is_ok()
            && self.file.write_all(b"\n").is_ok()
            && self.file.flush().is_ok()
        {
            self.size += line.len() as u64 + 1;
        }
    }
}

/// app.log 的全局 logger。
pub struct Logger {
    level: Level,
    file: Mutex<RotatingFile>,
}

impl Logger {
    pub fn new(path: PathBuf, level: Level) -> std::io::Result<Self> {
        Ok(Self {
            level,
            file: Mutex::new(RotatingFile::open(path, MAX_LOG_SIZE, MAX_LOG_FILES)?),
        })
    }

    pub fn log(&self, level: Level, msg: &str) {
        if level < self.level {
            return;
        }
        let line = format!(
            "{} [{}] {}",
            crate::platform::local_timestamp(),
            level.as_str(),
            msg
        );
        if let Ok(mut f) = self.file.lock() {
            f.write_line(&line);
        }
    }
}

static LOGGER: OnceLock<Arc<Logger>> = OnceLock::new();

pub fn init(path: PathBuf, level: Level) -> Arc<Logger> {
    let logger = match Logger::new(path, level) {
        Ok(l) => Arc::new(l),
        Err(e) => {
            // 极端的兜底：路径不可写时退到临时目录
            let fallback = std::env::temp_dir().join("PeripheralBattery").join("app.log");
            let _ = fs::create_dir_all(fallback.parent().unwrap_or(Path::new(".")));
            match Logger::new(fallback, level) {
                Ok(l) => {
                    eprintln!("app.log 打开失败({e})，回退到临时目录");
                    Arc::new(l)
                }
                Err(e2) => {
                    eprintln!("日志完全不可用: {e2}");
                    Arc::new(Logger {
                        level,
                        file: Mutex::new(
                            RotatingFile::open(
                                std::env::temp_dir().join("unused.log"),
                                1,
                                1,
                            )
                            .expect("temp log"),
                        ),
                    })
                }
            }
        }
    };
    let _ = LOGGER.set(logger.clone());
    logger
}

pub fn log(level: Level, msg: String) {
    match LOGGER.get() {
        Some(l) => l.log(level, &msg),
        None => eprintln!("[{}] {msg}", level.as_str()),
    }
}

#[macro_export]
macro_rules! log_debug {
    ($($arg:tt)*) => {
        $crate::logging::log($crate::logging::Level::Debug, format!($($arg)*))
    };
}

#[macro_export]
macro_rules! log_info {
    ($($arg:tt)*) => {
        $crate::logging::log($crate::logging::Level::Info, format!($($arg)*))
    };
}

#[macro_export]
macro_rules! log_warn {
    ($($arg:tt)*) => {
        $crate::logging::log($crate::logging::Level::Warn, format!($($arg)*))
    };
}

#[macro_export]
macro_rules! log_error {
    ($($arg:tt)*) => {
        $crate::logging::log($crate::logging::Level::Error, format!($($arg)*))
    };
}
