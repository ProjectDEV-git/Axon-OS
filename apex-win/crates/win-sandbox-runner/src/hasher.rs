use anyhow::{bail, Context, Result};
use sha2::{Digest, Sha256};
use std::fs::File;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

/// Size of the read buffer for streaming hash.
const CHUNK_SIZE: usize = 64 * 1024;

/// Identity of the exact file that was hashed.
///
/// Trust decisions are keyed by hash, but Wine opens the executable by path
/// later. Without an identity check, the file can be swapped between hashing
/// and exec (hash a trusted binary, exec a different one). The identity is
/// taken from `fstat` on the same fd that was hashed, and re-checked against
/// the path immediately before the tier spawns Wine. A rename, replace, or
/// in-place rewrite changes at least one of these fields.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FileIdentity {
    pub dev: u64,
    pub ino: u64,
    pub size: u64,
    pub mtime: i64,
    pub mtime_nsec: i64,
    pub ctime: i64,
    pub ctime_nsec: i64,
}

impl FileIdentity {
    pub fn from_metadata(m: &std::fs::Metadata) -> Self {
        Self {
            dev: m.dev(),
            ino: m.ino(),
            size: m.size(),
            mtime: m.mtime(),
            mtime_nsec: m.mtime_nsec(),
            ctime: m.ctime(),
            ctime_nsec: m.ctime_nsec(),
        }
    }
}

/// Compute the SHA-256 hash of a file, returning the hex-encoded digest.
pub fn hash_file(path: &str) -> Result<String> {
    Ok(hash_path_with_identity(path)?.0)
}

/// Open `path` once, hash it through that fd, and return the fd's identity.
pub fn hash_path_with_identity(path: &str) -> Result<(String, FileIdentity)> {
    let file = File::open(Path::new(path))
        .with_context(|| format!("Failed to open file for hashing: {path}"))?;
    hash_open_file(&file)
}

/// Hash an already-open file from offset 0 (does not move a shared offset:
/// uses positional reads) and return its identity.
pub fn hash_open_file(file: &File) -> Result<(String, FileIdentity)> {
    use std::os::unix::fs::FileExt;
    let before = file.metadata().context("fstat before hashing")?;
    if !before.file_type().is_file() {
        bail!("not a regular file");
    }
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; CHUNK_SIZE];
    let mut off = 0u64;
    loop {
        let n = file.read_at(&mut buf, off)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
        off += n as u64;
    }
    let after = file.metadata().context("fstat after hashing")?;
    let id = FileIdentity::from_metadata(&before);
    if FileIdentity::from_metadata(&after) != id || off != before.len() {
        bail!("file changed while it was being hashed");
    }
    Ok((format!("{:x}", hasher.finalize()), id))
}

/// Refuse to continue if `path` no longer names the file that was hashed.
pub fn verify_unchanged(path: &str, expected: Option<&FileIdentity>) -> Result<()> {
    let Some(expected) = expected else {
        return Ok(());
    };
    let now = std::fs::metadata(path).with_context(|| format!("re-checking {path} before exec"))?;
    if FileIdentity::from_metadata(&now) != *expected {
        bail!(
            "Refusing to run '{path}': the file changed after it was hashed. The trust/tier \
             decision was made for different contents."
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn hash_known_empty_string() {
        // SHA-256 of empty string
        let expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        let tmp = tempfile::NamedTempFile::new().unwrap();
        tmp.as_file().flush().unwrap();
        let hash = hash_file(tmp.path().to_str().unwrap()).unwrap();
        assert_eq!(hash, expected);
    }

    #[test]
    fn hash_known_content() {
        // SHA-256 of "hello"
        let expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
        let mut tmp = tempfile::NamedTempFile::new().unwrap();
        write!(tmp, "hello").unwrap();
        let hash = hash_file(tmp.path().to_str().unwrap()).unwrap();
        assert_eq!(hash, expected);
    }

    #[test]
    fn hash_nonexistent_file() {
        let result = hash_file("/nonexistent/path/to/file.exe");
        assert!(result.is_err());
    }

    #[test]
    fn hash_rejects_directory() {
        assert!(hash_file("/tmp").is_err());
    }

    /// Hash-then-swap: replacing the file after hashing must be caught.
    #[test]
    fn verify_detects_replacement() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("a.exe");
        std::fs::write(&p, b"trusted").unwrap();
        let ps = p.to_str().unwrap();
        let (_, id) = hash_path_with_identity(ps).unwrap();
        assert!(verify_unchanged(ps, Some(&id)).is_ok());

        let other = dir.path().join("b.exe");
        std::fs::write(&other, b"evil!!!").unwrap();
        std::fs::rename(&other, &p).unwrap();
        assert!(verify_unchanged(ps, Some(&id)).is_err());
    }

    #[test]
    fn verify_detects_in_place_rewrite() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("a.exe");
        std::fs::write(&p, b"trusted").unwrap();
        let ps = p.to_str().unwrap();
        let (_, id) = hash_path_with_identity(ps).unwrap();
        std::fs::write(&p, b"different-length").unwrap();
        assert!(verify_unchanged(ps, Some(&id)).is_err());
    }
}
