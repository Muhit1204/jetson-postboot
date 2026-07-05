"""Tests for lib/backup.py: timestamped copies into ./backups/ (GUARDRAILS 3.2)."""

import unittest

from jetson_postboot.lib import backup
from tests.support import make_work_dir


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.work = make_work_dir(self)
        self.backups = self.work / "backups"

    def test_backup_file_copies_content(self):
        src = self.work / "fstab"
        src.write_text("UUID=abc / ext4 defaults 0 1\n", encoding="utf-8")
        dest = backup.backup_file(src, self.backups)
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.parent, self.backups)
        self.assertEqual(
            dest.read_text(encoding="utf-8"), src.read_text(encoding="utf-8")
        )
        self.assertTrue(dest.name.endswith("-fstab"))

    def test_backup_file_never_overwrites(self):
        src = self.work / "fstab"
        src.write_text("one", encoding="utf-8")
        first = backup.backup_file(src, self.backups, timestamp="20260704-000000")
        src.write_text("two", encoding="utf-8")
        second = backup.backup_file(src, self.backups, timestamp="20260704-000000")
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding="utf-8"), "one")
        self.assertEqual(second.read_text(encoding="utf-8"), "two")

    def test_backup_text_writes_dump(self):
        dest = backup.backup_text("sfdisk-nvme0n1.dump", "label: gpt\n", self.backups)
        self.assertEqual(dest.read_text(encoding="utf-8"), "label: gpt\n")
        self.assertIn("sfdisk-nvme0n1.dump", dest.name)

    def test_missing_source_raises(self):
        with self.assertRaises(OSError):
            backup.backup_file(self.work / "absent", self.backups)


if __name__ == "__main__":
    unittest.main()
