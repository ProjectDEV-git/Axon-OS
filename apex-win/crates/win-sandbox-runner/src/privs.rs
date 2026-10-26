//! Privilege helpers for the root daemon.
//!
//! Two primitives:
//!
//! * [`UserIds::lookup`] resolves a uid's passwd entry and full group list in
//!   the *parent*, so the forked child never calls NSS (not async-signal-safe)
//!   and keeps the user's supplementary groups (`dialout` for USB serial
//!   cables, `video`/`render`/`audio`, ...). The earlier `setgroups(0)` drop
//!   stripped them, which broke hardware access for daemon launches.
//!
//! * [`FsIdGuard`] switches the *calling thread's* filesystem uid/gid to the
//!   target user for the lifetime of the guard. Linux fsuid is per-thread
//!   (glibc does not broadcast `setfsuid`), so a launch thread can create,
//!   read, and write files in the user's home with exactly the user's access
//!   rights. Without it, root's `create_dir_all`/`chown`/`write` follow
//!   user-planted symlinks (e.g. `prefixes/<hash>` -> `/etc`) and hand root
//!   ownership of arbitrary paths to the user.

use anyhow::{bail, Result};
use std::ffi::{CStr, CString};
use std::path::PathBuf;

/// Resolved identity for a target user.
#[derive(Debug, Clone)]
pub struct UserIds {
    pub uid: u32,
    pub gid: u32,
    pub name: String,
    pub home: PathBuf,
    pub groups: Vec<libc::gid_t>,
}

impl UserIds {
    /// Resolve `uid` via `getpwuid_r` and `getgrouplist`.
    pub fn lookup(uid: u32) -> Result<Self> {
        let mut buf = vec![0u8; 16 * 1024];
        let mut pwd: libc::passwd = unsafe { std::mem::zeroed() };
        let mut result: *mut libc::passwd = std::ptr::null_mut();
        loop {
            let rc = unsafe {
                libc::getpwuid_r(
                    uid,
                    &mut pwd,
                    buf.as_mut_ptr() as *mut libc::c_char,
                    buf.len(),
                    &mut result,
                )
            };
            if rc == libc::ERANGE && buf.len() < 1 << 20 {
                buf.resize(buf.len() * 2, 0);
                continue;
            }
            if rc != 0 || result.is_null() {
                bail!("no passwd entry for uid {uid}");
            }
            break;
        }
        let name = unsafe { CStr::from_ptr(pwd.pw_name) }
            .to_string_lossy()
            .into_owned();
        let home = unsafe { CStr::from_ptr(pwd.pw_dir) }
            .to_string_lossy()
            .into_owned();
        if home.is_empty() || !home.starts_with('/') {
            bail!("uid {uid} has no absolute home directory");
        }
        let gid = pwd.pw_gid;
        let groups = group_list(&name, gid);
        Ok(Self {
            uid,
            gid,
            name,
            home: PathBuf::from(home),
            groups,
        })
    }
}

fn group_list(name: &str, gid: u32) -> Vec<libc::gid_t> {
    let Ok(cname) = CString::new(name) else {
        return vec![gid];
    };
    let mut n: libc::c_int = 64;
    loop {
        let mut groups: Vec<libc::gid_t> = vec![0; n as usize];
        let before = n;
        #[cfg(target_os = "macos")]
        let rc = unsafe {
            libc::getgrouplist(
                cname.as_ptr(),
                gid as libc::c_int,
                groups.as_mut_ptr() as *mut libc::c_int,
                &mut n,
            )
        };
        #[cfg(not(target_os = "macos"))]
        let rc = unsafe { libc::getgrouplist(cname.as_ptr(), gid, groups.as_mut_ptr(), &mut n) };
        if rc >= 0 {
            groups.truncate(n.max(0) as usize);
            if !groups.contains(&gid) {
                groups.push(gid);
            }
            return groups;
        }
        if n <= before {
            n = before * 2;
        }
        if n > 65536 {
            return vec![gid];
        }
    }
}

/// Per-thread filesystem identity switch (Linux, root only).
///
/// A no-op when the process is not root (nothing to drop) and on non-Linux
/// targets (the macOS daemon already runs as the user).
pub struct FsIdGuard {
    #[allow(dead_code)]
    active: bool,
    #[allow(dead_code)]
    prev: (u32, u32),
}

impl FsIdGuard {
    /// Switch this thread's fsuid/fsgid; the previous values are restored on
    /// drop, so guards nest (`enter(0, 0)` inside a user guard temporarily
    /// regains root file access for root-owned bookkeeping).
    pub fn enter(uid: u32, gid: u32) -> Result<Self> {
        #[cfg(target_os = "linux")]
        {
            if unsafe { libc::geteuid() } != 0 {
                return Ok(Self {
                    active: false,
                    prev: (0, 0),
                });
            }
            unsafe {
                // An invalid id (-1) changes nothing and returns the current.
                let prev_uid = libc::setfsuid(u32::MAX) as u32;
                let prev_gid = libc::setfsgid(u32::MAX) as u32;
                libc::setfsgid(gid);
                libc::setfsuid(uid);
                let cur_uid = libc::setfsuid(u32::MAX) as u32;
                let cur_gid = libc::setfsgid(u32::MAX) as u32;
                if cur_uid != uid || cur_gid != gid {
                    libc::setfsuid(prev_uid);
                    libc::setfsgid(prev_gid);
                    bail!("failed to switch filesystem identity to {uid}:{gid}");
                }
                Ok(Self {
                    active: true,
                    prev: (prev_uid, prev_gid),
                })
            }
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (uid, gid);
            Ok(Self {
                active: false,
                prev: (0, 0),
            })
        }
    }
}

impl Drop for FsIdGuard {
    fn drop(&mut self) {
        #[cfg(target_os = "linux")]
        if self.active {
            unsafe {
                libc::setfsuid(self.prev.0);
                libc::setfsgid(self.prev.1);
            }
        }
    }
}

/// Resolve `uid` in the parent and configure `cmd` to drop to it; if the
/// uid has no passwd entry the spawn fails instead of running as root.
///
/// # Safety
/// Installs a `pre_exec` hook.
pub unsafe fn drop_to_uid(cmd: &mut std::process::Command, uid: u32) {
    use std::os::unix::process::CommandExt;
    match UserIds::lookup(uid) {
        Ok(ids) => unsafe { drop_to_user(cmd, &ids) },
        Err(_) => unsafe {
            cmd.pre_exec(move || {
                Err(std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    "target uid has no passwd entry",
                ))
            });
        },
    }
}

/// Configure `cmd` to run as `ids` (supplementary groups -> gid -> uid).
///
/// Everything is resolved before fork; the `pre_exec` closure only makes
/// async-signal-safe syscalls. Any failure aborts the spawn instead of
/// continuing with root's credentials.
///
/// # Safety
/// Installs a `pre_exec` hook; see [`std::os::unix::process::CommandExt`].
pub unsafe fn drop_to_user(cmd: &mut std::process::Command, ids: &UserIds) {
    use std::os::unix::process::CommandExt;
    let uid = ids.uid;
    let gid = ids.gid;
    let groups = ids.groups.clone();
    unsafe {
        cmd.pre_exec(move || {
            let euid = libc::geteuid();
            if euid == uid && libc::getegid() == gid {
                // Already the target (macOS LaunchAgent, CLI): nothing to drop.
                return Ok(());
            }
            #[cfg(target_os = "macos")]
            let rc = libc::setgroups(groups.len() as libc::c_int, groups.as_ptr());
            #[cfg(not(target_os = "macos"))]
            let rc = libc::setgroups(groups.len(), groups.as_ptr());
            if rc != 0 {
                return Err(std::io::Error::last_os_error());
            }
            if libc::setgid(gid) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            if libc::setuid(uid) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            // Paranoia: regaining root must be impossible now.
            if uid != 0 && libc::setuid(0) == 0 {
                return Err(std::io::Error::other("privilege drop was reversible"));
            }
            Ok(())
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lookup_current_user() {
        let uid = unsafe { libc::getuid() };
        let ids = UserIds::lookup(uid).unwrap();
        assert_eq!(ids.uid, uid);
        assert!(ids.home.is_absolute());
        assert!(ids.groups.contains(&ids.gid));
    }

    #[test]
    fn lookup_unknown_uid_fails() {
        assert!(UserIds::lookup(4_000_000_000).is_err());
    }

    #[test]
    fn fsid_guard_is_noop_when_unprivileged() {
        if unsafe { libc::geteuid() } == 0 {
            return;
        }
        let g = FsIdGuard::enter(1, 1).unwrap();
        drop(g);
    }

    /// Spawning as ourselves must work (the no-op path).
    #[test]
    fn drop_to_self_spawns() {
        let uid = unsafe { libc::getuid() };
        let ids = UserIds::lookup(uid).unwrap();
        let mut cmd = std::process::Command::new("true");
        unsafe { drop_to_user(&mut cmd, &ids) };
        assert!(cmd.status().unwrap().success());
    }
}
