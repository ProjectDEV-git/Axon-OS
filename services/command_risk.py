"""Risk level and effects of a shell command, for the confirmation card.

This only *describes* a command so the user can decide. It never makes a
command allowed: ``service_utils.validate_command`` (the allowlist) runs
first and stays the gate. Unknown commands get a cautious level.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

READ_ONLY = "read_only"
CHANGES_FILES = "changes_files"
DELETES_FILES = "deletes_files"
NEEDS_ROOT = "needs_root"

# Lowest to highest.
LEVELS = (READ_ONLY, CHANGES_FILES, DELETES_FILES, NEEDS_ROOT)

LEVEL_LABELS = {
    READ_ONLY: "Read-only",
    CHANGES_FILES: "Changes files",
    DELETES_FILES: "Deletes files",
    NEEDS_ROOT: "Needs admin rights",
}

LEVEL_DESCRIPTIONS = {
    READ_ONLY: "Shows information. Doesn't change anything on your computer.",
    CHANGES_FILES: "Can create or change files.",
    DELETES_FILES: "Can delete files. This may not be undoable.",
    NEEDS_ROOT: "Runs with administrator rights and can change the whole system.",
}

_ROOT_PREFIXES = frozenset({"sudo", "pkexec", "doas", "su", "run0"})
_DELETERS = frozenset({"rm", "rmdir", "unlink", "shred", "trash", "gio-trash"})
_WRITERS = frozenset(
    {"cp", "mv", "touch", "mkdir", "tee", "dd", "ln", "chmod", "chown", "truncate", "install"}
)
# Commands that only read or print. Matches the read-only part of
# service_utils.ALLOWED_COMMANDS.
_READERS = frozenset(
    {
        "ls",
        "cat",
        "grep",
        "echo",
        "date",
        "whoami",
        "hostname",
        "uname",
        "df",
        "du",
        "free",
        "uptime",
        "ps",
        "pwd",
        "wc",
        "head",
        "tail",
        "diff",
        "file",
        "stat",
        "readlink",
        "realpath",
        "basename",
        "dirname",
    }
)
# Harmless side effects that change no files.
_SIDE_EFFECT_ONLY = {
    "paplay": "Plays a sound.",
    "notify-send": "Shows a desktop notification.",
}


@dataclass
class CommandRisk:
    """What a command would do, as far as Axon can tell."""

    level: str
    effects: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LEVEL_LABELS[self.level]

    @property
    def description(self) -> str:
        return LEVEL_DESCRIPTIONS[self.level]

    @property
    def needs_root(self) -> bool:
        return self.level == NEEDS_ROOT


def _paths(args: list[str]) -> list[str]:
    return [a for a in args if a and not a.startswith("-")]


def assess(command: str) -> CommandRisk:
    """Return the risk level and known effects of *command*.

    Unparseable or unknown commands get ``changes_files`` so they are never
    shown as harmless.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        return CommandRisk(CHANGES_FILES, ["Axon couldn't read this command."])
    if not argv:
        return CommandRisk(CHANGES_FILES, ["Empty command."])

    binary, args = argv[0], argv[1:]
    if binary in _ROOT_PREFIXES:
        return CommandRisk(NEEDS_ROOT, ["Asks for your password and runs as administrator."])
    if binary in _DELETERS:
        targets = _paths(args)
        return CommandRisk(DELETES_FILES, [f"Deletes: {', '.join(targets) or '(nothing named)'}"])
    if binary in _WRITERS:
        targets = _paths(args)
        return CommandRisk(CHANGES_FILES, [f"Writes: {', '.join(targets) or '(nothing named)'}"])

    if binary == "sort":
        for i, arg in enumerate(args):
            if arg in ("-o", "--output") and i + 1 < len(args):
                return CommandRisk(CHANGES_FILES, [f"Writes: {args[i + 1]}"])
            if arg.startswith("--output="):
                return CommandRisk(CHANGES_FILES, [f"Writes: {arg.split('=', 1)[1]}"])
            if arg.startswith("-o") and len(arg) > 2:
                return CommandRisk(CHANGES_FILES, [f"Writes: {arg[2:]}"])
        return CommandRisk(READ_ONLY, _reads(args))
    if binary == "uniq":
        paths = _paths(args)
        if len(paths) >= 2:  # uniq INPUT OUTPUT writes OUTPUT
            return CommandRisk(CHANGES_FILES, [f"Reads: {paths[0]}", f"Writes: {paths[1]}"])
        return CommandRisk(READ_ONLY, _reads(args))

    if binary in _SIDE_EFFECT_ONLY:
        return CommandRisk(READ_ONLY, [_SIDE_EFFECT_ONLY[binary]])
    if binary in _READERS:
        return CommandRisk(READ_ONLY, _reads(args) if binary != "echo" else ["Prints text."])
    return CommandRisk(CHANGES_FILES, ["Axon doesn't know what this command changes."])


def _reads(args: list[str]) -> list[str]:
    paths = _paths(args)
    return [f"Reads: {', '.join(paths)}"] if paths else ["Shows information."]


def can_always_allow(risk: CommandRisk) -> bool:
    """ "Always allow" is offered only at the lowest risk level.

    Never for commands that need root, delete files or change files.
    """
    return risk.level == READ_ONLY
