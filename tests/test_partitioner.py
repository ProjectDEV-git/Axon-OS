"""Unit tests for partitioner using dry-run mode (no root or external tools required)."""

from installer.partitioner import Partitioner


def test_get_partition_map_dry_run():
    p = Partitioner(dry_run=True)
    pm = p.get_partition_map("/dev/sda")
    assert isinstance(pm, list)
    assert len(pm) >= 2
    assert all("num" in entry and "start" in entry and "end" in entry for entry in pm)


def test_create_partitions_dry_run_does_not_raise():
    p = Partitioner(dry_run=True)
    # Should not raise when dry_run=True
    p.create_partitions("/dev/sda")


def test_partition_alongside_dry_run_returns_number_or_none():
    p = Partitioner(dry_run=True)
    new_part = p.partition_alongside("/dev/sda", 2, 5)  # shrink by 5GB
    # In dry-run we expect an int (partition index) or None on failure
    assert (isinstance(new_part, int) or new_part is None)
