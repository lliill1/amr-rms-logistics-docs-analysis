"""Загрузка архива: распаковка (в т.ч. вложенных архивов), конвертация старых форматов, учёт дубликатов."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

ARCHIVE_EXTS = {".zip", ".7z", ".rar"}
# старые/офисные форматы -> во что конвертировать через LibreOffice
CONVERT_MAP = {
    ".doc": "docx", ".rtf": "docx", ".odt": "docx",
    ".xls": "xlsx", ".ods": "xlsx",
    ".ppt": "pdf", ".pptx": "pdf", ".odp": "pdf",
    ".vsd": "pdf", ".vsdx": "pdf",
}
SKIP_NAMES = {"thumbs.db", ".ds_store", "desktop.ini"}


@dataclass
class FoundFile:
    rel_path: str      # путь для пользователя: «Архив.zip/02_EWM/ПР_EWM.docx»
    path: Path         # реальный путь на диске


@dataclass
class IngestReport:
    files: list[FoundFile] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (путь, причина)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip_member_name(info: zipfile.ZipInfo) -> str:
    """Архивы, созданные в русской Windows, хранят имена в cp866 без флага UTF-8."""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("cp866")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def _safe_target(root: Path, member: str) -> Path | None:
    parts = [p for p in PurePosixPath(member.replace("\\", "/")).parts if p not in ("", ".", "..", "/")]
    if not parts:
        return None
    target = root.joinpath(*parts).resolve()
    if not str(target).startswith(str(root.resolve())):
        return None  # защита от zip-slip
    return target


def extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    ext = archive.suffix.lower()
    if ext == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                target = _safe_target(dest, _zip_member_name(info))
                if target is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    elif ext == ".7z":
        try:
            import py7zr  # type: ignore
        except ImportError as e:
            raise RuntimeError("для .7z установите пакет py7zr или распакуйте архив вручную") from e
        with py7zr.SevenZipFile(archive) as z:
            z.extractall(dest)
    elif ext == ".rar":
        try:
            import rarfile  # type: ignore
        except ImportError as e:
            raise RuntimeError("для .rar установите пакет rarfile (и unrar) или распакуйте архив вручную") from e
        with rarfile.RarFile(archive) as r:
            r.extractall(dest)
    else:
        raise RuntimeError(f"неизвестный формат архива {ext}")


def find_soffice(configured: str | None = None) -> str | None:
    if configured and Path(configured).exists():
        return configured
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def convert_with_libreoffice(src: Path, target_ext: str, out_dir: Path, soffice: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    # отдельный профиль, чтобы не конфликтовать с открытым у пользователя LibreOffice
    profile = (out_dir / ".lo_profile").resolve().as_uri()
    cmd = [soffice, f"-env:UserInstallation={profile}", "--headless", "--convert-to", target_ext,
           "--outdir", str(out_dir), str(src)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    result = out_dir / f"{src.stem}.{target_ext}"
    if not result.exists():
        raise RuntimeError("LibreOffice не создал файл")
    return result


def collect_files(source: Path, work_dir: Path, _prefix: str = "", _depth: int = 0) -> IngestReport:
    """Собирает все файлы из папки или архива. Вложенные архивы распаковываются (до 5 уровней)."""
    report = IngestReport()
    source = Path(source)
    if source.is_file() and source.suffix.lower() in ARCHIVE_EXTS:
        dest = work_dir / "unpacked" / sha256_of(source)[:12]
        try:
            if not dest.exists():
                extract_archive(source, dest)
        except Exception as e:  # noqa: BLE001
            report.skipped.append((_prefix + source.name, f"не удалось распаковать: {e}"))
            return report
        sub = collect_files(dest, work_dir, _prefix + source.name + "/", _depth + 1)
        report.files += sub.files
        report.skipped += sub.skipped
        return report
    if source.is_file():
        report.files.append(FoundFile(_prefix + source.name, source))
        return report

    for dirpath, _dirs, filenames in os.walk(source):
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = _prefix + p.relative_to(source).as_posix()
            if fn.lower() in SKIP_NAMES or fn.startswith("~$"):
                continue
            if p.suffix.lower() in ARCHIVE_EXTS:
                if _depth >= 5:
                    report.skipped.append((rel, "слишком глубокая вложенность архивов"))
                    continue
                sub = collect_files(p, work_dir, rel[: -len(fn)], _depth + 1)
                report.files += sub.files
                report.skipped += sub.skipped
            else:
                report.files.append(FoundFile(rel, p))
    return report
