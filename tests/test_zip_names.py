"""Имена файлов в zip: архивы из macOS/Linux (UTF-8 без флага) и из русской Windows (cp866)."""
from __future__ import annotations

import sys
import tempfile
import unicodedata
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ingest import collect_files  # noqa: E402


class RawNameInfo(zipfile.ZipInfo):
    """ZipInfo, записывающий имя в заданной кодировке без флага UTF-8 (как это делают macOS и русская Windows)."""
    raw_name = b""

    def _encodeFilenameFlags(self):
        return self.raw_name, 0


def write_zip(path: Path, members: dict[str, bytes], encoding: str | None) -> None:
    """encoding=None — обычная запись (UTF-8 с флагом); иначе имя кодируется вручную и флаг UTF-8 НЕ ставится."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            if encoding:
                raw = name.encode(encoding)
                info = RawNameInfo(raw.decode("cp437"))       # так zipfile видит имя без флага UTF-8
                info.raw_name = raw
            else:
                info = zipfile.ZipInfo(name)
            zf.writestr(info, data)


class ZipNamesTest(unittest.TestCase):
    NAME = "Это файл со скриншотом.pdf"

    def names(self, encoding):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_zip(tmp / "a.zip", {f"Папка/{self.NAME}": b"x"}, encoding)
            rep = collect_files(tmp / "a.zip", tmp / "work")
            return [f.rel_path for f in rep.files]

    def test_utf8_without_flag_macos(self):
        self.assertEqual(self.names("utf-8"), [f"a.zip/Папка/{self.NAME}"])

    def test_cp866_without_flag_windows(self):
        self.assertEqual(self.names("cp866"), [f"a.zip/Папка/{self.NAME}"])

    def test_utf8_with_flag(self):
        self.assertEqual(self.names(None), [f"a.zip/Папка/{self.NAME}"])

    def test_decomposed_letters_are_normalized(self):
        nfd = unicodedata.normalize("NFD", "файл.pdf")           # й = и + знак краткой: так пишет macOS
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_zip(tmp / "a.zip", {nfd: b"x"}, "utf-8")
            (f,) = collect_files(tmp / "a.zip", tmp / "work").files
        self.assertEqual(f.rel_path, "a.zip/файл.pdf")

    def test_macos_service_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_zip(tmp / "a.zip", {"doc.pdf": b"x", "__MACOSX/._doc.pdf": b"x", "._doc2.pdf": b"x"}, None)
            files = [f.rel_path for f in collect_files(tmp / "a.zip", tmp / "work").files]
        self.assertEqual(files, ["a.zip/doc.pdf"])


if __name__ == "__main__":
    unittest.main()
