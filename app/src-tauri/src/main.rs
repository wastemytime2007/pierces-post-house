// PreCut — Tauri bridge.
//
// This process spawns the Python backend as a child and acts as a message
// router. React → Tauri IPC → Python stdin. Python stdout → Tauri events → React.
//
// DESIGN NOTES
// ------------
// * Python communicates in JSON Lines (\n-terminated). We use BufReader::lines()
//   to get each event as one string, then forward as-is to React via emit().
// * React forwards commands via the `send_to_backend` invoke command; we write
//   them to the Python process's stdin.
// * The backend is spawned LAZILY — first time React calls `send_to_backend`
//   after the app window is ready. This lets the user see the window before
//   Python starts up.
// * We do not buffer events or try to parse them on the Rust side. The Rust
//   side is a pipe. React deserializes and acts.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Arc;
use std::thread;
use tauri::{AppHandle, Emitter, Manager, State};

// ---------------------------------------------------------------------------
// Backend process handle (shared across commands via Tauri state)
// ---------------------------------------------------------------------------

struct BackendHandle {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
}

impl BackendHandle {
    fn new() -> Self {
        Self {
            child: None,
            stdin: None,
        }
    }

    fn is_running(&self) -> bool {
        self.stdin.is_some()
    }
}

type SharedBackend = Arc<Mutex<BackendHandle>>;

// ---------------------------------------------------------------------------
// Spawn helpers
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Spawn helpers
// ---------------------------------------------------------------------------

/// Where to find backend.py.
/// Lookup order:
///   1. `BACKEND_PYTHON_SCRIPT` env var (dev override)
///   2. Tauri resource dir — production path after bundling. We use the
///      resolve() API which handles the `_up_` translation that Tauri
///      applies to bundle resources with `../` prefixes.
///   3. Development layout: sibling to the Tauri crate at project root
///   4. Dev from within src-tauri/
fn resolve_python_script(app: &AppHandle) -> Result<PathBuf, String> {
    // 1) Explicit override via env var — useful during development
    if let Ok(p) = std::env::var("BACKEND_PYTHON_SCRIPT") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Ok(path);
        }
    }

    // 2) Bundled resource (production builds).
    //    tauri.conf.json has `"resources": ["../python_backend/backend.py", ...]`
    //    Tauri translates `../` → `_up_` during bundling, and the resolve()
    //    API translates it back for us.
    if let Ok(resolved) = app
        .path()
        .resolve("../python_backend/backend.py", tauri::path::BaseDirectory::Resource)
    {
        if resolved.exists() {
            return Ok(resolved);
        }
    }

    // Fallback: scan the resource dir manually — helps if Tauri's resolve()
    // path changes in future versions
    if let Ok(resource_dir) = app.path().resource_dir() {
        let candidates = [
            resource_dir.join("_up_").join("python_backend").join("backend.py"),
            resource_dir.join("python_backend").join("backend.py"),
            resource_dir.join("backend.py"),
        ];
        for c in &candidates {
            if c.exists() {
                return Ok(c.clone());
            }
        }
    }

    // 3) Development: project root sibling
    if let Ok(cwd) = std::env::current_dir() {
        let dev_path = cwd.join("python_backend").join("backend.py");
        if dev_path.exists() {
            return Ok(dev_path);
        }
        // 4) Dev from within src-tauri/
        if let Some(parent) = cwd.parent() {
            let p = parent.join("python_backend").join("backend.py");
            if p.exists() {
                return Ok(p);
            }
        }
    }

    Err(format!(
        "Could not locate python_backend/backend.py. Tried:\n\
        - BACKEND_PYTHON_SCRIPT env var\n\
        - Tauri resource dir ({:?})\n\
        - Current dir ({:?})\n\
        - Parent of current dir\n\
        Set BACKEND_PYTHON_SCRIPT env var to override.",
        app.path().resource_dir().ok(),
        std::env::current_dir().ok(),
    ))
}

/// Where to find setup_helper.py. Same lookup order as backend.py since
/// they live in the same directory.
fn resolve_setup_helper(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(p) = std::env::var("BACKEND_SETUP_HELPER") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Ok(path);
        }
    }

    if let Ok(resolved) = app
        .path()
        .resolve("../python_backend/setup_helper.py", tauri::path::BaseDirectory::Resource)
    {
        if resolved.exists() {
            return Ok(resolved);
        }
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        let candidates = [
            resource_dir.join("_up_").join("python_backend").join("setup_helper.py"),
            resource_dir.join("python_backend").join("setup_helper.py"),
            resource_dir.join("setup_helper.py"),
        ];
        for c in &candidates {
            if c.exists() {
                return Ok(c.clone());
            }
        }
    }

    if let Ok(cwd) = std::env::current_dir() {
        let dev_path = cwd.join("python_backend").join("setup_helper.py");
        if dev_path.exists() {
            return Ok(dev_path);
        }
        if let Some(parent) = cwd.parent() {
            let p = parent.join("python_backend").join("setup_helper.py");
            if p.exists() {
                return Ok(p);
            }
        }
    }

    Err("Could not locate python_backend/setup_helper.py".into())
}

/// Which Python to use for the setup helper. CRITICALLY different from
/// resolve_python_binary() below: the setup helper must run BEFORE the
/// user has installed any Python, so we can't rely on Homebrew Python
/// existing. /usr/bin/python3 ships with macOS 12+ and only needs
/// stdlib — which is all the setup helper imports.
///
/// Falls back to the main resolver if /usr/bin/python3 is somehow absent
/// (shouldn't happen on a supported macOS version).
fn resolve_setup_python() -> String {
    let system_python = PathBuf::from("/usr/bin/python3");
    if system_python.exists() {
        return "/usr/bin/python3".to_string();
    }
    resolve_python_binary()
}

/// Which Python executable to use. GUI apps on macOS don't inherit shell PATH,
/// so `python3` alone might fail even if it works in Terminal. We check common
/// locations explicitly.
fn resolve_python_binary() -> String {
    // Explicit override first
    if let Ok(p) = std::env::var("PYTHON_BIN") {
        if PathBuf::from(&p).exists() {
            return p;
        }
    }

    // Check common locations where python3 lives on macOS
    let candidates = [
        "/opt/homebrew/bin/python3",   // Apple Silicon Homebrew
        "/usr/local/bin/python3",      // Intel Homebrew
        "/usr/bin/python3",            // macOS system Python
        "/opt/local/bin/python3",      // MacPorts
    ];
    for c in &candidates {
        if PathBuf::from(c).exists() {
            return c.to_string();
        }
    }

    // Last resort — let the shell find it (might fail in GUI context)
    "python3".into()
}

/// Spawn the Python backend and wire up stdout reader.
fn spawn_backend(app: AppHandle, backend: SharedBackend) -> Result<(), String> {
    let script = resolve_python_script(&app)?;
    let python = resolve_python_binary();

    let mut cmd = Command::new(&python);
    cmd.arg(&script);
    cmd.stdin(Stdio::piped());
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    // GUI-launched macOS apps don't inherit shell PATH, so tools installed
    // via Homebrew or MacPorts are invisible to the spawned process.
    // Prepend the common locations so ffmpeg, python extensions, etc. work.
    let current_path = std::env::var("PATH").unwrap_or_default();
    let extra_paths = "/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
    let new_path = if current_path.is_empty() {
        extra_paths.to_string()
    } else {
        format!("{extra_paths}:{current_path}")
    };
    cmd.env("PATH", new_path);

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn Python backend: {e}\nTried: {python} {script:?}"))?;

    let stdout = child.stdout.take().ok_or("Failed to capture backend stdout")?;
    let stderr = child.stderr.take().ok_or("Failed to capture backend stderr")?;
    let stdin = child.stdin.take().ok_or("Failed to capture backend stdin")?;

    // Save handle
    {
        let mut guard = backend.lock();
        guard.child = Some(child);
        guard.stdin = Some(stdin);
    }

    // Reader thread: each line from Python → "backend-event" Tauri event to React
    let app_out = app.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(l) if !l.is_empty() => {
                    // We don't parse — forward the raw JSON string to React.
                    if let Err(e) = app_out.emit("backend-event", &l) {
                        eprintln!("Failed to emit backend-event: {e}");
                    }
                }
                Ok(_) => {} // blank line, ignore
                Err(e) => {
                    eprintln!("Error reading backend stdout: {e}");
                    break;
                }
            }
        }
    });

    // Stderr reader — Python stderr gets noisy from Whisper's tqdm
    // progress bars and deprecation warnings. We classify lines here so
    // the React activity log doesn't light up red for normal chatter.
    let app_err = app.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().flatten() {
            eprintln!("[backend stderr] {line}");

            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }

            // Classify: real errors vs. progress bar / warning noise
            let level = if trimmed.contains("Traceback")
                || trimmed.contains("Error:")
                || trimmed.starts_with("ERROR")
                || trimmed.contains("CRITICAL")
            {
                "error"
            } else if trimmed.contains("UserWarning")
                || trimmed.contains("FutureWarning")
                || trimmed.contains("DeprecationWarning")
                || trimmed.contains("warnings.warn")
            {
                "warn"
            } else if trimmed.contains("frames/s")
                || trimmed.contains("it/s")
                || trimmed.starts_with("[")
                || trimmed.chars().next().map_or(false, |c| c.is_ascii_digit())
            {
                // tqdm progress — benign. Suppress entirely to avoid log spam.
                continue;
            } else {
                "info"
            };

            let payload = serde_json::json!({
                "type": "stderr",
                "level": level,
                "message": line,
            });
            let _ = app_err.emit("backend-event", payload.to_string());
        }
    });

    Ok(())
}

// ---------------------------------------------------------------------------
// Tauri commands (invocable from React)
// ---------------------------------------------------------------------------

#[tauri::command]
fn send_to_backend(
    command_json: String,
    backend: State<SharedBackend>,
    app: AppHandle,
) -> Result<(), String> {
    // Lazy-spawn the backend on first call
    {
        let guard = backend.lock();
        if !guard.is_running() {
            drop(guard);
            spawn_backend(app, backend.inner().clone())?;
        }
    }

    let mut guard = backend.lock();
    let stdin = guard
        .stdin
        .as_mut()
        .ok_or("Backend has no stdin (crashed?)")?;

    writeln!(stdin, "{command_json}")
        .map_err(|e| format!("Failed to write to backend: {e}"))?;
    stdin
        .flush()
        .map_err(|e| format!("Failed to flush backend stdin: {e}"))?;

    Ok(())
}

#[tauri::command]
fn backend_status(backend: State<SharedBackend>) -> BackendStatus {
    let guard = backend.lock();
    BackendStatus {
        running: guard.is_running(),
    }
}

#[derive(Serialize, Deserialize)]
struct BackendStatus {
    running: bool,
}

/// Open Finder with the given file highlighted/selected.
/// Uses macOS's `open -R` which is the native "Reveal in Finder" behavior.
#[tauri::command]
fn show_in_finder(path: String) -> Result<(), String> {
    let path_buf = PathBuf::from(&path);
    if !path_buf.exists() {
        return Err(format!("File does not exist: {path}"));
    }
    Command::new("open")
        .arg("-R")
        .arg(&path)
        .spawn()
        .map_err(|e| format!("Failed to open Finder: {e}"))?;
    Ok(())
}

// ---------------------------------------------------------------------------
// First-launch setup (Drop 4.44)
// ---------------------------------------------------------------------------
//
// The setup flow lives in python_backend/setup_helper.py — a stdlib-only
// script that runs under /usr/bin/python3 (available on all supported
// macOS versions) and does NOT require the main backend's Python deps to
// be installed. This is important because "install the main backend's
// Python deps" is one of the things it does.
//
// Protocol:
//   * `setup_check` runs the helper once, parses its single-line JSON
//     result, and returns it synchronously.
//   * `setup_install` spawns the helper for a long-running install and
//     streams its JSONL stdout as "backend-event" events (same channel
//     as the main backend uses — the React side already knows how to
//     parse these). Multiple installs can't run concurrently; we reject
//     a second call while one is in flight.

type SharedSetupChild = Arc<Mutex<Option<Child>>>;

/// Run the setup helper with `check` and return its JSON report. This is
/// synchronous — the check takes ~1 second because it shells out to
/// xcode-select, brew, ffmpeg, python3.
#[tauri::command]
fn setup_check(app: AppHandle) -> Result<String, String> {
    let script = resolve_setup_helper(&app)?;
    let python = resolve_setup_python();

    let mut cmd = Command::new(&python);
    cmd.arg(&script).arg("check");

    // Same PATH extension as the main backend — brew/ffmpeg aren't on
    // the inherited PATH in a GUI-launched app.
    let extra_paths = "/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
    let current_path = std::env::var("PATH").unwrap_or_default();
    let new_path = if current_path.is_empty() {
        extra_paths.to_string()
    } else {
        format!("{extra_paths}:{current_path}")
    };
    cmd.env("PATH", new_path);

    let output = cmd
        .output()
        .map_err(|e| format!("Failed to run setup_helper.py check: {e}\nTried: {python} {script:?}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("setup_helper.py check failed (exit {}): {stderr}",
                           output.status.code().unwrap_or(-1)));
    }

    // The helper emits exactly one JSON line for `check`. Return it raw
    // to the React side, which parses it.
    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok(stdout.trim().to_string())
}

/// Stream a setup install. Returns immediately after spawning; the
/// install emits JSONL events on "backend-event" until it finishes
/// with either install_done or install_error.
#[tauri::command]
fn setup_install(
    component: String,
    app: AppHandle,
    child: State<SharedSetupChild>,
) -> Result<(), String> {
    // Reject concurrent installs — both for sanity and because brew
    // serializes locally anyway.
    {
        let guard = child.lock();
        if guard.is_some() {
            return Err("A setup install is already running.".into());
        }
    }

    let script = resolve_setup_helper(&app)?;
    let python = resolve_setup_python();

    // Allowlist the component — we pass it as argv, but even though
    // the Python side validates, we sanity-check here so a junk string
    // doesn't even reach Python.
    const ALLOWED: &[&str] = &[
        "xcode_clt", "homebrew", "ffmpeg", "python", "python_packages",
    ];
    if !ALLOWED.contains(&component.as_str()) {
        return Err(format!("Unknown setup component: {component}"));
    }

    let mut cmd = Command::new(&python);
    cmd.arg(&script).arg("install").arg(&component);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    let extra_paths = "/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
    let current_path = std::env::var("PATH").unwrap_or_default();
    let new_path = if current_path.is_empty() {
        extra_paths.to_string()
    } else {
        format!("{extra_paths}:{current_path}")
    };
    cmd.env("PATH", new_path);

    let mut spawned = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn setup_helper.py install {component}: {e}"))?;

    let stdout = spawned.stdout.take().ok_or("Failed to capture setup stdout")?;
    let stderr = spawned.stderr.take().ok_or("Failed to capture setup stderr")?;

    // Store the child so we know it's running (and could kill it later
    // if we add a cancel button).
    {
        let mut guard = child.lock();
        *guard = Some(spawned);
    }

    // stdout: forward each JSON line as a backend-event. The React side
    // already handles that event type.
    let app_out = app.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().flatten() {
            if line.is_empty() { continue; }
            if let Err(e) = app_out.emit("backend-event", &line) {
                eprintln!("setup emit failed: {e}");
            }
        }
    });

    // stderr: wrap lines in a synthetic install_progress event so React
    // shows them in the install log. This catches things like pip
    // warnings that go to stderr but aren't errors.
    let app_err = app.clone();
    let component_for_err = component.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().flatten() {
            if line.trim().is_empty() { continue; }
            let payload = serde_json::json!({
                "type": "install_progress",
                "component": component_for_err,
                "stage": "stderr",
                "message": line,
            });
            let _ = app_err.emit("backend-event", payload.to_string());
        }
    });

    // Waiter thread: when the child exits, clear the state handle and
    // emit a terminal event if the helper didn't already emit one.
    // (Normally install_done/install_error is emitted by the helper
    // itself, but if it crashes without emitting we want the UI to
    // unfreeze.)
    let child_state = child.inner().clone();
    let app_waiter = app.clone();
    let component_for_wait = component.clone();
    thread::spawn(move || {
        // Pull the child out of the shared handle so we can wait() on it
        // without holding the lock.
        let mut child_opt = {
            let mut guard = child_state.lock();
            guard.take()
        };
        if let Some(ref mut c) = child_opt {
            let status = c.wait();
            match status {
                Ok(st) if !st.success() => {
                    let payload = serde_json::json!({
                        "type": "install_error",
                        "component": component_for_wait,
                        "ok": false,
                        "message": format!("Installer exited {}", st.code().unwrap_or(-1)),
                    });
                    let _ = app_waiter.emit("backend-event", payload.to_string());
                }
                Err(e) => {
                    let payload = serde_json::json!({
                        "type": "install_error",
                        "component": component_for_wait,
                        "ok": false,
                        "message": format!("Waiting on installer failed: {e}"),
                    });
                    let _ = app_waiter.emit("backend-event", payload.to_string());
                }
                Ok(_) => {} // success — helper already emitted install_done
            }
        }
    });

    Ok(())
}

/// Mark setup as complete — writes setup_complete=true into the settings
/// file so we don't show the setup screen on next launch.
#[tauri::command]
fn setup_mark_complete(app: AppHandle) -> Result<(), String> {
    let script = resolve_setup_helper(&app)?;
    let python = resolve_setup_python();
    let status = Command::new(&python)
        .arg(&script)
        .arg("mark-complete")
        .status()
        .map_err(|e| format!("Failed to mark setup complete: {e}"))?;
    if !status.success() {
        return Err(format!("mark-complete exited {}", status.code().unwrap_or(-1)));
    }
    Ok(())
}

/// Reset the setup-complete flag — exposed so the React side can put
/// a "re-run setup" button in settings. Doesn't uninstall anything.
#[tauri::command]
fn setup_reset(app: AppHandle) -> Result<(), String> {
    let script = resolve_setup_helper(&app)?;
    let python = resolve_setup_python();
    let status = Command::new(&python)
        .arg(&script)
        .arg("reset")
        .status()
        .map_err(|e| format!("Failed to reset setup: {e}"))?;
    if !status.success() {
        return Err(format!("reset exited {}", status.code().unwrap_or(-1)));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

fn main() {
    let backend: SharedBackend = Arc::new(Mutex::new(BackendHandle::new()));
    let setup_child: SharedSetupChild = Arc::new(Mutex::new(None));

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .manage(backend)
        .manage(setup_child)
        .invoke_handler(tauri::generate_handler![
            send_to_backend,
            backend_status,
            show_in_finder,
            setup_check,
            setup_install,
            setup_mark_complete,
            setup_reset,
        ])
        .run(tauri::generate_context!())
        .expect("Error while running PreCut");
}
