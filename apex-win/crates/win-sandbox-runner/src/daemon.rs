//! Background daemon for seamless .exe execution.
//!
//! The daemon pre-loads the app database, rules, and prefix manager into memory
//! and registers a binfmt_misc handler. When a .exe is launched, the kernel
//! runs the handler, which hands the daemon an open fd of the executable over
//! a Unix socket (`launch.sock`, see [`crate::launch`]). The daemon learns the
//! caller's uid from the kernel (`SO_PEERCRED`), never from the message.
//!
//! An IPC Unix socket allows runtime control: trust apps, reload rules, query status.

use anyhow::{bail, Context, Result};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;
use tracing::{debug, error, info, warn};

use crate::{appdb, config, dispatch, hasher, launch, netopt, rules, Args};
use win_sandbox_common::rules_schema::RulesFile;

/// Runtime state cached by the daemon to avoid re-loading on every .exe launch.
pub struct DaemonState {
    pub app_db: appdb::AppDatabase,
    pub rules: RulesFile,
    #[allow(dead_code)]
    pub config: crate::config::Config,
    pub net_config: netopt::NetOptimizerConfig,
    /// Host sandbox capabilities (Landlock ABI, bwrap version, overlay
    /// support), probed once at startup. `--status` reports this so "is
    /// Tier 3 real on this machine" is answerable without reading source or
    /// grepping the journal — the same question dispatch's fail-secure Tier 3
    /// check answers for itself on every explicit Tier 3 launch.
    pub capabilities: crate::capabilities::Capabilities,
    /// Track how many .exe launches we've handled.
    pub launch_count: u64,
    /// Track per-exe launch history (exe path -> count).
    pub launch_history: HashMap<String, u64>,
}

impl DaemonState {
    /// Load all state from disk.
    pub fn load() -> Result<Self> {
        let app_db = appdb::AppDatabase::load_embedded();
        let rules_path = config::find_rules_path(None);
        let rules = rules::load_rules(rules_path.as_deref())?;
        // No PrefixManager here on purpose: it must be built per-launch from the
        // FIFO-forwarded user env (PrefixManager::for_user), because the daemon
        // runs as root and its own HOME is /root or unset.
        let config = crate::config::load_config(None);
        let net_config = netopt::load_config(None);
        let capabilities = crate::capabilities::Capabilities::detect();

        info!(
            "Daemon state loaded: {} app profiles, {} rules",
            app_db.profiles.len(),
            rules.entries.len()
        );
        info!(
            "Sandbox capabilities: landlock_abi={:?} bwrap={:?} tier3_available={}",
            capabilities.landlock_abi,
            capabilities.bwrap_version,
            capabilities.unprivileged_overlay
        );

        Ok(Self {
            app_db,
            rules,
            config,
            net_config,
            capabilities,
            launch_count: 0,
            launch_history: HashMap::new(),
        })
    }

    /// Reload rules from disk (user may have edited rules.json).
    pub fn reload_rules(&mut self) -> Result<()> {
        let rules_path = config::find_rules_path(None);
        self.rules = rules::load_rules(rules_path.as_deref())?;
        info!("Rules reloaded: {} entries", self.rules.entries.len());
        Ok(())
    }

    /// Reload the network optimizer config from disk.
    pub fn reload_net_config(&mut self) {
        self.net_config = netopt::load_config(None);
        info!("Network config reloaded");
    }
}

/// Base directory for daemon runtime state (FIFO, IPC socket, PID file).
///
/// Linux: `/run` is the systemd/FHS convention — the unit file's
/// `RuntimeDirectory=win-sandbox-runner` (see
/// `scripts/win-sandbox-runner.service`) creates this exact path before
/// `ExecStart` runs, so this string must stay in sync with that unit file.
/// The Linux daemon runs as root (`run_daemon()` enforces this) because it
/// needs to register a binfmt_misc handler in `/proc/sys/fs/binfmt_misc`,
/// which requires root, and `/run` itself is root-owned.
///
/// macOS: there is no binfmt_misc, and therefore nothing that requires the
/// daemon to run as root — see the platform split in `run_daemon()` below.
/// It runs as a per-user `launchd` LaunchAgent instead (mirroring the
/// architecture doc's Path A: the file's owner runs Wine directly, nothing
/// executes as root). `/var/run` (root-owned, mode 0755) would therefore
/// EACCES for a plain user process, so the runtime dir lives under the
/// user's own `$TMPDIR` instead — the macOS analogue of Linux's
/// `XDG_RUNTIME_DIR`, and already private per-user (mode 0700, set by the
/// OS at login).
#[cfg(target_os = "linux")]
const RUNTIME_DIR_BASE: &str = "/run/win-sandbox-runner";

/// Runtime directory base for macOS: `$TMPDIR/win-sandbox-runner`.
///
/// `$TMPDIR` on macOS is a per-user, private directory (mode 0700, created
/// by the OS at login), which is what makes it safe to host a FIFO and an
/// IPC socket that control process launches.
///
/// There is deliberately NO fallback to `/tmp`. An earlier version of this
/// function fell back to `/tmp` when `$TMPDIR` was unset, which was wrong:
/// `/tmp` is mode 1777 and shared by every user on the machine, so the
/// daemon's FIFO (created 0666) and IPC socket would have been reachable by
/// any local user, and the containing directory would have been a symlink-
/// swap target. `$TMPDIR`'s 0700 parent is precisely the property that
/// makes the 1777 mode set in `ensure_runtime_dir` harmless; `/tmp` has no
/// such parent, so the same mode there is a real exposure rather than a
/// cosmetic one. Failing loudly is correct: launchd always sets `$TMPDIR`,
/// so an unset value means something is wrong with the environment, and
/// silently downgrading to a world-writable location is not a safe
/// interpretation of "something is wrong".
#[cfg(not(target_os = "linux"))]
fn runtime_dir_base_checked() -> Result<PathBuf> {
    let tmp = std::env::var("TMPDIR")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .context(
            "TMPDIR is not set. The macOS daemon stores its FIFO and IPC socket there \
             because it is private to your user (mode 0700). Refusing to fall back to \
             /tmp, which every user on this machine can write to. launchd always sets \
             TMPDIR; if you are running the daemon by hand, set it first.",
        )?;
    // TMPDIR on macOS is typically already-slash-terminated
    // (e.g. "/var/folders/.../T/"); trim so join() doesn't produce "//".
    Ok(PathBuf::from(tmp.trim_end_matches('/')).join("win-sandbox-runner"))
}

#[cfg(target_os = "linux")]
fn runtime_dir_base_checked() -> Result<PathBuf> {
    Ok(PathBuf::from(RUNTIME_DIR_BASE))
}

/// Infallible view of [`runtime_dir_base_checked`], for the paths that only
/// build a path string and cannot meaningfully fail (`--status`/`--stop`
/// clients, cleanup on shutdown). If `$TMPDIR` is missing on macOS this
/// yields a path under a non-existent-by-design directory, so those callers
/// simply find no daemon, which is the correct outcome: without `$TMPDIR`
/// the daemon refused to start in the first place.
fn runtime_dir_base() -> PathBuf {
    runtime_dir_base_checked().unwrap_or_else(|_| {
        // Not a usable runtime dir, and deliberately not /tmp: a path that
        // cannot exist is safer than one every local user can write to.
        PathBuf::from("/nonexistent/win-sandbox-runner")
    })
}

/// Paths used by the daemon.
///
/// `launch.sock` replaced the old `fifo`: the binfmt handler connects, passes
/// an open fd of the executable, and the daemon learns the caller's uid from
/// the kernel instead of trusting a `UID:` line.
pub(crate) fn launch_socket_path() -> PathBuf {
    runtime_dir_base().join("launch.sock")
}

fn socket_path() -> PathBuf {
    runtime_dir_base().join("ipc.sock")
}

fn pid_path() -> PathBuf {
    runtime_dir_base().join("daemon.pid")
}

/// Ensure the runtime directory exists, is a real directory owned by us, and
/// has the right mode.
///
/// Linux: 0755. Users only need to *connect* to the sockets inside (socket
/// permissions govern that), not create files, so the old 1777 (which let
/// any user pre-create `fifo`/`ipc.sock`/`daemon.pid` squatters or symlinks
/// before the daemon started) is gone. macOS: 0700, the daemon and its only
/// client are the same user.
fn ensure_runtime_dir() -> Result<()> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let dir = runtime_dir_base_checked()?;
    match std::fs::symlink_metadata(&dir) {
        Ok(m) => {
            if !m.file_type().is_dir() {
                bail!("{} exists and is not a directory", dir.display());
            }
            if m.uid() != unsafe { libc::geteuid() } {
                bail!(
                    "{} is owned by uid {}, not the daemon; refusing to use it",
                    dir.display(),
                    m.uid()
                );
            }
        }
        Err(_) => {
            std::fs::create_dir_all(&dir)
                .with_context(|| format!("Failed to create {} (are you root?)", dir.display()))?;
        }
    }
    #[cfg(target_os = "linux")]
    let mode = 0o755;
    #[cfg(not(target_os = "linux"))]
    let mode = 0o700;
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(mode))?;
    // Legacy FIFO from older versions.
    let _ = std::fs::remove_file(dir.join("fifo"));
    Ok(())
}

/// Write the daemon PID file.
fn write_pid() -> Result<()> {
    let pid = std::process::id();
    std::fs::write(pid_path(), pid.to_string())?;
    debug!("PID {pid} written to {}", pid_path().display());
    Ok(())
}

/// Remove daemon runtime files on shutdown.
fn cleanup_runtime() {
    let _ = std::fs::remove_file(launch_socket_path());
    let _ = std::fs::remove_file(socket_path());
    let _ = std::fs::remove_file(pid_path());
}

/// Bind a Unix socket at `path` with `mode`, replacing a stale one.
fn bind_socket(path: &Path, mode: u32) -> Result<UnixListener> {
    use std::os::unix::fs::PermissionsExt;
    match std::fs::symlink_metadata(path) {
        Ok(m) if m.file_type().is_dir() => bail!("{} is a directory", path.display()),
        Ok(_) => std::fs::remove_file(path)?,
        Err(_) => {}
    }
    let listener = UnixListener::bind(path).with_context(|| format!("bind {}", path.display()))?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode))?;
    Ok(listener)
}

/// Linux: any local user may connect (authorization is per-peer-uid);
/// macOS: only the owning user.
#[cfg(target_os = "linux")]
const SOCKET_MODE: u32 = 0o666;
#[cfg(not(target_os = "linux"))]
const SOCKET_MODE: u32 = 0o600;

/// Accept loop shared by the IPC and launch listeners.
fn spawn_accept_loop(
    name: &'static str,
    listener: UnixListener,
    shutdown: Arc<Mutex<bool>>,
    handler: impl Fn(UnixStream) + Send + Sync + 'static,
) -> thread::JoinHandle<()> {
    let handler = Arc::new(handler);
    thread::spawn(move || {
        listener.set_nonblocking(true).ok();
        loop {
            if *shutdown.lock().unwrap() {
                info!("{name} listener shutting down");
                break;
            }
            match listener.accept() {
                Ok((stream, _)) => {
                    stream.set_nonblocking(false).ok();
                    let h = Arc::clone(&handler);
                    thread::spawn(move || h(stream));
                }
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    thread::sleep(std::time::Duration::from_millis(100));
                }
                Err(e) => {
                    warn!("{name} accept error: {e}");
                    thread::sleep(std::time::Duration::from_millis(500));
                }
            }
        }
    })
}

/// Spawn the IPC socket listener thread.
///
/// Accepts commands over a Unix socket:
///   - "trust <exe_path>" — add a trusted rule (root only)
///   - "reload" — reload rules from disk (root / daemon user)
///   - "status" — return daemon status as JSON (anyone)
///   - "quit" — shut down the daemon (root / daemon user)
fn spawn_ipc_listener(
    state: Arc<Mutex<DaemonState>>,
    shutdown: Arc<Mutex<bool>>,
) -> Result<thread::JoinHandle<()>> {
    let path = socket_path();
    let listener = bind_socket(&path, SOCKET_MODE).context("Failed to create IPC socket")?;
    info!("IPC socket listening at {}", path.display());
    let sd = Arc::clone(&shutdown);
    Ok(spawn_accept_loop(
        "IPC",
        listener,
        shutdown,
        move |stream| {
            if let Err(e) = handle_ipc_command(stream, &state, &sd) {
                debug!("IPC command error: {e}");
            }
        },
    ))
}

/// Spawn the launch socket listener thread.
fn spawn_launch_listener(
    state: Arc<Mutex<DaemonState>>,
    shutdown: Arc<Mutex<bool>>,
) -> Result<thread::JoinHandle<()>> {
    let path = launch_socket_path();
    let listener = bind_socket(&path, SOCKET_MODE).context("Failed to create launch socket")?;
    info!("Launch socket listening at {}", path.display());
    Ok(spawn_accept_loop(
        "launch",
        listener,
        shutdown,
        move |stream| {
            serve_launch(stream, &state);
        },
    ))
}
/// The binfmt_misc registration line for the APEX-WIN handler.
///
/// This duplicates `scripts/register-binfmt.sh` on purpose: the daemon
/// registers at startup and cannot assume the script is installed. Every
/// *other* path (Makefile, install.sh, .deb postinst) must call that script
/// rather than inlining a copy.
///
/// The `\xff\xff` mask is mandatory. Without it the kernel rejects the whole
/// registration with EINVAL. That bug recurred three times while this string
/// was duplicated across five files, so `binfmt_definition_matches_script`
/// asserts this constant stays byte-identical to the script's `--print`.
///
/// Flags are `F` only. The previous `C` flag makes the kernel compute the
/// runner's credentials from the *.exe* file: a setuid .exe (owned by root
/// or by another user) would start the runner, and then Wine, with that
/// owner's euid, under the caller's environment and arguments. Nothing here
/// needs elevated credentials, so the flag could only ever grant privilege.
const BINFMT_REGISTRATION: &str =
    ":APEX-WIN:M:0:\\x4d\\x5a:\\xff\\xff:/usr/bin/win-sandbox-runner:F";

/// Register the binfmt_misc handler for .exe files.
///
/// This tells the kernel: "when someone tries to execute a .exe file,
/// run /usr/bin/win-sandbox-runner instead, passing the .exe path as an argument."
fn register_binfmt() -> Result<()> {
    let binfmt_dir = Path::new("/proc/sys/fs/binfmt_misc");
    if !binfmt_dir.exists() {
        return Err(anyhow::anyhow!(
            "binfmt_misc not available (is the kernel module loaded?)"
        ));
    }

    let register_path = binfmt_dir.join("register");

    // binfmt_misc format: :name:type:offset:magic:mask:interpreter:flags
    // - name: PE_WINE
    // - type: M (magic match)
    // - offset: 0 (check at start of file)
    // - magic: 4d5a (MZ header — PE executable signature)
    // - mask: ffff (match first 2 bytes)
    // - interpreter: /usr/bin/win-sandbox-runner
    // - flags: F (fix binary: the interpreter is opened at registration time,
    //   so it keeps working inside mount namespaces/chroots). No C, see the
    //   BINFMT_REGISTRATION doc comment.
    let registration = format!("{BINFMT_REGISTRATION}\n");

    // First, unregister if already registered
    let status_path = binfmt_dir.join("APEX-WIN");
    if status_path.exists() {
        let _ = std::fs::write(&status_path, "-1");
        debug!("Unregistered existing APEX-WIN binfmt handler");
    }

    std::fs::write(&register_path, &registration)
        .context("Failed to register binfmt handler (are you root?)")?;

    info!("binfmt_misc registered: .exe -> /usr/bin/win-sandbox-runner");
    Ok(())
}

/// Unregister the binfmt_misc handler.
pub fn unregister_binfmt() -> Result<()> {
    let status_path = Path::new("/proc/sys/fs/binfmt_misc/APEX-WIN");
    if status_path.exists() {
        std::fs::write(status_path, "-1")?;
        info!("binfmt_misc unregistered: APEX-WIN removed");
    } else {
        info!("binfmt_misc: APEX-WIN not registered (nothing to do)");
    }
    Ok(())
}

/// Build the JSON body for the `status` IPC command.
///
/// Pulled out of `handle_ipc_command` so the format is directly testable
/// without a running daemon (constructing a full `DaemonState` for a unit
/// test would need a real `AppDatabase`/`RulesFile`, which is heavier than
/// this warrants).
fn build_status_json(
    launch_count: u64,
    app_profiles: usize,
    rules_count: usize,
    caps: &crate::capabilities::Capabilities,
) -> String {
    format!(
        r#"{{"launch_count":{},"app_profiles":{},"rules":{},"uptime":"running","landlock_abi":{},"bwrap_version":{},"tier3_available":{},"seatbelt_available":{},"tier1_2_available":{}}}"#,
        launch_count,
        app_profiles,
        rules_count,
        caps.landlock_abi
            .map(|v| v.to_string())
            .unwrap_or_else(|| "null".to_string()),
        caps.bwrap_version
            .as_ref()
            .map(|v| format!("\"{v}\""))
            .unwrap_or_else(|| "null".to_string()),
        caps.unprivileged_overlay,
        caps.seatbelt_available
            .map(|v| v.to_string())
            .unwrap_or_else(|| "null".to_string()),
        caps.tier12_available(),
    )
}

/// Authorize an IPC command against the peer's kernel-reported uid.
///
/// `daemon_uid` is the uid the daemon runs as (0 under systemd on Linux).
///
/// `trust` is root/daemon-user only. Rules the daemon loads are global, so a
/// trusted hash added by one user would disable sandboxing for that binary
/// for *every* user. Unprivileged users mark their own apps trusted with
/// `--trust` on the CLI, which writes their own `~/.config` rules; the daemon
/// honors those for that user's launches only (see [`rules_for_user`]).
pub(crate) fn authorize_ipc_command(
    cmd: &str,
    peer_uid: u32,
    daemon_uid: u32,
) -> std::result::Result<(), &'static str> {
    let privileged = peer_uid == 0 || peer_uid == daemon_uid;
    if cmd == "status" {
        return Ok(());
    }
    if cmd == "quit" || cmd == "reload" || cmd.starts_with("trust ") {
        return if privileged {
            Ok(())
        } else {
            Err(
                "this command requires root (use `win-sandbox-runner --exe <file> --trust` \
                 to trust an app for your own user)",
            )
        };
    }
    // Unknown commands are rejected later with a protocol error.
    Ok(())
}

/// Maximum length of one IPC command line. `read_line` with no bound let any
/// local user grow the root daemon's memory without limit.
const MAX_IPC_LINE: u64 = 8192;

/// Handle a single IPC command.
fn handle_ipc_command(
    mut stream: UnixStream,
    state: &Arc<Mutex<DaemonState>>,
    shutdown: &Arc<Mutex<bool>>,
) -> Result<()> {
    use std::io::Read;
    stream.set_read_timeout(Some(std::time::Duration::from_secs(5)))?;
    let mut reader = BufReader::new(stream.try_clone()?.take(MAX_IPC_LINE));
    let mut line = String::new();
    reader.read_line(&mut line)?;
    let cmd = line.trim();

    debug!("IPC command: {cmd}");

    let peer = launch::peer_uid(&stream);
    let daemon_uid = unsafe { libc::geteuid() };
    if let Err(reason) = authorize_ipc_command(cmd, peer, daemon_uid) {
        launch::reply(&mut stream, &format!("ERROR: {reason}"));
        return Ok(());
    }

    let response = if cmd == "status" {
        let state = state.lock().unwrap();
        build_status_json(
            state.launch_count,
            state.app_db.profiles.len(),
            state.rules.entries.len(),
            &state.capabilities,
        )
    } else if cmd == "reload" {
        let mut state = state.lock().unwrap();
        state.reload_rules()?;
        state.reload_net_config();
        "OK: rules and config reloaded".to_string()
    } else if let Some(exe_path) = cmd.strip_prefix("trust ") {
        let exe_path = exe_path.trim();
        let hash = hasher::hash_file(exe_path)?;
        crate::save_trusted_rule(exe_path, &hash)?;
        let mut state = state.lock().unwrap();
        state.reload_rules()?;
        format!("OK: {exe_path} trusted (hash: {hash})")
    } else if cmd == "quit" {
        "OK: shutting down".to_string()
    } else {
        format!("ERROR: unknown command '{cmd}'")
    };

    launch::reply(&mut stream, response.trim_end());

    if cmd == "quit" {
        *shutdown.lock().unwrap() = true;
    }

    Ok(())
}

/// Configure a Command to switch to the given UID in the child process.
///
/// Kept for existing call sites. Resolves the user's primary gid and full
/// supplementary group list in the parent (no NSS in the forked child) and
/// fails the spawn rather than continuing as root. See [`crate::privs`].
///
/// # Safety
/// Installs a `pre_exec` hook.
pub unsafe fn configure_child_uid(cmd: &mut std::process::Command, uid: u32) {
    unsafe { crate::privs::drop_to_uid(cmd, uid) };
}

/// Per-uid cap on concurrent launches, so one local user cannot exhaust the
/// daemon's threads and block everyone else's launches.
const MAX_LAUNCHES_PER_UID: usize = 32;

static ACTIVE_LAUNCHES: Mutex<Option<HashMap<u32, usize>>> = Mutex::new(None);

struct LaunchSlot(u32);

impl LaunchSlot {
    fn acquire(uid: u32) -> Option<Self> {
        let mut g = ACTIVE_LAUNCHES.lock().unwrap_or_else(|e| e.into_inner());
        let map = g.get_or_insert_with(HashMap::new);
        let n = map.entry(uid).or_insert(0);
        if *n >= MAX_LAUNCHES_PER_UID {
            return None;
        }
        *n += 1;
        Some(Self(uid))
    }
}

impl Drop for LaunchSlot {
    fn drop(&mut self) {
        let mut g = ACTIVE_LAUNCHES.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(map) = g.as_mut() {
            if let Some(n) = map.get_mut(&self.0) {
                *n = n.saturating_sub(1);
                if *n == 0 {
                    map.remove(&self.0);
                }
            }
        }
    }
}

/// Decide whether a launch-socket peer may launch at all.
///
/// Linux (root daemon): any real user except root. Wine must never run as
/// root; root can still use `--exe` directly. Unknown peers (`u32::MAX`) are
/// refused. macOS (per-user LaunchAgent): only the daemon's own user.
pub(crate) fn authorize_launch_peer(peer: u32, daemon_uid: u32) -> std::result::Result<(), String> {
    if peer == u32::MAX {
        return Err("could not determine caller identity".into());
    }
    if daemon_uid == 0 {
        if peer == 0 {
            return Err("refusing to run Windows programs as root via the daemon; \
                        use `win-sandbox-runner --exe` directly if you really mean it"
                .into());
        }
        return Ok(());
    }
    if peer != daemon_uid {
        return Err(format!(
            "this daemon runs as uid {daemon_uid} and only serves that user"
        ));
    }
    Ok(())
}

/// Serve one connection on the launch socket.
fn serve_launch(mut stream: UnixStream, state: &Arc<Mutex<DaemonState>>) {
    let peer = launch::peer_uid(&stream);
    let result = (|| -> Result<i32> {
        if let Err(e) = authorize_launch_peer(peer, unsafe { libc::geteuid() }) {
            bail!("{e}");
        }
        let _slot = LaunchSlot::acquire(peer)
            .ok_or_else(|| anyhow::anyhow!("too many concurrent launches for this user"))?;
        let (msg, file) = launch::server_receive(&mut stream)?;
        let req = LaunchRequest::build(peer, msg, file)?;
        info!("Daemon: launch {} for uid {}", req.exe_path, peer);
        launch::reply(&mut stream, "ACCEPTED");
        handle_launch(&req, state)
    })();
    match result {
        Ok(code) => launch::reply(&mut stream, &format!("EXIT {code}")),
        Err(e) => {
            error!("Launch failed (uid {peer}): {e:#}");
            let text = format!("{e:#}").replace('\n', " ");
            launch::reply(&mut stream, &format!("ERROR: {text}"));
        }
    }
}

/// A validated launch request.
///
/// Every field is either kernel-derived (uid), daemon-derived (identity,
/// hash, passwd-based HOME/USER), or validated against that identity (env).
struct LaunchRequest {
    exe_path: String,
    uid: Option<u32>,
    args: Vec<String>,
    env: HashMap<String, String>,
    ids: Option<crate::privs::UserIds>,
    hash: String,
    identity: Option<hasher::FileIdentity>,
}

impl LaunchRequest {
    /// Validate a received message against the peer identity and the fd the
    /// peer handed us.
    fn build(uid: u32, msg: launch::LaunchMessage, file: std::fs::File) -> Result<Self> {
        let ids = crate::privs::UserIds::lookup(uid)?;

        // Hash the file the *client* opened (it had to be readable by them).
        let (hash, identity) =
            hasher::hash_open_file(&file).with_context(|| format!("hashing {}", msg.exe))?;

        // The path must name that same file, checked with the user's own
        // filesystem rights, or the client lied about which file it runs.
        {
            let _fs = crate::privs::FsIdGuard::enter(ids.uid, ids.gid)?;
            hasher::verify_unchanged(&msg.exe, Some(&identity)).with_context(|| {
                format!("{} is not the file that was passed to the daemon", msg.exe)
            })?;
        }

        let env = crate::env_sanitize::validate_daemon_env(&msg.env, &ids);
        Ok(Self {
            exe_path: msg.exe,
            uid: Some(uid),
            args: msg.args,
            env,
            ids: Some(ids),
            hash,
            identity: Some(identity),
        })
    }
}

/// Rules that apply to one user's launch: the user's own
/// `~/.config/win-sandbox/rules.json` if present and valid (read with the
/// user's filesystem rights), otherwise the daemon's global rules. A user's
/// file only ever affects that user's own launches.
fn rules_for_user(ids: &crate::privs::UserIds, global: &RulesFile) -> RulesFile {
    let path = ids.home.join(".config/win-sandbox/rules.json");
    let loaded = crate::privs::FsIdGuard::enter(ids.uid, ids.gid)
        .ok()
        .and_then(|_g| {
            if path.is_file() {
                rules::load_rules(Some(&path)).ok()
            } else {
                None
            }
        });
    match loaded {
        Some(r) => {
            debug!("Using per-user rules {}", path.display());
            r
        }
        None => global.clone(),
    }
}

/// Handle a validated launch. Returns the exit code reported to the client.
fn handle_launch(req: &LaunchRequest, state: &Arc<Mutex<DaemonState>>) -> Result<i32> {
    {
        let mut state = state.lock().unwrap();
        state.launch_count += 1;
        *state
            .launch_history
            .entry(req.exe_path.clone())
            .or_insert(0) += 1;
    }

    // Snapshot rules/app_db under the lock, then drop it before dispatch.
    // Holding the mutex across the Wine process serializes every launch and
    // blocks IPC status/quit for the whole session.
    let (global_rules, app_db) = {
        let state = state.lock().unwrap();
        (state.rules.clone(), state.app_db.clone())
    };
    let rules = match &req.ids {
        Some(ids) => rules_for_user(ids, &global_rules),
        None => global_rules,
    };

    let mut args = args_for_launch_request(req);

    // Everything this thread does on disk from here on (prefix creation,
    // state.json, overlay dirs) happens with the user's filesystem identity,
    // so user-planted symlinks can only ever reach what the user could
    // already reach. Child processes are unaffected (execve resets fsuid).
    let _fs = match &req.ids {
        Some(ids) => Some(crate::privs::FsIdGuard::enter(ids.uid, ids.gid)?),
        None => None,
    };
    let code = dispatch::execute(&mut args, &req.exe_path, &req.hash, &rules, &app_db)?;
    Ok(if code == std::process::ExitCode::SUCCESS {
        0
    } else {
        1
    })
}

/// Build the `Args` used to dispatch a daemon launch request.
/// Extracted out of `handle_launch` so the fields that matter for
/// correctness on this code path -- especially `no_gui` -- are pinned by a
/// direct unit test instead of only being verifiable by reading the source.
///
/// `no_gui` is unconditionally `true`: this always runs on a daemon
/// background thread, which has no controlling terminal to prompt on.
/// Leaving it `false` would hang every daemon-dispatched launch of an
/// unknown `.exe` waiting on stdin input that can never arrive.
fn args_for_launch_request(req: &LaunchRequest) -> Args {
    Args {
        exe: Some(req.exe_path.clone()),
        tier: None,
        rules: None,
        verbose: false,
        resolved_wineprefix: None,
        no_gui: true,
        dry_run: false,
        gamepad: false,
        nested_x11: false,
        xvfb: false,
        host_x11: false,
        wayland: false,
        args: req.args.clone(),
        trust: false,
        optimize_net: false,
        cleanup_net: false,
        configure_net: false,
        daemon: false,
        status: false,
        reload: false,
        stop: false,
        unregister: false,
        print_seatbelt_profile: false,
        wine_prefix: None,
        user_env: req.env.clone(),
        uid: req.uid,
        exe_identity: req.identity,
    }
}

/// Main daemon entry point.
///
/// 1. Loads all state into memory
/// 2. Registers binfmt_misc handler
/// 3. Binds the launch and IPC sockets
/// 4. Serves until `quit`
pub fn run_daemon() -> Result<()> {
    info!("Starting APEX-WIN daemon...");

    // Root is required on Linux only, and only because binfmt_misc
    // registration (/proc/sys/fs/binfmt_misc/register) and the systemd
    // RuntimeDirectory (/run/win-sandbox-runner) both require it. Neither
    // applies on macOS: the macOS daemon is a per-user launchd LaunchAgent
    // and must NOT require root.
    #[cfg(target_os = "linux")]
    {
        // SAFETY: geteuid() is always safe to call on Unix
        if unsafe { libc::geteuid() } != 0 {
            return Err(anyhow::anyhow!(
                "Daemon must run as root (needed for binfmt_misc registration)"
            ));
        }
        // Overlays left behind by a previous daemon that was SIGKILLed.
        crate::cleanup::sweep_stale_overlays();
    }

    // 1. Load state
    let state = Arc::new(Mutex::new(DaemonState::load()?));

    // 2. Setup runtime
    ensure_runtime_dir()?;
    write_pid()?;

    // 3. Register binfmt_misc (non-fatal — daemon can still serve IPC/launch)
    if let Err(e) = register_binfmt() {
        warn!("binfmt_misc registration failed: {e}");
        warn!("Daemon will still run; launches via the launch socket remain available.");
    }

    // 4. Sockets
    let shutdown = Arc::new(Mutex::new(false));
    let ipc_handle = spawn_ipc_listener(Arc::clone(&state), Arc::clone(&shutdown))?;
    let launch_handle = spawn_launch_listener(Arc::clone(&state), Arc::clone(&shutdown))?;

    // 5. Apply network optimizations if config says to
    {
        let state = state.lock().unwrap();
        match netopt::optimize(&state.net_config) {
            Ok(result) => {
                if result.bbr_applied || result.sqm_applied {
                    info!("Network optimized for gaming: {result}");
                }
            }
            Err(e) => {
                debug!("Network optimization skipped (not root or not available): {e}");
            }
        }
    }

    info!(
        "APEX-WIN daemon ready. Listening on {}",
        launch_socket_path().display()
    );

    let _ = ipc_handle.join();
    let _ = launch_handle.join();

    info!("Cleaning up...");
    unregister_binfmt().ok();
    cleanup_runtime();
    info!("APEX-WIN daemon stopped.");
    Ok(())
}

/// Query the daemon status via IPC socket.
pub fn query_status() -> Result<String> {
    send_command("status")
}

/// Send a command to the daemon via IPC socket.
pub fn send_command(cmd: &str) -> Result<String> {
    let path = socket_path();
    if !path.exists() {
        return Err(anyhow::anyhow!(
            "Daemon not running (socket not found at {})",
            path.display()
        ));
    }

    let mut stream =
        UnixStream::connect(&path).context("Failed to connect to daemon IPC socket")?;

    stream.write_all(format!("{cmd}\n").as_bytes())?;
    stream.flush()?;

    let mut response = String::new();
    let mut reader = BufReader::new(&stream);
    reader.read_line(&mut response)?;

    Ok(response)
}
#[cfg(test)]
mod tests {
    use super::*;

    /// The exact regression this project already made once: `no_gui` on a
    /// FIFO-dispatched request was hardcoded `false`, which is only safe
    /// because a background daemon thread has no controlling terminal. As
    /// long as `wizard.rs`'s interactive prompt was unimplemented, `false`
    /// here was silently harmless; it stopped being harmless the moment the
    /// prompt did something. Pinned here as a real assertion instead of a
    /// property only checkable by reading the source.
    #[test]
    fn launch_request_args_never_enable_gui_prompts() {
        let args = args_for_launch_request(&sample_request(HashMap::new()));
        assert!(
            args.no_gui,
            "a daemon-dispatched launch must never enable the interactive wizard prompt, \
             which would hang the daemon's background thread waiting on stdin"
        );
    }

    #[test]
    fn launch_request_args_never_enable_gui_prompts_even_with_missing_uid() {
        let mut req = sample_request(HashMap::new());
        req.uid = None;
        assert!(args_for_launch_request(&req).no_gui);
    }

    /// Program arguments must reach Wine (the FIFO protocol silently dropped
    /// every argument after the .exe path).
    #[test]
    fn launch_request_args_forward_exe_path_uid_env_and_args() {
        let mut env = HashMap::new();
        env.insert("DISPLAY".to_string(), ":0".to_string());
        let args = args_for_launch_request(&sample_request(env.clone()));
        assert_eq!(args.exe.as_deref(), Some("/home/alice/game.exe"));
        assert_eq!(args.uid, Some(1000));
        assert_eq!(args.user_env, env);
        assert_eq!(args.args, vec!["-windowed".to_string()]);
    }

    fn caps(
        landlock_abi: Option<u8>,
        bwrap_version: Option<&str>,
        overlay: bool,
    ) -> crate::capabilities::Capabilities {
        caps_with_seatbelt(landlock_abi, bwrap_version, overlay, None)
    }

    fn caps_with_seatbelt(
        landlock_abi: Option<u8>,
        bwrap_version: Option<&str>,
        overlay: bool,
        seatbelt: Option<bool>,
    ) -> crate::capabilities::Capabilities {
        crate::capabilities::Capabilities {
            landlock_abi,
            bwrap_version: bwrap_version.map(String::from),
            unprivileged_overlay: overlay,
            seatbelt_available: seatbelt,
        }
    }

    /// `--status` output must stay valid JSON with the capability fields a
    /// caller (a future CI acceptance check, or a human running --status by
    /// hand) can parse, matching what was measured by hand on this session's
    /// machine: Landlock ABI v4/v8 depending on kernel, bwrap 0.9.0,
    /// tier3_available=false.
    #[test]
    fn status_json_is_valid_and_matches_capabilities() {
        let json = build_status_json(3, 35, 7, &caps(Some(4), Some("0.9.0"), false));
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("must be valid JSON");

        assert_eq!(parsed["launch_count"], 3);
        assert_eq!(parsed["app_profiles"], 35);
        assert_eq!(parsed["rules"], 7);
        assert_eq!(parsed["landlock_abi"], 4);
        assert_eq!(parsed["bwrap_version"], "0.9.0");
        assert_eq!(parsed["tier3_available"], false);
    }

    /// A host with no bwrap or unusable Landlock must serialize as JSON
    /// `null`, not a Rust `None` string or an invalid literal, or every
    /// consumer of --status has to special-case a malformed field.
    #[test]
    fn status_json_handles_missing_capabilities_as_null() {
        let json = build_status_json(0, 0, 0, &caps(None, None, false));
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("must be valid JSON");

        assert!(parsed["landlock_abi"].is_null());
        assert!(parsed["bwrap_version"].is_null());
        assert_eq!(parsed["tier3_available"], false);
    }

    #[test]
    fn status_json_reports_tier3_available_true() {
        let json = build_status_json(0, 0, 0, &caps(Some(4), Some("0.10.0"), true));
        let parsed: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed["tier3_available"], true);
    }

    /// On Linux (`seatbelt_available: None`, since the field only applies to
    /// macOS), `--status` must still report `null`, not silently omit the
    /// key or coerce it to `false` — those are different facts (`false`
    /// would mean "macOS host, sandbox-exec missing").
    #[test]
    fn status_json_seatbelt_null_on_non_macos() {
        let json = build_status_json(0, 0, 0, &caps(None, None, false));
        let parsed: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert!(parsed["seatbelt_available"].is_null());
    }

    #[test]
    fn status_json_seatbelt_reports_macos_state() {
        let with = caps_with_seatbelt(None, None, false, Some(true));
        let without = caps_with_seatbelt(None, None, false, Some(false));

        let parsed_with: serde_json::Value =
            serde_json::from_str(&build_status_json(0, 0, 0, &with)).unwrap();
        let parsed_without: serde_json::Value =
            serde_json::from_str(&build_status_json(0, 0, 0, &without)).unwrap();

        assert_eq!(parsed_with["seatbelt_available"], true);
        assert_eq!(parsed_without["seatbelt_available"], false);
    }

    /// tier1_2_available must be true whenever ANY of the three underlying
    /// mechanisms (Landlock, bubblewrap, Seatbelt) is present, and false
    /// only when none are — this is the field a caller checks to answer
    /// "is any filesystem-restriction sandbox usable here at all" without
    /// having to know which OS it is running on.
    #[test]
    fn status_json_tier1_2_available_true_when_any_mechanism_present() {
        let via_landlock = caps(Some(4), None, false);
        let via_bwrap = caps(None, Some("0.9.0"), false);
        let via_seatbelt = caps_with_seatbelt(None, None, false, Some(true));

        for c in [via_landlock, via_bwrap, via_seatbelt] {
            let parsed: serde_json::Value =
                serde_json::from_str(&build_status_json(0, 0, 0, &c)).unwrap();
            assert_eq!(
                parsed["tier1_2_available"], true,
                "expected tier1_2_available=true for {c:?}"
            );
        }
    }

    #[test]
    fn status_json_tier1_2_available_false_when_nothing_present() {
        let nothing = caps_with_seatbelt(None, None, false, None);
        let parsed: serde_json::Value =
            serde_json::from_str(&build_status_json(0, 0, 0, &nothing)).unwrap();
        assert_eq!(parsed["tier1_2_available"], false);

        // Explicitly-false Seatbelt (macOS, sandbox-exec missing) must not
        // be confused with "not applicable" (None) — both correctly report
        // tier1_2_available=false here, but for different underlying facts.
        let macos_no_seatbelt = caps_with_seatbelt(None, None, false, Some(false));
        let parsed2: serde_json::Value =
            serde_json::from_str(&build_status_json(0, 0, 0, &macos_no_seatbelt)).unwrap();
        assert_eq!(parsed2["tier1_2_available"], false);
    }

    #[test]
    fn launch_socket_path_format() {
        assert_eq!(launch_socket_path(), runtime_dir_base().join("launch.sock"));
    }

    #[test]
    fn socket_path_format() {
        assert_eq!(socket_path(), runtime_dir_base().join("ipc.sock"));
    }

    #[test]
    fn pid_path_format() {
        assert_eq!(pid_path(), runtime_dir_base().join("daemon.pid"));
    }

    /// The Linux runtime dir must stay `/run/win-sandbox-runner` exactly —
    /// it is load-bearing: `scripts/win-sandbox-runner.service`'s
    /// `RuntimeDirectory=win-sandbox-runner` line creates precisely this
    /// path before `ExecStart`, so if this constant ever drifted from that
    /// unit file the daemon would silently fail to find its own runtime
    /// directory. This is the platform-specific half of the path-format
    /// tests above, which only check "does launch_socket_path() build on top of
    /// runtime_dir_base() correctly" — not "is the Linux value itself right".
    #[test]
    #[cfg(target_os = "linux")]
    fn runtime_dir_base_matches_systemd_unit_file() {
        assert_eq!(RUNTIME_DIR_BASE, "/run/win-sandbox-runner");
    }

    /// The macOS runtime dir must live under $TMPDIR (per-user, private,
    /// mode 0700), never a shared/root-owned location — the daemon there
    /// runs as an ordinary user (see `run_daemon()`'s platform split), so
    /// anywhere shared between users would be both wrong (permission
    /// errors) and a symlink-attack surface no `/run`-style tmpfs has.
    #[test]
    #[cfg(not(target_os = "linux"))]
    fn runtime_dir_base_is_under_tmpdir_not_shared() {
        let dir = runtime_dir_base();
        assert!(
            dir.ends_with("win-sandbox-runner"),
            "expected .../win-sandbox-runner, got {}",
            dir.display()
        );
        assert_ne!(
            dir,
            std::path::PathBuf::from("/var/run/win-sandbox-runner"),
            "must not resolve to a root-owned shared path"
        );
    }

    /// The previous version of this test asserted only that the path wasn't
    /// `/var/run`, while its own doc comment claimed "never a shared
    /// location". That gap let a `/tmp` fallback ship: `/tmp` is mode 1777
    /// and shared by every local user, exactly the thing the comment said
    /// was forbidden, and no assertion contradicted it. These cases pin the
    /// actual claim down.
    #[test]
    #[cfg(not(target_os = "linux"))]
    fn runtime_dir_base_never_resolves_to_a_world_writable_dir() {
        let dir = runtime_dir_base();
        for shared in [
            "/tmp",
            "/var/tmp",
            "/private/tmp",
            "/var/run",
            "/private/var/run",
        ] {
            assert!(
                !dir.starts_with(shared),
                "runtime dir {} is under {shared}, which every local user can write to; \
                 the FIFO and IPC socket there control process launches",
                dir.display()
            );
        }
    }

    /// With TMPDIR unset the daemon must refuse to start, not silently pick
    /// a world-writable directory. This is the check that would have caught
    /// the `/tmp` fallback directly.
    #[test]
    #[cfg(not(target_os = "linux"))]
    fn runtime_dir_base_checked_refuses_when_tmpdir_missing() {
        // Safety: single-threaded test process; restored before returning.
        let saved = std::env::var("TMPDIR").ok();

        std::env::remove_var("TMPDIR");
        let missing = runtime_dir_base_checked();

        std::env::set_var("TMPDIR", "   ");
        let blank = runtime_dir_base_checked();

        std::env::set_var("TMPDIR", "/var/folders/ab/cd/T/");
        let ok = runtime_dir_base_checked();

        match saved {
            Some(v) => std::env::set_var("TMPDIR", v),
            None => std::env::remove_var("TMPDIR"),
        }

        assert!(missing.is_err(), "unset TMPDIR must refuse, not fall back");
        assert!(blank.is_err(), "blank TMPDIR must refuse, not fall back");
        let ok = ok.expect("a real TMPDIR must be accepted");
        assert_eq!(
            ok,
            std::path::PathBuf::from("/var/folders/ab/cd/T/win-sandbox-runner"),
            "trailing slash on TMPDIR must not produce a doubled separator"
        );
    }

    fn sample_request(env: HashMap<String, String>) -> LaunchRequest {
        LaunchRequest {
            exe_path: "/home/alice/game.exe".to_string(),
            uid: Some(1000),
            args: vec!["-windowed".into()],
            env,
            ids: None,
            hash: "0".repeat(64),
            identity: None,
        }
    }

    /// status is open to any peer; quit/reload/trust require root (or the
    /// daemon's own uid when the daemon is not root).
    #[test]
    fn ipc_privileged_commands_require_authorization() {
        assert!(authorize_ipc_command("status", 1000, 1000).is_ok());
        assert!(authorize_ipc_command("quit", 1000, 1000).is_ok());
        assert!(authorize_ipc_command("reload", 1000, 1000).is_ok());

        // Root daemon: non-root peer must not stop/reload it.
        assert!(authorize_ipc_command("quit", 1000, 0).is_err());
        assert!(authorize_ipc_command("reload", 1000, 0).is_err());
        assert!(authorize_ipc_command("quit", 0, 0).is_ok());

        // trust writes rules every user's launches read: root only, even for
        // a file the peer owns (would otherwise disable sandboxing for others).
        assert!(authorize_ipc_command("trust /home/a/a.exe", 1000, 0).is_err());
        assert!(authorize_ipc_command("trust /home/a/a.exe", 0, 0).is_ok());
        // Unknown peer never privileged.
        assert!(authorize_ipc_command("quit", u32::MAX, 0).is_err());
    }

    #[test]
    fn launch_peer_authorization() {
        // Root daemon (Linux): users yes, root no, unknown no.
        assert!(authorize_launch_peer(1000, 0).is_ok());
        assert!(authorize_launch_peer(0, 0).is_err());
        assert!(authorize_launch_peer(u32::MAX, 0).is_err());
        // Per-user daemon (macOS): only its own user.
        assert!(authorize_launch_peer(501, 501).is_ok());
        assert!(authorize_launch_peer(502, 501).is_err());
    }

    #[test]
    fn launch_slots_are_capped_per_uid_and_released() {
        let uid = 4_000_000_001;
        let slots: Vec<_> = (0..MAX_LAUNCHES_PER_UID)
            .map(|_| LaunchSlot::acquire(uid).unwrap())
            .collect();
        assert!(LaunchSlot::acquire(uid).is_none());
        assert!(
            LaunchSlot::acquire(uid + 1).is_some(),
            "other users unaffected"
        );
        drop(slots);
        assert!(LaunchSlot::acquire(uid).is_some());
    }

    /// The daemon side derives the uid from the kernel and hashes the passed
    /// fd; a message cannot smuggle a different file under a path.
    #[test]
    fn launch_request_build_binds_path_to_passed_fd() {
        let dir = tempfile::tempdir().unwrap();
        let exe = dir.path().join("app.exe");
        let other = dir.path().join("other.exe");
        std::fs::write(&exe, b"MZ app").unwrap();
        std::fs::write(&other, b"MZ other").unwrap();
        let uid = unsafe { libc::getuid() };

        let msg = |p: &Path| launch::LaunchMessage {
            exe: p.to_str().unwrap().to_string(),
            args: vec![],
            env: HashMap::new(),
        };
        let ok = LaunchRequest::build(uid, msg(&exe), std::fs::File::open(&exe).unwrap()).unwrap();
        assert_eq!(ok.uid, Some(uid));
        assert_eq!(ok.hash, hasher::hash_file(exe.to_str().unwrap()).unwrap());

        // Path says app.exe, fd is other.exe: refused.
        let err = LaunchRequest::build(uid, msg(&exe), std::fs::File::open(&other).unwrap())
            .err()
            .expect("mismatched fd/path must be refused");
        assert!(format!("{err:#}").contains("not the file"), "{err:#}");
    }
    /// The daemon and scripts/register-binfmt.sh both define the registration
    /// line, because the daemon cannot assume the script is installed. This
    /// test is what keeps that duplication honest: it runs the script's
    /// --print and asserts byte equality with the Rust constant.
    ///
    /// Without it, the two drift. The missing-\xff\xff-mask bug (kernel returns
    /// EINVAL) was found and fixed three separate times precisely because
    /// nothing compared the copies.
    ///
    /// Linux-only: this shells out to `sh scripts/register-binfmt.sh
    /// --print`, whose `echo "$DEFINITION"` behavior is not portable. Caught
    /// live on the CI macOS runner: `/bin/sh` there is bash running in POSIX
    /// mode, whose `echo` DOES interpret `\xHH` escapes, while Linux's
    /// `/bin/sh` (dash) does NOT -- so the same script prints literally
    /// different bytes (`\x4d\x5a` vs. the raw bytes 0x4d 0x5a) depending on
    /// which OS runs it. binfmt_misc itself only exists on Linux anyway, so
    /// this test asserting Linux-specific shell behavior is correctly scoped
    /// to Linux, not a bug to paper over with a portable echo elsewhere.
    #[test]
    #[cfg(target_os = "linux")]
    fn binfmt_definition_matches_script() {
        let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../scripts/register-binfmt.sh");

        // Skip rather than fail when the script is absent (e.g. installed
        // crate, vendored build) so this cannot produce a false failure.
        if !script.exists() {
            eprintln!("skip: {} not present", script.display());
            return;
        }

        let out = std::process::Command::new("sh")
            .arg(&script)
            .arg("--print")
            .output()
            .expect("failed to run register-binfmt.sh --print");

        assert!(
            out.status.success(),
            "register-binfmt.sh --print failed: {}",
            String::from_utf8_lossy(&out.stderr)
        );

        let from_script = String::from_utf8_lossy(&out.stdout).trim().to_string();
        assert_eq!(
            from_script, BINFMT_REGISTRATION,
            "binfmt definition drifted between daemon.rs and register-binfmt.sh"
        );
    }

    /// Guards the specific field that has broken three times.
    #[test]
    fn binfmt_registration_carries_mask() {
        let fields: Vec<&str> = BINFMT_REGISTRATION.split(':').collect();
        // :name:type:offset:magic:mask:interpreter:flags -> leading empty field
        assert_eq!(fields.len(), 8, "unexpected field count: {fields:?}");
        assert_eq!(fields[1], "APEX-WIN");
        assert_eq!(fields[2], "M", "must be a magic match");
        assert_eq!(fields[3], "0", "magic is at offset 0");
        assert_eq!(fields[4], r"\x4d\x5a", "MZ header");
        assert_eq!(
            fields[5], r"\xff\xff",
            "mask must be present or the kernel rejects with EINVAL"
        );
        assert!(fields[6].ends_with("win-sandbox-runner"));
        assert!(
            !fields[7].contains('C'),
            "C would take credentials from a setuid .exe"
        );
        assert!(fields[7].contains('F'), "F = fix binary");
    }
}
