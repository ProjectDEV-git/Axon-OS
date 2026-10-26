use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Isolation tier levels (0 = least restrictive, 3 = most restrictive).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Tier {
    /// Direct wine execution, no sandboxing.
    Tier0,
    /// Landlock LSM filesystem + network restrictions.
    Tier1,
    /// Bubblewrap container with namespace isolation.
    Tier2,
    /// OverlayFS + RAM ephemeral (changes lost on exit).
    Tier3,
}

#[derive(Debug, Error)]
#[error("invalid tier value: {0}")]
pub struct InvalidTier(pub String);

impl Tier {
    /// Parse a tier from a numeric string ("0" through "3").
    pub fn from_str_level(s: &str) -> Result<Self, InvalidTier> {
        match s {
            "0" => Ok(Self::Tier0),
            "1" => Ok(Self::Tier1),
            "2" => Ok(Self::Tier2),
            "3" => Ok(Self::Tier3),
            _ => Err(InvalidTier(s.to_string())),
        }
    }

    /// Return the numeric level (0–3).
    pub fn level(&self) -> u8 {
        match self {
            Self::Tier0 => 0,
            Self::Tier1 => 1,
            Self::Tier2 => 2,
            Self::Tier3 => 3,
        }
    }
}

impl std::fmt::Display for Tier {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.level())
    }
}

impl std::str::FromStr for Tier {
    type Err = InvalidTier;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Self::from_str_level(s)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tier_round_trip() {
        for tier in [Tier::Tier0, Tier::Tier1, Tier::Tier2, Tier::Tier3] {
            let json = serde_json::to_string(&tier).unwrap();
            let parsed: Tier = serde_json::from_str(&json).unwrap();
            assert_eq!(tier, parsed);
        }
    }

    #[test]
    fn tier_from_str() {
        assert_eq!(Tier::from_str_level("0").unwrap(), Tier::Tier0);
        assert_eq!(Tier::from_str_level("3").unwrap(), Tier::Tier3);
        assert!(Tier::from_str_level("5").is_err());
        assert!(Tier::from_str_level("abc").is_err());
    }

    #[test]
    fn tier_ordering() {
        assert!(Tier::Tier0 < Tier::Tier3);
        assert!(Tier::Tier2 > Tier::Tier1);
    }
}
