use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tauri::{AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, PhysicalSize, State};

type PendingResponse = mpsc::SyncSender<Result<Value, String>>;

struct Sidecar {
    child: Mutex<Child>,
    stdin: Mutex<ChildStdin>,
    pending: Arc<Mutex<HashMap<String, PendingResponse>>>,
}

#[derive(Clone, Copy)]
struct WindowPlacement {
    position: PhysicalPosition<i32>,
    size: PhysicalSize<u32>,
    was_maximized: bool,
}

struct BridgeState {
    sidecar: Arc<Sidecar>,
    backend_root: PathBuf,
    runtime: RuntimeCommand,
    compact_restore: Mutex<Option<WindowPlacement>>,
    picker_directory: Mutex<Option<PathBuf>>,
    allow_close: Arc<AtomicBool>,
}

#[derive(Clone)]
struct RuntimeCommand {
    executable: PathBuf,
    prefix_args: Vec<String>,
    working_dir: PathBuf,
    provider_config: PathBuf,
    python_path: Option<PathBuf>,
}

#[cfg(target_os = "windows")]
fn schedule_native_minimize() -> Result<(), String> {
    let script = format!(
        "Start-Sleep -Milliseconds 150; Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class BookflowWindow {{ [DllImport(\"user32.dll\")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow); }}'; $process = Get-Process -Id {}; [BookflowWindow]::ShowWindowAsync($process.MainWindowHandle, 6) | Out-Null",
        std::process::id(),
    );
    let mut command = Command::new("powershell.exe");
    command.args(["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", &script])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    use std::os::windows::process::CommandExt;
    command.creation_flags(0x08000000);
    command.spawn().map_err(|error| error.to_string())?;
    Ok(())
}

#[cfg(debug_assertions)]
fn configured_python() -> Result<PathBuf, String> {
    let value = std::env::var_os("BOOKFLOW_PYTHON")
        .ok_or_else(|| "BOOKFLOW_PYTHON must point to the approved bilingual-book interpreter".to_string())?;
    let path = PathBuf::from(value);
    if !path.is_file() {
        return Err(format!("BOOKFLOW_PYTHON does not exist: {}", path.display()));
    }
    Ok(path)
}

fn configure_runtime_command(command: &mut Command, runtime: &RuntimeCommand) {
    command.current_dir(&runtime.working_dir);
    if let Some(python_path) = &runtime.python_path {
        command.env("PYTHONPATH", python_path);
    } else {
        for name in [
            "BOOKFLOW_PYTHON", "BOOKFLOW_PROJECT_ROOT", "BOOKFLOW_DESKTOP_BACKEND_ROOT",
            "PYTHONPATH", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "NODE_PATH", "RUSTUP_HOME",
        ] {
            command.env_remove(name);
        }
    }
    command.env("PYTHONIOENCODING", "utf-8");
}

#[cfg(debug_assertions)]
fn resolve_runtime(_app: &AppHandle) -> Result<(RuntimeCommand, PathBuf), String> {
    let project_root = std::env::var_os("BOOKFLOW_PROJECT_ROOT").map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join(".."));
    let backend_root = std::env::var_os("BOOKFLOW_DESKTOP_BACKEND_ROOT").map(PathBuf::from)
        .unwrap_or_else(|| project_root.join("output").join("desktop_backend"));
    let runtime = RuntimeCommand {
        executable: configured_python()?,
        prefix_args: vec!["-m".to_string(), "bookflow.desktop_sidecar".to_string()],
        working_dir: project_root.clone(),
        provider_config: project_root.join("config").join("providers.local.yaml"),
        python_path: Some(project_root.join("src")),
    };
    Ok((runtime, backend_root))
}

#[cfg(not(debug_assertions))]
fn resolve_runtime(app: &AppHandle) -> Result<(RuntimeCommand, PathBuf), String> {
    let local_app_data = std::env::var_os("LOCALAPPDATA")
        .ok_or_else(|| "LOCALAPPDATA is unavailable".to_string())?;
    let data_root = PathBuf::from(local_app_data).join("Bookflow Scholar");
    let config_dir = data_root.join("config");
    let backend_root = data_root.join("backend");
    std::fs::create_dir_all(&config_dir).map_err(|error| error.to_string())?;
    std::fs::create_dir_all(&backend_root).map_err(|error| error.to_string())?;

    let resource_dir = app.path().resource_dir().map_err(|error| error.to_string())?;
    let sidecar = resource_dir.join("bookflow-sidecar").join("bookflow-sidecar.exe");
    let default_config = resource_dir.join("defaults").join("providers.yaml");
    let provider_config = config_dir.join("providers.yaml");
    if !provider_config.is_file() {
        std::fs::copy(&default_config, &provider_config)
            .map_err(|error| format!("failed to initialize provider configuration: {error}"))?;
    }
    if !sidecar.is_file() {
        return Err(format!("packaged sidecar is unavailable: {}", sidecar.display()));
    }
    let runtime = RuntimeCommand {
        executable: sidecar,
        prefix_args: Vec::new(),
        working_dir: data_root,
        provider_config,
        python_path: None,
    };
    Ok((runtime, backend_root))
}

fn spawn_sidecar(app: AppHandle, runtime: &RuntimeCommand, backend_root: &Path) -> Result<Arc<Sidecar>, String> {
    let log_dir = backend_root.join("logs");
    std::fs::create_dir_all(&log_dir).map_err(|error| error.to_string())?;
    let stderr_log = OpenOptions::new().create(true).append(true)
        .open(log_dir.join("sidecar-stderr.log")).map_err(|error| error.to_string())?;
    let mut command = Command::new(&runtime.executable);
    configure_runtime_command(&mut command, runtime);
    command.args(&runtime.prefix_args)
        .arg("bridge")
        .arg("--backend-root").arg(backend_root)
        .arg("--provider-config").arg(&runtime.provider_config)
        .arg("--persistent")
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::from(stderr_log));
    let mut child = command.spawn()
        .map_err(|error| format!("failed to start persistent Bookflow sidecar: {error}"))?;
    let stdin = child.stdin.take().ok_or_else(|| "sidecar stdin unavailable".to_string())?;
    let stdout = child.stdout.take().ok_or_else(|| "sidecar stdout unavailable".to_string())?;
    let pending: Arc<Mutex<HashMap<String, PendingResponse>>> = Arc::new(Mutex::new(HashMap::new()));
    let reader_pending = pending.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            let Ok(frame) = serde_json::from_str::<Value>(&line) else {
                let _ = app.emit("bookflow://sidecar-error", json!({"message": "invalid sidecar frame"}));
                continue;
            };
            match frame.get("kind").and_then(Value::as_str).unwrap_or("") {
                "response" => {
                    let request_id = frame.get("request_id").and_then(Value::as_str).unwrap_or("");
                    if let Ok(mut values) = reader_pending.lock() {
                        if let Some(sender) = values.remove(request_id) {
                            let response = frame.get("response").cloned()
                                .ok_or_else(|| "sidecar response payload missing".to_string());
                            let _ = sender.send(response);
                        }
                    }
                }
                "event" => {
                    if let Some(event) = frame.get("event") {
                        let _ = app.emit("bookflow://backend-event", event.clone());
                    }
                }
                "ready" => { let _ = app.emit("bookflow://sidecar-ready", frame.clone()); }
                "heartbeat" => { let _ = app.emit("bookflow://sidecar-heartbeat", frame.clone()); }
                "stopped" => { let _ = app.emit("bookflow://sidecar-stopped", frame.clone()); }
                _ => {}
            }
        }
        if let Ok(mut values) = reader_pending.lock() {
            for (_, sender) in values.drain() {
                let _ = sender.send(Err("persistent sidecar disconnected".to_string()));
            }
        }
        let _ = app.emit("bookflow://sidecar-disconnected", json!({"recoverable": true}));
    });
    Ok(Arc::new(Sidecar { child: Mutex::new(child), stdin: Mutex::new(stdin), pending }))
}

impl Sidecar {
    fn request(&self, envelope: Value) -> Result<Value, String> {
        let request_id = envelope.get("command_id").and_then(Value::as_str)
            .ok_or_else(|| "command_id missing".to_string())?.to_string();
        let (sender, receiver) = mpsc::sync_channel(1);
        self.pending.lock().map_err(|_| "sidecar pending lock poisoned".to_string())?
            .insert(request_id.clone(), sender);
        let frame = json!({"kind": "command", "request_id": request_id, "envelope": envelope});
        let write_result = self.stdin.lock().map_err(|_| "sidecar stdin lock poisoned".to_string())
            .and_then(|mut input| {
                writeln!(input, "{}", frame).map_err(|error| error.to_string())?;
                input.flush().map_err(|error| error.to_string())
            });
        if let Err(error) = write_result {
            if let Ok(mut pending) = self.pending.lock() { pending.remove(&request_id); }
            return Err(error);
        }
        receiver.recv_timeout(Duration::from_secs(30 * 60))
            .map_err(|_| "sidecar command response timed out".to_string())?
    }

    fn shutdown(&self) {
        let request_id = "tauri-exit";
        if let Ok(mut input) = self.stdin.lock() {
            let _ = writeln!(input, "{}", json!({"kind": "shutdown", "request_id": request_id}));
            let _ = input.flush();
        }
        if let Ok(mut child) = self.child.lock() {
            for _ in 0..40 {
                if child.try_wait().ok().flatten().is_some() { return; }
                std::thread::sleep(Duration::from_millis(100));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[tauri::command]
async fn bookflow_bridge_command(state: State<'_, BridgeState>, envelope: Value) -> Result<Value, String> {
    state.sidecar.request(envelope)
}

#[tauri::command]
async fn bookflow_window_command(app: AppHandle, state: State<'_, BridgeState>, action: String) -> Result<Value, String> {
    let window = app.get_webview_window("main").ok_or_else(|| "main window unavailable".to_string())?;
    match action.as_str() {
        "minimize" => {
            #[cfg(target_os = "windows")]
            schedule_native_minimize()?;
            #[cfg(not(target_os = "windows"))]
            window.minimize().map_err(|error| error.to_string())?;
        }
        "maximize_toggle" => {
            if state.compact_restore.lock().map_err(|_| "compact lock poisoned".to_string())?.is_some() {
                return Err("maximize is unavailable in compact mode".to_string());
            }
            if window.is_maximized().map_err(|error| error.to_string())? {
                window.unmaximize().map_err(|error| error.to_string())?;
            } else {
                window.maximize().map_err(|error| error.to_string())?;
            }
        }
        "restore" => {
            window.unminimize().map_err(|error| error.to_string())?;
            window.show().map_err(|error| error.to_string())?;
            window.set_focus().map_err(|error| error.to_string())?;
        }
        "start_dragging" => window.start_dragging().map_err(|error| error.to_string())?,
        "compact_toggle" => {
            let mut restore = state.compact_restore.lock().map_err(|_| "compact lock poisoned".to_string())?;
            if let Some(placement) = restore.take() {
                if window.is_maximized().map_err(|error| error.to_string())? {
                    window.unmaximize().map_err(|error| error.to_string())?;
                }
                window.set_size(placement.size).map_err(|error| error.to_string())?;
                window.set_position(placement.position).map_err(|error| error.to_string())?;
                if placement.was_maximized {
                    window.maximize().map_err(|error| error.to_string())?;
                }
            } else {
                let was_maximized = window.is_maximized().map_err(|error| error.to_string())?;
                if was_maximized {
                    window.unmaximize().map_err(|error| error.to_string())?;
                }
                let placement = WindowPlacement {
                    position: window.outer_position().map_err(|error| error.to_string())?,
                    size: window.inner_size().map_err(|error| error.to_string())?,
                    was_maximized,
                };
                *restore = Some(placement);
                window.set_size(LogicalSize::new(520.0, 760.0)).map_err(|error| error.to_string())?;
                window.center().map_err(|error| error.to_string())?;
            }
        }
        "close" => {
            state.allow_close.store(true, Ordering::SeqCst);
            app.exit(0);
        }
        _ => return Err(format!("unknown window action: {action}")),
    }
    Ok(json!({"action": action, "ok": true}))
}

#[tauri::command]
async fn bookflow_pick_paths(mode: String, state: State<'_, BridgeState>) -> Result<Vec<String>, String> {
    let picker = match mode.as_str() {
        "single" => r#"$d=New-Object System.Windows.Forms.OpenFileDialog; $d.Title='Select a book source'; $d.Filter='Book sources (*.pdf;*.png;*.jpg;*.jpeg)|*.pdf;*.png;*.jpg;*.jpeg'; $d.Multiselect=$false; $d.RestoreDirectory=$true; if($initialDirectory -and [System.IO.Directory]::Exists($initialDirectory)){$d.InitialDirectory=$initialDirectory}; try { if($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){$d.FileNames | ForEach-Object {[Console]::Out.WriteLine($_)}} } finally { $d.Dispose() }"#,
        "multiple" => r#"$d=New-Object System.Windows.Forms.OpenFileDialog; $d.Title='Select book sources'; $d.Filter='Book sources (*.pdf;*.png;*.jpg;*.jpeg)|*.pdf;*.png;*.jpg;*.jpeg'; $d.Multiselect=$true; $d.RestoreDirectory=$true; if($initialDirectory -and [System.IO.Directory]::Exists($initialDirectory)){$d.InitialDirectory=$initialDirectory}; try { if($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){$d.FileNames | ForEach-Object {[Console]::Out.WriteLine($_)}} } finally { $d.Dispose() }"#,
        "folder" => r#"$d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='Select a folder containing book sources'; $d.ShowNewFolderButton=$false; if($initialDirectory -and [System.IO.Directory]::Exists($initialDirectory)){$d.SelectedPath=$initialDirectory}; try { if($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Out.WriteLine($d.SelectedPath)} } finally { $d.Dispose() }"#,
        "web_assist_file" => r#"$d=New-Object System.Windows.Forms.OpenFileDialog; $d.Title='Select a Bookflow review result'; $d.Filter='Bookflow review results (*.json;*.csv;*.xlsx)|*.json;*.csv;*.xlsx'; $d.Multiselect=$false; $d.RestoreDirectory=$true; if($initialDirectory -and [System.IO.Directory]::Exists($initialDirectory)){$d.InitialDirectory=$initialDirectory}; try { if($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){$d.FileNames | ForEach-Object {[Console]::Out.WriteLine($_)}} } finally { $d.Dispose() }"#,
        "web_assist_folder" => r#"$d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='Select a Bookflow review package'; $d.ShowNewFolderButton=$false; if($initialDirectory -and [System.IO.Directory]::Exists($initialDirectory)){$d.SelectedPath=$initialDirectory}; try { if($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Out.WriteLine($d.SelectedPath)} } finally { $d.Dispose() }"#,
        _ => return Err(format!("unknown picker mode: {mode}")),
    };
    // A native dialog without an owner can open behind the borderless Tauri window and
    // leave the invoke waiting forever.  The transparent top-most owner keeps the
    // system picker visible without coupling it to a test path or project.
    let script = format!(r#"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public static class BookflowPickerDpi {{ [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }}'
[BookflowPickerDpi]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$initialDirectory = [Environment]::GetEnvironmentVariable('BOOKFLOW_PICKER_DIRECTORY', 'Process')
$owner = New-Object System.Windows.Forms.Form
$owner.Text = 'Bookflow'
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.Size = New-Object System.Drawing.Size(900, 650)
$owner.Opacity = 0
$owner.Show()
try {{
    {picker}
}} finally {{
    $owner.Close()
    $owner.Dispose()
}}
"#);
    let mut command = Command::new("powershell.exe");
    command.args(["-NoProfile", "-NonInteractive", "-STA", "-Command", script.as_str()]);
    if let Some(initial_directory) = state.picker_directory.lock()
        .map_err(|_| "picker directory lock poisoned".to_string())?.clone() {
        command.env("BOOKFLOW_PICKER_DIRECTORY", initial_directory);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let output = command.output().map_err(|error| format!("failed to open native picker: {error}"))?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).chars().take(500).collect());
    }
    let paths: Vec<String> = String::from_utf8_lossy(&output.stdout).lines().map(str::trim)
        .filter(|value| !value.is_empty()).map(str::to_string).collect();
    if let Some(first) = paths.first() {
        let selected = PathBuf::from(first);
        let directory = if mode == "folder" || mode == "web_assist_folder" { selected } else {
            selected.parent().map(Path::to_path_buf).unwrap_or(selected)
        };
        if directory.is_dir() {
            *state.picker_directory.lock()
                .map_err(|_| "picker directory lock poisoned".to_string())? = Some(directory);
        }
    }
    Ok(paths)
}

#[tauri::command]
async fn bookflow_open_web_assist_package(
    package_id: String,
    state: State<'_, BridgeState>,
) -> Result<Value, String> {
    if !package_id.starts_with("webassist_")
        || !package_id.chars().all(|value| value.is_ascii_alphanumeric() || value == '_')
    {
        return Err("invalid web-assist package id".to_string());
    }
    let root = state.backend_root.join("web_assist").join("exports")
        .canonicalize().map_err(|error| error.to_string())?;
    let target = root.join(&package_id).canonicalize().map_err(|error| error.to_string())?;
    if !target.starts_with(&root) || !target.is_dir() {
        return Err("web-assist package directory is unavailable".to_string());
    }
    let mut command = Command::new("explorer.exe");
    command.arg(&target).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command.spawn().map_err(|error| error.to_string())?;
    Ok(json!({"package_id": package_id, "opened": true}))
}

#[tauri::command]
async fn bookflow_credential_command(
    action: String,
    role: String,
    secret: Option<String>,
    state: State<'_, BridgeState>,
) -> Result<Value, String> {
    if !matches!(role.as_str(), "language" | "vision") {
        return Err("unknown model role".to_string());
    }
    if !matches!(action.as_str(), "status" | "set" | "delete") {
        return Err("unknown credential action".to_string());
    }
    let credential = if action == "set" {
        let value = secret.filter(|value| !value.is_empty())
            .ok_or_else(|| "credential must not be empty".to_string())?;
        if value.encode_utf16().count() > 256 {
            return Err("credential is too long for Windows Credential Manager".to_string());
        }
        Some(value)
    } else {
        None
    };
    let mut command = Command::new(&state.runtime.executable);
    configure_runtime_command(&mut command, &state.runtime);
    command.args(&state.runtime.prefix_args)
        .arg("credential")
        .arg("--action").arg(&action)
        .arg("--role").arg(&role)
        .arg("--provider-config").arg(&state.runtime.provider_config)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn().map_err(|error| format!("credential helper failed to start: {error}"))?;
    if let Some(value) = credential {
        let mut input = child.stdin.take().ok_or_else(|| "credential helper stdin unavailable".to_string())?;
        input.write_all(value.as_bytes()).map_err(|error| error.to_string())?;
    }
    let output = child.wait_with_output().map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).chars().take(500).collect());
    }
    serde_json::from_slice(&output.stdout).map_err(|error| format!("invalid credential helper response: {error}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .setup(|app| {
            let (runtime, backend_root) = resolve_runtime(app.handle())
                .map_err(std::io::Error::other)?;
            let sidecar = spawn_sidecar(app.handle().clone(), &runtime, &backend_root)
                .map_err(std::io::Error::other)?;
            let allow_close = Arc::new(AtomicBool::new(false));
            app.manage(BridgeState { sidecar, backend_root: backend_root.clone(),
                runtime,
                compact_restore: Mutex::new(None),
                picker_directory: Mutex::new(None), allow_close: allow_close.clone() });
            if let Some(window) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        if !allow_close.load(Ordering::SeqCst) {
                            api.prevent_close();
                            let _ = handle.emit("bookflow://close-requested", json!({"source": "system"}));
                        }
                    }
                });
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![bookflow_bridge_command, bookflow_window_command,
            bookflow_pick_paths, bookflow_open_web_assist_package, bookflow_credential_command]);
    let app = builder.build(tauri::generate_context!()).expect("error while building Bookflow desktop");
    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }) {
            handle.state::<BridgeState>().sidecar.shutdown();
        }
    });
}
