use std::fs;
use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::{Manager, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::ShellExt;

mod startup;
use startup::{Action as StartupAction, StartupWait};

#[cfg(target_os = "macos")]
type BackendChild = tauri_plugin_shell::process::CommandChild;
#[cfg(target_os = "windows")]
type BackendChild = std::process::Child;

struct RuntimeState {
    child: Mutex<Option<BackendChild>>,
    server_url: Mutex<Option<String>>,
    stop: Arc<AtomicBool>,
    backend_exit: Arc<Mutex<Option<String>>>,
    startup_feedback: Mutex<Option<serde_json::Value>>,
}

fn replace_file(source: &Path, destination: &Path) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    if destination.exists() {
        fs::remove_file(destination).map_err(|error| error.to_string())?;
    }
    fs::rename(source, destination).map_err(|error| error.to_string())
}

fn copy_tree(source: &Path, destination: &Path) -> Result<(), String> {
    if !source.is_dir() {
        return Err(format!("Bundled directory is missing: {}", source.display()));
    }
    fs::create_dir_all(destination).map_err(|error| error.to_string())?;
    for entry in fs::read_dir(source).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        let file_type = entry.file_type().map_err(|error| error.to_string())?;
        if file_type.is_symlink() {
            return Err(format!("Bundled symbolic links are not allowed: {}", entry.path().display()));
        }
        let target = destination.join(entry.file_name());
        if file_type.is_dir() {
            copy_tree(&entry.path(), &target)?;
        } else if file_type.is_file() {
            fs::copy(entry.path(), target).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn ensure_user_workspace(
    starter: &Path,
    data_root: &Path,
) -> Result<(PathBuf, PathBuf), String> {
    fs::create_dir_all(data_root).map_err(|error| error.to_string())?;
    let default_vault = data_root.join("Vault");
    if !default_vault.exists() {
        let incoming = data_root.join("Vault.incoming");
        if incoming.exists() {
            fs::remove_dir_all(&incoming).map_err(|error| error.to_string())?;
        }
        copy_tree(starter, &incoming)?;
        fs::rename(&incoming, &default_vault).map_err(|error| error.to_string())?;
    }
    if !default_vault.is_dir() {
        return Err(format!(
            "Bok Vault is not a directory: {}",
            default_vault.display()
        ));
    }

    let personal_core = data_root.join("Personal Core");
    fs::create_dir_all(&personal_core).map_err(|error| error.to_string())?;
    let vault = selected_vault(data_root).unwrap_or(default_vault);
    configure_vault(&vault, &personal_core)?;
    Ok((vault, personal_core))
}

fn selected_vault(data_root: &Path) -> Option<PathBuf> {
    let path = data_root.join("selected-vault.json");
    let raw = fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let selected = PathBuf::from(value.get("path")?.as_str()?);
    let canonical = selected.canonicalize().ok()?;
    canonical.is_dir().then_some(canonical)
}

fn persist_selected_vault(data_root: &Path, selected: &Path) -> Result<PathBuf, String> {
    let canonical = selected.canonicalize().map_err(|error| error.to_string())?;
    if !canonical.is_dir() {
        return Err("请选择一个文件夹，而不是文件。".into());
    }
    let personal_core = data_root.join("Personal Core");
    if canonical == data_root || canonical == personal_core || canonical.starts_with(&personal_core) {
        return Err("知识库文件夹不能使用 Bok 的程序数据或 Personal Core 目录。".into());
    }
    configure_vault(&canonical, &personal_core)?;
    let destination = data_root.join("selected-vault.json");
    let temporary = data_root.join("selected-vault.json.incoming");
    let rendered = serde_json::to_vec_pretty(&serde_json::json!({
        "schema": 1,
        "path": canonical.to_string_lossy(),
    }))
    .map_err(|error| error.to_string())?;
    fs::write(&temporary, rendered).map_err(|error| error.to_string())?;
    replace_file(&temporary, &destination)?;
    Ok(canonical)
}

fn configure_vault(vault: &Path, personal_core: &Path) -> Result<(), String> {
    if !vault.is_dir() {
        return Err(format!("Bok Vault is not a directory: {}", vault.display()));
    }
    let state_dir = vault.join(".bok");
    fs::create_dir_all(&state_dir).map_err(|error| error.to_string())?;
    let config_path = state_dir.join("config.json");
    let mut config = if config_path.is_file() {
        let raw = fs::read_to_string(&config_path).map_err(|error| error.to_string())?;
        match serde_json::from_str::<serde_json::Value>(&raw) {
            Ok(value) if value.is_object() => value,
            _ => {
                let invalid = state_dir.join("config.invalid.json");
                if invalid.exists() {
                    fs::remove_file(&invalid).map_err(|error| error.to_string())?;
                }
                fs::rename(&config_path, invalid).map_err(|error| error.to_string())?;
                serde_json::json!({})
            }
        }
    } else {
        serde_json::json!({})
    };
    let object = config
        .as_object_mut()
        .ok_or_else(|| "Bok config must be a JSON object".to_string())?;
    object.entry("local_only").or_insert(serde_json::json!(true));
    object.entry("provider").or_insert(serde_json::json!("auto"));
    object
        .entry("embedding_provider")
        .or_insert(serde_json::json!("none"));
    if object
        .get("personal_core_root")
        .and_then(|value| value.as_str())
        .unwrap_or("")
        .is_empty()
    {
        object.insert(
            "personal_core_root".to_string(),
            serde_json::json!(personal_core.to_string_lossy()),
        );
    }
    let temporary = state_dir.join("config.json.incoming");
    let rendered = serde_json::to_vec_pretty(&config).map_err(|error| error.to_string())?;
    fs::write(&temporary, rendered).map_err(|error| error.to_string())?;
    replace_file(&temporary, &config_path)?;
    Ok(())
}

fn backend_arguments(
    ui_root: &Path,
    vault_root: &Path,
    bok_root: &Path,
    ready_file: &Path,
    control_dir: &Path,
) -> Vec<String> {
    vec![
        "--server-only".into(),
        "0".into(),
        "--ready-file".into(),
        ready_file.to_string_lossy().into_owned(),
        "--ui-root".into(),
        ui_root.to_string_lossy().into_owned(),
        "--vault-root".into(),
        vault_root.to_string_lossy().into_owned(),
        "--bok-package-root".into(),
        bok_root.to_string_lossy().into_owned(),
        "--native-control-dir".into(),
        control_dir.to_string_lossy().into_owned(),
        "--idle-timeout".into(),
        "0".into(),
        "--parent-pid".into(),
        std::process::id().to_string(),
    ]
}

#[cfg(target_os = "macos")]
fn start_backend(
    app: &tauri::App,
    arguments: &[String],
    _resource_root: &Path,
    backend_exit: Arc<Mutex<Option<String>>>,
) -> Result<BackendChild, String> {
    let command = app
        .shell()
        .sidecar("bok-preview")
        .map_err(|error| error.to_string())?
        .args(arguments);
    let (mut events, child) = command.spawn().map_err(|error| error.to_string())?;
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = events.recv().await {
            let message = match event {
                CommandEvent::Terminated(status) => Some(format!(
                    "本地服务已退出（退出码：{:?}）。可以点击重试重新启动。", status.code
                )),
                // A pipe-read error is not proof that the process exited.
                // Continue probing readiness until an actual termination.
                _ => None, // Never persist stdout/stderr containing local paths or memory.
            };
            if let Some(message) = message {
                if let Ok(mut exited) = backend_exit.lock() { *exited = Some(message); }
            }
        }
    });
    Ok(child)
}

#[cfg(target_os = "windows")]
fn start_backend(
    _app: &tauri::App,
    arguments: &[String],
    resource_root: &Path,
    _backend_exit: Arc<Mutex<Option<String>>>,
) -> Result<BackendChild, String> {
    use std::os::windows::process::CommandExt;
    use std::process::{Command, Stdio};
    use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;

    let python = resource_root.join("resources/windows-python/pythonw.exe");
    let script = resource_root.join("resources/windows-source/web_preview.pyw");
    if !python.is_file() || !script.is_file() {
        return Err("Bundled Windows Python runtime is incomplete".into());
    }
    Command::new(python)
        .arg(script)
        .args(arguments)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|error| error.to_string())
}

#[cfg(target_os = "macos")]
fn stop_child(child: BackendChild) {
    let _ = child.kill();
}

#[cfg(target_os = "windows")]
fn stop_child(mut child: BackendChild) {
    let _ = child.kill();
    let _ = child.wait();
}

fn show_startup_error(app: &tauri::AppHandle, message: &str) {
    show_startup_feedback(app, message, true);
}

fn show_startup_progress(app: &tauri::AppHandle, message: &str) {
    show_startup_feedback(app, message, false);
}

fn show_startup_feedback(app: &tauri::AppHandle, message: &str, error: bool) {
    let Some(state) = app.try_state::<RuntimeState>() else { return; };
    let Ok(mut feedback) = state.startup_feedback.lock() else { return; };
    let revision = feedback.as_ref().and_then(|value| value["revision"].as_u64()).unwrap_or(0) + 1;
    let value = serde_json::json!({"message": message, "error": error, "revision": revision});
    *feedback = Some(value.clone());
    drop(feedback);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.eval(&format!("window.showStartupStatus?.({value});"));
    }
}

fn require_loading_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    let url = window.url().map_err(|error| error.to_string())?;
    if window.label() != "main" || url.port().is_some()
        || !startup::loading_origin(url.scheme(), url.host_str(), url.path()) {
        return Err("此操作只允许从 Bok 本地启动页面调用。".into());
    }
    Ok(())
}

#[tauri::command]
fn startup_status(window: tauri::WebviewWindow, state: State<'_, RuntimeState>) -> Result<Option<serde_json::Value>, String> {
    require_loading_window(&window)?;
    state.startup_feedback.lock().map(|value| value.clone()).map_err(|_| "启动状态暂不可用。".into())
}

#[tauri::command]
fn retry_startup(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    require_loading_window(&window)?;
    if let Some(state) = app.try_state::<RuntimeState>() {
        if state.stop.swap(true, Ordering::Relaxed) { return Ok(()); }
    }
    stop_runtime(&app);
    app.request_restart();
    Ok(())
}

fn open_quick_note(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("quick-note") {
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    if let Ok(window) = WebviewWindowBuilder::new(
        app,
        "quick-note",
        WebviewUrl::App("quick-note.html".into()),
    )
    .title("Bok 随手记")
    .inner_size(420.0, 560.0)
    .min_inner_size(360.0, 460.0)
    .center()
    .resizable(true)
    .always_on_top(true)
    .build()
    {
        let _ = window.set_focus();
    }
}

fn loopback_port(server_url: &str) -> Result<u16, String> {
    server_url
        .strip_prefix("http://127.0.0.1:")
        .and_then(|value| value.strip_suffix('/'))
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| "Bok 本地服务地址无效。".to_string())
}

fn current_server_url(state: &RuntimeState) -> Result<String, String> {
    state
        .server_url
        .lock()
        .map_err(|_| "Bok 本地服务状态不可用。".to_string())?
        .clone()
        .ok_or_else(|| "Bok 本地服务仍在启动，请稍后重试。".to_string())
}

fn probe_loopback(server_url: &str) -> Result<(), String> {
    let port = loopback_port(server_url)?;
    let address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let mut stream = TcpStream::connect_timeout(&address.into(), Duration::from_secs(2))
        .map_err(|_| "Bok 本地服务尚未连接。".to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .map_err(|error| error.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|error| error.to_string())?;
    let request = format!(
        "GET /api/heartbeat HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .and_then(|_| stream.flush())
        .map_err(|_| "Bok 本地服务没有响应。".to_string())?;
    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|_| "Bok 本地服务没有完成心跳响应。".to_string())?;
    if heartbeat_ready(&response) {
        Ok(())
    } else {
        Err("Bok 本地服务尚未就绪。".to_string())
    }
}

fn heartbeat_ready(response: &[u8]) -> bool {
    let response = String::from_utf8_lossy(response);
    let Some((headers, body)) = response.split_once("\r\n\r\n") else { return false; };
    if headers.lines().next().unwrap_or_default().split_whitespace().nth(1) != Some("200") {
        return false;
    }
    serde_json::from_str::<serde_json::Value>(body).ok().map_or(false, |value| {
        value["ready"] == true && value["service"] == "boujoy-knowledge-preview"
    })
}

fn require_quick_note_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() == "quick-note" {
        Ok(())
    } else {
        Err("此操作只允许从 Bok 随手记窗口调用。".to_string())
    }
}

fn post_quick_note(server_url: &str, text: &str) -> Result<(), String> {
    let port = loopback_port(server_url)?;
    let address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let mut stream = TcpStream::connect_timeout(&address.into(), Duration::from_secs(3))
        .map_err(|_| "无法连接 Bok 本地服务；草稿仍保留在本机。".to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|error| error.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .map_err(|error| error.to_string())?;

    let body = serde_json::to_vec(&serde_json::json!({
        "text": text,
        "source": "boujoy-native-quick-note",
    }))
    .map_err(|error| error.to_string())?;
    let idempotency = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let origin = server_url.trim_end_matches('/');
    let request = format!(
        "POST /api/bok/v1/quick-notes HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: {origin}\r\nReferer: {server_url}\r\nSec-Fetch-Site: same-origin\r\nAccept: application/json\r\nContent-Type: application/json\r\nIdempotency-Key: native-quick-note-{idempotency}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream
        .write_all(request.as_bytes())
        .and_then(|_| stream.write_all(&body))
        .and_then(|_| stream.flush())
        .map_err(|_| "随手记保存请求发送失败；草稿仍保留在本机。".to_string())?;

    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|_| "Bok 本地服务没有完成响应；草稿仍保留在本机。".to_string())?;
    let status = String::from_utf8_lossy(&response)
        .lines()
        .next()
        .unwrap_or_default()
        .to_string();
    if status.contains(" 200 ") || status.contains(" 201 ") || status.contains(" 202 ") {
        Ok(())
    } else {
        Err("Bok 没有保存这条随手记；草稿仍保留在本机。".to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::{heartbeat_ready, loopback_port, post_quick_note, probe_loopback};
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    #[test]
    fn loopback_port_rejects_non_local_urls() {
        assert_eq!(loopback_port("http://127.0.0.1:8765/").unwrap(), 8765);
        assert!(loopback_port("https://example.com/").is_err());
        assert!(loopback_port("http://localhost:8765/").is_err());
    }

    #[test]
    fn native_quick_note_uses_same_origin_json_request() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut chunk = [0_u8; 4096];
            loop {
                let length = stream.read(&mut chunk).unwrap();
                if length == 0 {
                    break;
                }
                request.extend_from_slice(&chunk[..length]);
                let marker = b"boujoy-native-quick-note";
                if request.windows(marker.len()).any(|window| window == marker) {
                    break;
                }
            }
            let request = String::from_utf8_lossy(&request);
            assert!(request.starts_with("POST /api/bok/v1/quick-notes HTTP/1.1\r\n"));
            assert!(request.contains(&format!("Origin: http://127.0.0.1:{port}\r\n")));
            assert!(request.contains("Sec-Fetch-Site: same-origin\r\n"));
            assert!(request.contains("\"source\":\"boujoy-native-quick-note\""));
            assert!(request.contains("\"text\":\"测试随手记\""));
            stream
                .write_all(
                    b"HTTP/1.1 201 Created\r\nContent-Type: application/json\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{\"ok\":true}",
                )
                .unwrap();
        });

        post_quick_note(&format!("http://127.0.0.1:{port}/"), "测试随手记").unwrap();
        server.join().unwrap();
    }

    #[test]
    fn native_quick_note_status_checks_real_heartbeat() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 1024];
            let length = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..length]);
            assert!(request.starts_with("GET /api/heartbeat HTTP/1.1\r\n"));
            let body = r#"{"service":"boujoy-knowledge-preview","ready":true}"#;
            let response = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
            stream.write_all(response.as_bytes()).unwrap();
        });

        probe_loopback(&format!("http://127.0.0.1:{port}/")).unwrap();
        server.join().unwrap();
    }

    #[test]
    fn native_quick_note_status_rejects_unhealthy_backend() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 1024];
            let _ = stream.read(&mut request).unwrap();
            stream
                .write_all(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                )
                .unwrap();
        });

        assert!(probe_loopback(&format!("http://127.0.0.1:{port}/")).is_err());
        server.join().unwrap();
    }

    #[test]
    fn heartbeat_requires_the_expected_service_to_be_ready() {
        for body in [
            r#"{"service":"boujoy-knowledge-preview","ready":false}"#,
            r#"{"service":"unrelated-service","ready":true}"#,
            r#"{"ok":true}"#,
            "not JSON",
        ] {
            assert!(!heartbeat_ready(format!("HTTP/1.1 200 OK\r\n\r\n{body}").as_bytes()));
        }
        assert!(heartbeat_ready(b"HTTP/1.1 200 OK\r\n\r\n{\"service\":\"boujoy-knowledge-preview\",\"ready\":true}"));
    }
}

#[tauri::command]
fn quick_note_status(
    window: tauri::WebviewWindow,
    state: State<'_, RuntimeState>,
) -> Result<bool, String> {
    require_quick_note_window(&window)?;
    let server_url = current_server_url(&state)?;
    probe_loopback(&server_url)?;
    Ok(true)
}

#[tauri::command]
fn quick_note_save(
    window: tauri::WebviewWindow,
    state: State<'_, RuntimeState>,
    text: String,
) -> Result<(), String> {
    require_quick_note_window(&window)?;
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Err("随手记内容不能为空。".to_string());
    }
    if trimmed.chars().count() > 20_000 {
        return Err("随手记不能超过 20000 个字符。".to_string());
    }
    post_quick_note(&current_server_url(&state)?, trimmed)
}

fn show_vault_selection_error(app: &tauri::AppHandle, message: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let text = format!("无法切换知识库：{message}");
        let encoded = serde_json::to_string(&text).unwrap_or_else(|_| "\"无法切换知识库\"".into());
        let _ = window.eval(&format!("window.showNativeVaultError?.({encoded});"));
    }
}

fn select_vault(app: &tauri::AppHandle, data_root: &Path) {
    let selected = app.dialog().file().blocking_pick_folder();
    let Some(selected) = selected else {
        return;
    };
    let selected = match selected.into_path() {
        Ok(path) => path,
        Err(error) => {
            show_vault_selection_error(app, &error.to_string());
            return;
        }
    };
    match persist_selected_vault(data_root, &selected) {
        Ok(_) => {
            stop_runtime(app);
            app.request_restart();
        }
        Err(error) => show_vault_selection_error(app, &error),
    }
}

fn watch_native_requests(
    app: tauri::AppHandle,
    control_dir: PathBuf,
    data_root: PathBuf,
    stop: Arc<AtomicBool>,
) {
    thread::spawn(move || {
        let quick_note_request = control_dir.join("open-quick-note.request");
        let select_vault_request = control_dir.join("select-vault.request");
        while !stop.load(Ordering::Relaxed) {
            if quick_note_request.is_file() {
                let _ = fs::remove_file(&quick_note_request);
                open_quick_note(&app);
            }
            if select_vault_request.is_file() {
                let _ = fs::remove_file(&select_vault_request);
                select_vault(&app, &data_root);
            }
            thread::sleep(Duration::from_millis(120));
        }
    });
}

fn finish_startup(
    app: tauri::AppHandle,
    ready_file: PathBuf,
    control_dir: PathBuf,
    data_root: PathBuf,
    stop: Arc<AtomicBool>,
) {
    thread::spawn(move || {
        let started = Instant::now();
        let mut waiting = StartupWait::default();
        while !stop.load(Ordering::Relaxed) {
            let mut exited = backend_exit_message(&app);
            let ready_url = if exited.is_none() {
                fs::read_to_string(&ready_file).ok().and_then(|raw| {
                    let url = raw.trim().to_string();
                    probe_loopback(&url).ok().map(|_| url)
                })
            } else { None };
            // A heartbeat may block while the user retries or the child exits.
            // Re-read both signals before allowing the old worker to navigate.
            exited = backend_exit_message(&app).or(exited);
            match waiting.next(started.elapsed(), stop.load(Ordering::Relaxed), exited.is_some(), ready_url.is_some()) {
                StartupAction::Stop => return,
                StartupAction::BackendExited => {
                    show_startup_error(&app, exited.as_deref().unwrap_or("本地服务已退出，请点击重试。"));
                    return;
                }
                StartupAction::Navigate => {
                    let url = ready_url.expect("Ready action requires a URL");
                    if let (Some(window), Ok(parsed)) =
                        (app.get_webview_window("main"), url.parse())
                    {
                        if window.navigate(parsed).is_ok() {
                            if let Some(state) = app.try_state::<RuntimeState>() {
                                if let Ok(mut server_url) = state.server_url.lock() {
                                    *server_url = Some(url.clone());
                                }
                            }
                            watch_native_requests(
                                app.clone(),
                                control_dir,
                                data_root,
                                stop,
                            );
                            return;
                        }
                    }
                }
                StartupAction::SlowNotice => show_startup_progress(&app, "本地服务启动比平时慢，正在继续等待。就绪后会自动打开知识库。"),
                StartupAction::RetryNotice => show_startup_error(&app, "本地服务仍未就绪。可以继续等待或点击重试；知识库内容不会被覆盖。"),
                StartupAction::Wait => {}
            }
            thread::sleep(Duration::from_millis(120));
        }
    });
}

fn backend_exit_message(app: &tauri::AppHandle) -> Option<String> {
    let state = app.try_state::<RuntimeState>()?;
    #[cfg(target_os = "windows")]
    if let Ok(mut child) = state.child.lock() {
        if let Some(child) = child.as_mut() {
            if let Ok(Some(status)) = child.try_wait() {
                if let Ok(mut exited) = state.backend_exit.lock() {
                    *exited = Some(format!("本地服务已退出（{status}），请点击重试。"));
                }
            }
        }
    }
    let message = state.backend_exit.lock().ok()?.clone();
    message
}

fn stop_runtime(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<RuntimeState>() {
        state.stop.store(true, Ordering::Relaxed);
        if let Ok(mut guard) = state.child.lock() {
            if let Some(child) = guard.take() {
                stop_child(child);
            }
        }
    }
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
compile_error!("Bok Desktop currently supports macOS and Windows only");

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![quick_note_status, quick_note_save, retry_startup, startup_status])
        .on_window_event(|window, event| {
            if window.label() == "main" && matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                stop_runtime(window.app_handle());
                window.app_handle().exit(0);
            }
        })
        .setup(|app| {
            let resource_root = app.path().resource_dir().map_err(|error| error.to_string())?;
            let data_root = app.path().app_local_data_dir().map_err(|error| error.to_string())?;
            let ui_root = resource_root.join("resources/ui");
            let starter = resource_root.join("resources/starter-vault");
            let (vault, _personal_core) = ensure_user_workspace(&starter, &data_root)?;
            let control_dir = data_root.join("runtime-control");
            fs::create_dir_all(&control_dir).map_err(|error| error.to_string())?;
            let ready_file = control_dir.join("preview-ready.txt");
            let _ = fs::remove_file(&ready_file);
            let _ = fs::remove_file(control_dir.join("open-quick-note.request"));
            let _ = fs::remove_file(control_dir.join("select-vault.request"));

            #[cfg(target_os = "macos")]
            let bok_root = resource_root.clone();
            #[cfg(target_os = "windows")]
            let bok_root = resource_root.join("resources/windows-source");
            let arguments = backend_arguments(
                &ui_root,
                &vault,
                &bok_root,
                &ready_file,
                &control_dir,
            );
            let backend_exit = Arc::new(Mutex::new(None));
            let child = start_backend(app, &arguments, &resource_root, backend_exit.clone())?;
            let stop = Arc::new(AtomicBool::new(false));
            app.manage(RuntimeState {
                child: Mutex::new(Some(child)),
                server_url: Mutex::new(None),
                stop: stop.clone(),
                backend_exit,
                startup_feedback: Mutex::new(None),
            });
            finish_startup(
                app.handle().clone(),
                ready_file,
                control_dir,
                data_root,
                stop,
            );
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Bok Desktop");

    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }) {
            stop_runtime(handle);
        }
    });
}
