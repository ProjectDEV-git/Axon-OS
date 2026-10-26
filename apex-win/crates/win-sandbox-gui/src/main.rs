#![allow(dead_code)]

mod config;
mod ipc;
mod ui;

use anyhow::Result;
use gtk::prelude::*;
use gtk::{self, glib};
use std::sync::{Arc, Mutex};
use tracing::{debug, info};

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::new("info"))
        .init();

    info!("win-sandbox-gui starting");

    // Initialize GTK4
    let app = gtk::Application::builder()
        .application_id("org.wine.SandboxRunner")
        .build();

    // Shared IPC request queue
    let ipc_queue: Arc<Mutex<Vec<ipc::IpcRequest>>> = Arc::new(Mutex::new(Vec::new()));
    let ipc_queue_clone = ipc_queue.clone();

    // Start IPC listener in background thread
    let socket_path = ipc::DEFAULT_SOCKET_PATH;
    match ipc::start_unix_listener(socket_path) {
        Ok(ipc_rx) => {
            info!("IPC listener started on {socket_path}");
            std::thread::Builder::new()
                .name("ipc-queue-filler".into())
                .spawn(move || {
                    for request in ipc_rx.iter() {
                        if let Ok(mut queue) = ipc_queue_clone.lock() {
                            queue.push(request);
                        }
                    }
                })
                .ok();
        }
        Err(e) => {
            tracing::error!("Failed to start IPC listener: {e}");
        }
    }

    // Check for --demo flag
    let demo_mode = std::env::args().any(|a| a == "--demo");

    app.connect_activate(move |app| {
        // Create a hidden main window
        let window = gtk::ApplicationWindow::builder()
            .application(app)
            .title("win-sandbox-runner")
            .default_width(1)
            .default_height(1)
            .build();

        // Use a glib timeout to poll the IPC queue every 100ms
        let queue = ipc_queue.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
            let requests: Vec<ipc::IpcRequest> = {
                let mut guard = queue.lock().unwrap_or_else(|e| e.into_inner());
                guard.drain(..).collect()
            };

            for request in requests {
                handle_ipc_request(request);
            }

            glib::ControlFlow::Continue
        });

        if demo_mode {
            info!("Demo mode — showing confirm dialog");
            // Show demo dialog after a short delay so the window is realized
            glib::timeout_add_local_once(std::time::Duration::from_millis(100), move || {
                let result = ui::confirm_dialog::show(
                    "notepad.exe",
                    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    "/home/user/.wine/drive_c/windows/notepad.exe",
                );
                info!("Demo result: {result:?}");
            });
        }

        window.present();
    });

    app.run_with_args::<String>(&[]);

    Ok(())
}

/// Handle an incoming IPC request by showing the appropriate dialog.
fn handle_ipc_request(request: ipc::IpcRequest) {
    debug!("Handling IPC request: {:?}", request.message);

    let response = match request.message {
        win_sandbox_common::message::IpcMessage::ConfirmRequest { hash, name, path } => {
            let result = ui::confirm_dialog::show(&name, &hash, &path);
            win_sandbox_common::message::IpcMessage::ConfirmResponse {
                tier: result.tier.level(),
                remember: result.remember,
            }
        }
        win_sandbox_common::message::IpcMessage::TrustRequest {
            hash,
            name,
            path,
            suggested_tier,
        } => {
            let result = ui::trust_dialog::show(&name, &hash, &path, suggested_tier);
            win_sandbox_common::message::IpcMessage::TrustResponse {
                tier: result.tier,
                network: result.network,
                gpu: result.gpu,
                remember: result.remember,
            }
        }
        win_sandbox_common::message::IpcMessage::Ping => {
            win_sandbox_common::message::IpcMessage::Pong
        }
        win_sandbox_common::message::IpcMessage::SetupProgress {
            name,
            step,
            progress,
        } => {
            info!("Setup progress: {name} — {step} ({progress:.0}%)");
            // In a full implementation, this would update a visible progress dialog.
            // For now, log it. The dialog module is ready at ui::setup_progress::show().
            return;
        }
        win_sandbox_common::message::IpcMessage::SetupComplete {
            name,
            success,
            summary,
        } => {
            info!("Setup complete: {name} — success={success} — {summary}");
            return;
        }
        _ => {
            debug!("Ignoring non-request IPC message");
            return;
        }
    };

    if let Err(e) = request.respond_tx.send(response) {
        tracing::error!("Failed to send IPC response: {e}");
    }
}
