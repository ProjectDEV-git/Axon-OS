//! OverlayFS lifecycle for Tier 3.
//!
//! Every live overlay is tracked in a process-wide registry, so concurrent
//! Tier 3 launches inside the daemon each clean up their own mount, and the
//! atexit/panic hooks clean up whatever is still mounted when the process
//! dies normally.
//!
//! SIGKILL (or OOM kill, or power loss) cannot run any hook, so overlays are
//! also swept on the next start: [`sweep_stale_overlays`] removes every
//! `win-run-<pid>-*` directory whose owning process no longer exists.
//!
//! Replaces an earlier design that stored one mount path in a `static mut`
//! (the second concurrent launch overwrote the first's path, so only one was
//! ever cleaned) and installed a SIGCHLD self-pipe that nothing read: after
//! 64 KiB of child exits the handler's `write()` blocked inside a signal
//! handler and wedged the daemon.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, Once};
use tracing::{debug, error, info, warn};

/// Base directory for overlay work (RAM-backed via /dev/shm).
pub const OVERLAY_ROOT: &str = "/dev/shm";
/// Name prefix of per-launch overlay directories under [`OVERLAY_ROOT`].
pub const OVERLAY_PREFIX: &str = "win-run-";

/// Overlay base directories (the `win-run-<pid>-<n>` dir) still mounted.
static ACTIVE: Mutex<Vec<PathBuf>> = Mutex::new(Vec::new());
static HOOKS: Once = Once::new();

/// Register a live overlay and make sure process-exit hooks exist.
pub fn register(base: &Path) {
    install_hooks_once();
    ACTIVE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .push(base.to_path_buf());
    debug!("Registered overlay {}", base.display());
}

/// Unmount and remove one overlay and drop it from the registry.
pub fn release(base: &Path) {
    ACTIVE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .retain(|p| p != base);
    cleanup_overlay_base(base);
}

fn install_hooks_once() {
    HOOKS.call_once(|| {
        unsafe {
            libc::atexit(cleanup_all_atexit);
        }
        let prev = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            error!("Panic occurred, cleaning up overlay mounts");
            cleanup_all();
            prev(info);
        }));
    });
}

extern "C" fn cleanup_all_atexit() {
    cleanup_all();
}

fn cleanup_all() {
    // try_lock: a panic while the registry is held must not deadlock the hook.
    let bases: Vec<PathBuf> = match ACTIVE.try_lock() {
        Ok(mut v) => std::mem::take(&mut *v),
        Err(std::sync::TryLockError::Poisoned(p)) => std::mem::take(&mut *p.into_inner()),
        Err(std::sync::TryLockError::WouldBlock) => return,
    };
    for base in bases {
        cleanup_overlay_base(&base);
    }
}

/// Unmount `<base>/merged` and remove `<base>`.
///
/// Uses `UMOUNT_NOFOLLOW` and never follows symlinks while removing: the
/// merged dir is visible to the sandboxed user, who could otherwise swap it
/// for a symlink and have root unmount or delete something else.
pub fn cleanup_overlay_base(base: &Path) {
    let merged = base.join("merged");
    debug!("Cleaning up overlay: {}", base.display());

    // Daemon launches run with the user's filesystem identity, but overlayfs
    // creates `work/work` as root with mode 000, which the user cannot
    // delete. Temporarily regain root's fs identity for the teardown (a
    // no-op when the process is not root). remove_dir_all never follows
    // symlinks, so this cannot be steered outside `base`.
    let _root_fs = crate::privs::FsIdGuard::enter(0, 0);

    use nix::mount::{umount2, MntFlags};
    match umount2(&merged, MntFlags::MNT_DETACH | MntFlags::UMOUNT_NOFOLLOW) {
        Ok(()) => {}
        // Not mounted (setup failed before mount, or already swept).
        Err(nix::errno::Errno::EINVAL) | Err(nix::errno::Errno::ENOENT) => {}
        Err(e) => warn!("Failed to unmount {}: {e}", merged.display()),
    }

    match std::fs::symlink_metadata(base) {
        Ok(m) if m.file_type().is_dir() => {
            // remove_dir_all does not follow symlinks inside the tree.
            if let Err(e) = std::fs::remove_dir_all(base) {
                error!("Failed to remove overlay dir {}: {e}", base.display());
            }
        }
        Ok(_) => warn!(
            "Refusing to remove {}: not a real directory",
            base.display()
        ),
        Err(_) => {}
    }
    info!("Overlay cleanup complete: {}", base.display());
}

/// Parse the owning pid out of an overlay directory name
/// (`win-run-<pid>-<seq>`, or the legacy `win-run-<pid>`).
pub fn owner_pid(name: &str) -> Option<u32> {
    let rest = name.strip_prefix(OVERLAY_PREFIX)?;
    let pid = rest.split('-').next()?;
    pid.parse().ok()
}

fn pid_alive(pid: u32) -> bool {
    Path::new(&format!("/proc/{pid}")).exists()
}

/// Remove overlays left behind by processes that died without cleanup
/// (SIGKILL, OOM, crash in a signal-unsafe spot, reboot of a persistent
/// /dev/shm). Only touches real directories (never symlinks). As root, the
/// directories are owned by the launching user (created under their fs
/// identity), so any owner is accepted; otherwise only our own.
pub fn sweep_stale_overlays() {
    sweep_stale_in(Path::new(OVERLAY_ROOT), pid_alive);
}

fn sweep_stale_in(root: &Path, alive: impl Fn(u32) -> bool) -> usize {
    use std::os::unix::fs::MetadataExt;
    let euid = unsafe { libc::geteuid() };
    let mut removed = 0;
    let Ok(entries) = std::fs::read_dir(root) else {
        return 0;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(name) = name.to_str() else { continue };
        let Some(pid) = owner_pid(name) else { continue };
        if alive(pid) {
            continue;
        }
        let path = entry.path();
        let Ok(meta) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        if !meta.file_type().is_dir() || (euid != 0 && meta.uid() != euid) {
            continue;
        }
        info!(
            "Sweeping stale overlay {} (pid {pid} is gone)",
            path.display()
        );
        cleanup_overlay_base(&path);
        removed += 1;
    }
    removed
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn owner_pid_parses_new_and_legacy_names() {
        assert_eq!(owner_pid("win-run-1234-7"), Some(1234));
        assert_eq!(owner_pid("win-run-1234"), Some(1234));
        assert_eq!(owner_pid("win-run-abc"), None);
        assert_eq!(owner_pid("other-1234"), None);
    }

    #[test]
    fn sweep_removes_only_dead_owned_dirs() {
        let root = tempfile::tempdir().unwrap();
        let dead = root.path().join("win-run-999999-0");
        let live = root.path().join("win-run-1-0");
        let unrelated = root.path().join("keepme");
        for d in [&dead, &live, &unrelated] {
            std::fs::create_dir_all(d.join("merged")).unwrap();
        }
        // A symlink named like an overlay must never be followed/removed.
        let target = root.path().join("target");
        std::fs::create_dir(&target).unwrap();
        std::os::unix::fs::symlink(&target, root.path().join("win-run-999998-0")).unwrap();

        let n = sweep_stale_in(root.path(), |pid| pid == 1);
        assert_eq!(n, 1);
        assert!(!dead.exists());
        assert!(live.exists());
        assert!(unrelated.exists());
        assert!(target.exists(), "symlink target must survive");
    }

    #[test]
    fn registry_release_removes_dir() {
        let root = tempfile::tempdir().unwrap();
        let base = root.path().join("win-run-5-0");
        std::fs::create_dir_all(base.join("merged")).unwrap();
        register(&base);
        release(&base);
        assert!(!base.exists());
        assert!(!ACTIVE.lock().unwrap().contains(&base));
    }
}
