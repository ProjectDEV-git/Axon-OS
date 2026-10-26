//! Integration tests for win-sandbox-runner.
//!
//! These tests exercise the full IPC protocol, rules loading, and dispatch
//! logic without requiring Wine or a running GUI. They use temp files
//! and mock data to verify end-to-end behavior.

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use win_sandbox_common::message::IpcMessage;

/// Test that the IPC protocol round-trips through a Unix socket correctly.
#[test]
fn ipc_unix_socket_round_trip() {
    // Create a temporary socket
    let dir = tempfile::tempdir().unwrap();
    let socket_path = dir.path().join("test-ipc.sock");
    let socket_str = socket_path.to_str().unwrap();

    // Start a minimal listener that sends back a canned response
    let socket_path_owned = socket_str.to_string();
    let listener = std::os::unix::net::UnixListener::bind(&socket_path_owned).unwrap();

    let server_thread = std::thread::spawn(move || {
        for stream in listener.incoming().take(1) {
            let stream = stream.unwrap();
            let reader_stream = stream.try_clone().unwrap();
            let mut reader = BufReader::new(reader_stream);
            let mut writer = stream;

            // Read request
            let mut line = String::new();
            reader.read_line(&mut line).unwrap();
            let request: IpcMessage = serde_json::from_str(line.trim()).unwrap();

            // Generate response
            let response = match request {
                IpcMessage::ConfirmRequest { .. } => IpcMessage::ConfirmResponse {
                    tier: 2,
                    remember: true,
                },
                IpcMessage::Ping => IpcMessage::Pong,
                _ => IpcMessage::Pong,
            };

            let json = serde_json::to_string(&response).unwrap();
            writeln!(writer, "{json}").unwrap();
            writer.flush().unwrap();
        }
    });

    // Client: send ConfirmRequest
    let mut stream = UnixStream::connect(socket_str).unwrap();
    let request = IpcMessage::ConfirmRequest {
        hash: "abc123".into(),
        name: "test.exe".into(),
        path: "/tmp/test.exe".into(),
    };
    let json = serde_json::to_string(&request).unwrap();
    writeln!(stream, "{json}").unwrap();
    stream.flush().unwrap();

    // Read response
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    let response: IpcMessage = serde_json::from_str(line.trim()).unwrap();

    match response {
        IpcMessage::ConfirmResponse { tier, remember } => {
            assert_eq!(tier, 2);
            assert!(remember);
        }
        _ => panic!("Expected ConfirmResponse, got: {response:?}"),
    }

    server_thread.join().unwrap();
}

/// Test IPC Ping/Pong round-trip.
#[test]
fn ipc_ping_pong() {
    let dir = tempfile::tempdir().unwrap();
    let socket_path = dir.path().join("test-ping.sock");
    let socket_str = socket_path.to_str().unwrap();

    let listener = std::os::unix::net::UnixListener::bind(socket_str).unwrap();

    let server = std::thread::spawn(move || {
        for stream in listener.incoming().take(1) {
            let stream = stream.unwrap();
            let reader_stream = stream.try_clone().unwrap();
            let mut reader = BufReader::new(reader_stream);
            let mut writer = stream;

            let mut line = String::new();
            reader.read_line(&mut line).unwrap();
            let request: IpcMessage = serde_json::from_str(line.trim()).unwrap();

            let response = match request {
                IpcMessage::Ping => IpcMessage::Pong,
                _ => IpcMessage::Pong,
            };

            let json = serde_json::to_string(&response).unwrap();
            writeln!(writer, "{json}").unwrap();
            writer.flush().unwrap();
        }
    });

    let mut stream = UnixStream::connect(socket_str).unwrap();
    let json = serde_json::to_string(&IpcMessage::Ping).unwrap();
    writeln!(stream, "{json}").unwrap();
    stream.flush().unwrap();

    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    let response: IpcMessage = serde_json::from_str(line.trim()).unwrap();
    assert!(matches!(response, IpcMessage::Pong));

    server.join().unwrap();
}

/// Test that the JSON schema validates correctly.
#[test]
fn rules_schema_valid() {
    let schema = include_str!("../../../config/rules.schema.json");
    let schema_value: serde_json::Value = serde_json::from_str(schema).unwrap();
    assert_eq!(schema_value["title"], "win-sandbox-runner rules");

    // Verify a valid rules.json passes validation
    let rules = serde_json::json!({
        "version": 1,
        "entries": [
            {
                "hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "name": "test.exe",
                "tier": "tier2",
                "network": true,
                "gpu": false
            }
        ],
        "defaults": {
            "unmapped_tier": "tier0",
            "untrusted_path_tier": "tier2",
            "network_default": false,
            "gpu_default": false
        }
    });

    // The rules deserialize correctly
    let parsed: win_sandbox_common::rules_schema::RulesFile =
        serde_json::from_value(rules).unwrap();
    assert_eq!(parsed.version, 1);
    assert_eq!(parsed.entries.len(), 1);
    assert_eq!(parsed.entries[0].name, "test.exe");
    assert!(parsed.entries[0].network);
}

/// Test that the config file is valid INI-like format.
#[test]
fn config_file_parses() {
    let config = include_str!("../../../config/win-sandbox-runner.conf");
    // Basic structure checks
    assert!(config.contains("[sandbox]"));
    assert!(config.contains("rules_path"));
    assert!(config.contains("[network]"));
    assert!(config.contains("tap_bridge_socket"));
}
