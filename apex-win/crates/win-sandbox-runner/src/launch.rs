//! Launch protocol between the binfmt_misc handler and the daemon.
//!
//! Replaces the old world-writable FIFO, where the daemon had to believe a
//! self-reported `UID:` line and a path string. Over a Unix stream socket:
//!
//! * the client's uid comes from the kernel (`SO_PEERCRED` / `getpeereid`),
//!   never from the message;
//! * the client passes an **open fd** of the executable (`SCM_RIGHTS`). The
//!   client opened it with its own credentials, so the daemon never reads a
//!   file on the user's behalf that the user could not read, and the hash is
//!   computed over exactly the file the user asked to run;
//! * the daemon replies with the final result, so the shell gets a real exit
//!   status and error text instead of a fire-and-forget write.
//!
//! Wire format (client -> daemon): 4-byte big-endian length (the fd rides on
//! these bytes as ancillary data), then that many bytes of JSON
//! [`LaunchMessage`]. Replies are newline-terminated lines: `ACCEPTED`, then
//! `EXIT <code>` or `ERROR: <text>`.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::io::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::Duration;

/// Upper bound on a launch message (paths + args + a handful of env vars).
pub const MAX_MESSAGE: usize = 256 * 1024;

/// Environment variables the client forwards. The daemon re-validates every
/// one against the peer's real identity (see `env_sanitize::validate_daemon_env`).
pub const FORWARDED_ENV: &[&str] = &[
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "XAUTHORITY",
    "XDG_SESSION_TYPE",
    "WINEDEBUG",
    "TERM",
];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LaunchMessage {
    /// Absolute path of the executable (as the client resolved it).
    pub exe: String,
    /// Arguments for the Windows program.
    #[serde(default)]
    pub args: Vec<String>,
    /// Session environment (display etc.), untrusted.
    #[serde(default)]
    pub env: HashMap<String, String>,
}

/// Kernel-reported uid of the peer. Fails closed with `u32::MAX`.
pub fn peer_uid(stream: &UnixStream) -> u32 {
    let fd = stream.as_raw_fd();
    #[cfg(target_os = "linux")]
    {
        let mut cred = libc::ucred {
            pid: 0,
            uid: u32::MAX,
            gid: u32::MAX,
        };
        let mut len = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
        let ret = unsafe {
            libc::getsockopt(
                fd,
                libc::SOL_SOCKET,
                libc::SO_PEERCRED,
                &mut cred as *mut _ as *mut libc::c_void,
                &mut len,
            )
        };
        if ret == 0 && len as usize == std::mem::size_of::<libc::ucred>() {
            return cred.uid;
        }
    }
    #[cfg(not(target_os = "linux"))]
    {
        let mut euid: libc::uid_t = 0;
        let mut egid: libc::gid_t = 0;
        if unsafe { libc::getpeereid(fd, &mut euid, &mut egid) } == 0 {
            return euid;
        }
    }
    u32::MAX
}

/// Send `payload` with `fd` attached to its first byte.
fn send_with_fd(sock: RawFd, payload: &[u8], fd: RawFd) -> std::io::Result<usize> {
    unsafe {
        let space = libc::CMSG_SPACE(std::mem::size_of::<RawFd>() as u32) as usize;
        let mut cbuf = vec![0u8; space];
        let mut iov = libc::iovec {
            iov_base: payload.as_ptr() as *mut libc::c_void,
            iov_len: payload.len(),
        };
        let mut msg: libc::msghdr = std::mem::zeroed();
        msg.msg_iov = &mut iov;
        msg.msg_iovlen = 1;
        msg.msg_control = cbuf.as_mut_ptr() as *mut libc::c_void;
        msg.msg_controllen = space as _;
        let cmsg = libc::CMSG_FIRSTHDR(&msg);
        (*cmsg).cmsg_level = libc::SOL_SOCKET;
        (*cmsg).cmsg_type = libc::SCM_RIGHTS;
        (*cmsg).cmsg_len = libc::CMSG_LEN(std::mem::size_of::<RawFd>() as u32) as _;
        std::ptr::copy_nonoverlapping(
            &fd as *const RawFd as *const u8,
            libc::CMSG_DATA(cmsg),
            std::mem::size_of::<RawFd>(),
        );
        let n = libc::sendmsg(sock, &msg, 0);
        if n < 0 {
            Err(std::io::Error::last_os_error())
        } else {
            Ok(n as usize)
        }
    }
}

/// Receive into `buf`, collecting every passed fd. Every received fd is
/// owned (closed on drop), including extras a hostile client might attach.
fn recv_with_fds(sock: RawFd, buf: &mut [u8]) -> std::io::Result<(usize, Vec<OwnedFd>)> {
    unsafe {
        // Room for a few fds so extras are received (and closed) rather than
        // silently leaking via truncation.
        let space = libc::CMSG_SPACE((8 * std::mem::size_of::<RawFd>()) as u32) as usize;
        let mut cbuf = vec![0u8; space];
        let mut iov = libc::iovec {
            iov_base: buf.as_mut_ptr() as *mut libc::c_void,
            iov_len: buf.len(),
        };
        let mut msg: libc::msghdr = std::mem::zeroed();
        msg.msg_iov = &mut iov;
        msg.msg_iovlen = 1;
        msg.msg_control = cbuf.as_mut_ptr() as *mut libc::c_void;
        msg.msg_controllen = space as _;
        #[cfg(target_os = "linux")]
        let flags = libc::MSG_CMSG_CLOEXEC;
        #[cfg(not(target_os = "linux"))]
        let flags = 0;
        let n = libc::recvmsg(sock, &mut msg, flags);
        if n < 0 {
            return Err(std::io::Error::last_os_error());
        }
        let mut fds = Vec::new();
        let mut cmsg = libc::CMSG_FIRSTHDR(&msg);
        while !cmsg.is_null() {
            if (*cmsg).cmsg_level == libc::SOL_SOCKET && (*cmsg).cmsg_type == libc::SCM_RIGHTS {
                let data = libc::CMSG_DATA(cmsg);
                let header = libc::CMSG_LEN(0) as usize;
                let count = ((*cmsg).cmsg_len as usize).saturating_sub(header)
                    / std::mem::size_of::<RawFd>();
                for i in 0..count {
                    let mut raw: RawFd = -1;
                    std::ptr::copy_nonoverlapping(
                        data.add(i * std::mem::size_of::<RawFd>()),
                        &mut raw as *mut RawFd as *mut u8,
                        std::mem::size_of::<RawFd>(),
                    );
                    if raw >= 0 {
                        #[cfg(not(target_os = "linux"))]
                        libc::fcntl(raw, libc::F_SETFD, libc::FD_CLOEXEC);
                        fds.push(OwnedFd::from_raw_fd(raw));
                    }
                }
            }
            cmsg = libc::CMSG_NXTHDR(&msg, cmsg);
        }
        if msg.msg_flags & libc::MSG_CTRUNC != 0 {
            return Err(std::io::Error::other("ancillary data truncated"));
        }
        Ok((n as usize, fds))
    }
}

/// Client: send a launch request and wait for the daemon's final answer.
/// Returns the Windows program's exit code as reported by the daemon.
pub fn client_launch(socket: &Path, exe_file: &File, msg: &LaunchMessage) -> Result<i32> {
    let mut stream =
        UnixStream::connect(socket).with_context(|| format!("connect to {}", socket.display()))?;
    let body = serde_json::to_vec(msg)?;
    if body.len() > MAX_MESSAGE {
        bail!("launch request too large ({} bytes)", body.len());
    }
    let header = (body.len() as u32).to_be_bytes();
    let sent = send_with_fd(stream.as_raw_fd(), &header, exe_file.as_raw_fd())?;
    stream.write_all(&header[sent..])?;
    stream.write_all(&body)?;
    stream.flush()?;

    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    loop {
        line.clear();
        if reader.read_line(&mut line)? == 0 {
            bail!("daemon closed the connection without a result");
        }
        let l = line.trim_end();
        if l == "ACCEPTED" {
            continue;
        }
        if let Some(code) = l.strip_prefix("EXIT ") {
            return Ok(code.trim().parse().unwrap_or(1));
        }
        if let Some(err) = l.strip_prefix("ERROR: ") {
            bail!("{err}");
        }
        bail!("unexpected daemon reply: {l}");
    }
}

/// Daemon: read one launch request and the executable fd from `stream`.
pub fn server_receive(stream: &mut UnixStream) -> Result<(LaunchMessage, File)> {
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    let mut header = [0u8; 4];
    let (mut got, mut fds) = recv_with_fds(stream.as_raw_fd(), &mut header)?;
    if got == 0 {
        bail!("client closed before sending a request");
    }
    while got < header.len() {
        let n = stream.read(&mut header[got..])?;
        if n == 0 {
            bail!("truncated header");
        }
        got += n;
    }
    if fds.len() != 1 {
        bail!("expected exactly one executable fd, got {}", fds.len());
    }
    let fd = fds.pop().unwrap();
    let len = u32::from_be_bytes(header) as usize;
    if len == 0 || len > MAX_MESSAGE {
        bail!("invalid request length {len}");
    }
    let mut body = vec![0u8; len];
    stream.read_exact(&mut body)?;
    let msg: LaunchMessage = serde_json::from_slice(&body).context("malformed launch request")?;
    if !msg.exe.starts_with('/') || msg.exe.contains('\0') {
        bail!("executable path must be absolute");
    }
    Ok((msg, File::from(fd)))
}

/// Daemon: write a reply line, ignoring a client that already went away.
pub fn reply(stream: &mut UnixStream, line: &str) {
    let _ = stream.write_all(line.as_bytes());
    let _ = stream.write_all(b"\n");
    let _ = stream.flush();
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::net::UnixListener;

    fn roundtrip(msg: LaunchMessage) -> (LaunchMessage, File, i32) {
        let dir = tempfile::tempdir().unwrap();
        let sock = dir.path().join("launch.sock");
        let exe = dir.path().join("app.exe");
        std::fs::write(&exe, b"MZ fake").unwrap();
        let listener = UnixListener::bind(&sock).unwrap();
        let server = std::thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            assert_eq!(peer_uid(&s), unsafe { libc::getuid() });
            let (m, f) = server_receive(&mut s).unwrap();
            reply(&mut s, "ACCEPTED");
            reply(&mut s, "EXIT 7");
            (m, f)
        });
        let file = File::open(&exe).unwrap();
        let code = client_launch(&sock, &file, &msg).unwrap();
        let (m, f) = server.join().unwrap();
        (m, f, code)
    }

    #[test]
    fn launch_roundtrip_passes_fd_message_and_exit_code() {
        let mut env = HashMap::new();
        env.insert("DISPLAY".into(), ":0".into());
        let msg = LaunchMessage {
            exe: "/home/a/app.exe".into(),
            args: vec!["--x".into(), "a b".into()],
            env,
        };
        let (got, mut file, code) = roundtrip(msg.clone());
        assert_eq!(got, msg);
        assert_eq!(code, 7);
        let mut s = String::new();
        file.read_to_string(&mut s).unwrap();
        assert_eq!(s, "MZ fake", "received fd must be the client's open file");
    }

    #[test]
    fn server_rejects_request_without_fd() {
        let dir = tempfile::tempdir().unwrap();
        let sock = dir.path().join("l.sock");
        let listener = UnixListener::bind(&sock).unwrap();
        let server = std::thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            server_receive(&mut s).map(|_| ())
        });
        let mut c = UnixStream::connect(&sock).unwrap();
        let body = br#"{"exe":"/x.exe"}"#;
        c.write_all(&(body.len() as u32).to_be_bytes()).unwrap();
        c.write_all(body).unwrap();
        let err = server.join().unwrap().unwrap_err();
        assert!(err.to_string().contains("exactly one"), "{err}");
    }

    #[test]
    fn server_rejects_relative_path_and_oversize() {
        let dir = tempfile::tempdir().unwrap();
        let sock = dir.path().join("l.sock");
        let exe = dir.path().join("a.exe");
        std::fs::write(&exe, b"MZ").unwrap();
        let listener = UnixListener::bind(&sock).unwrap();
        let server = std::thread::spawn(move || {
            let mut out = vec![];
            for _ in 0..2 {
                let (mut s, _) = listener.accept().unwrap();
                out.push(server_receive(&mut s).map(|_| ()).unwrap_err().to_string());
            }
            out
        });
        let f = File::open(&exe).unwrap();
        // relative path
        {
            let c = UnixStream::connect(&sock).unwrap();
            let body = br#"{"exe":"rel.exe"}"#;
            let hdr = (body.len() as u32).to_be_bytes();
            send_with_fd(c.as_raw_fd(), &hdr, f.as_raw_fd()).unwrap();
            (&c).write_all(body).unwrap();
        }
        // oversize length
        {
            let c = UnixStream::connect(&sock).unwrap();
            let hdr = ((MAX_MESSAGE + 1) as u32).to_be_bytes();
            send_with_fd(c.as_raw_fd(), &hdr, f.as_raw_fd()).unwrap();
        }
        let errs = server.join().unwrap();
        assert!(errs[0].contains("absolute"), "{errs:?}");
        assert!(errs[1].contains("invalid request length"), "{errs:?}");
    }
}
