"""Bounded, root-confined file service for the R.I.F.T. agent."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import secrets
import shutil
import stat
import time
from pathlib import Path
from typing import Any


CHUNK_LIMIT = 512 * 1024
MAX_UPLOAD_SIZE = 4 * 1024 * 1024 * 1024
QUARANTINED_EXTENSIONS = {
    ".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".lnk",
    ".scr", ".dll", ".com", ".hta", ".reg", ".jar", ".py", ".sh",
}
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class FilesService:
    """Expose a common shared folder for browsing, downloads, and phone uploads."""

    def __init__(self) -> None:
        app_data = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "RIFT"
        configured_root = os.getenv("RIFT_FILES_ROOT")
        default_root = str(Path.home() / "RIFT Shared")
        shared_root = os.path.expandvars(os.path.expanduser(configured_root)) if configured_root else default_root
        self.shared_root = Path(shared_root).resolve()
        self.shared_root.mkdir(parents=True, exist_ok=True)
        app_data.mkdir(parents=True, exist_ok=True)
        # Keep legacy RIFT_INBOX_DIR uploads working as a migration source, but
        # new uploads land in the same folder the phone browses and downloads.
        self.inbox_root = Path(os.getenv("RIFT_INBOX_DIR") or self.shared_root).expanduser().resolve()
        self.inbox_root.mkdir(parents=True, exist_ok=True)
        self.quarantine_root = (self.shared_root / "Quarantine").resolve()
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        self._paths: dict[str, tuple[str, str]] = {"shared-root": ("shared", ""), "inbox-root": ("inbox", "")}
        self._uploads: dict[str, dict[str, Any]] = {}

    def _root(self, scope: str) -> Path:
        if scope == "shared":
            return self.shared_root
        if scope == "inbox":
            return self.inbox_root
        raise ValueError("Unknown files folder")

    @staticmethod
    def _safe_name(name: str) -> str:
        name = str(name or "upload").replace("\\", "/").split("/")[-1]
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
        if not name or name in {".", ".."}:
            name = "upload"
        stem = name.split(".", 1)[0].upper()
        if stem in RESERVED_NAMES:
            name = f"_{name}"
        return name[:180]

    def _resolve(self, scope: str, relative: str) -> Path:
        root = self._root(scope)
        raw = root / relative
        current = root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Symbolic links are not shared")
        candidate = raw.resolve(strict=True)
        if candidate != root and root not in candidate.parents:
            raise ValueError("Path is outside the configured folder")
        if candidate != root and candidate.is_symlink():
            raise ValueError("Symbolic links are not shared")
        return candidate

    def list_entries(self, path_id: str = "shared-root", limit: int = 200, offset: int = 0) -> dict[str, Any]:
        if path_id not in self._paths:
            raise ValueError("Folder link expired; refresh the folder")
        scope, relative = self._paths[path_id]
        folder = self._resolve(scope, relative) if relative else self._root(scope)
        if not folder.is_dir():
            raise ValueError("That item is not a folder")
        entries = []
        candidates = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
        hidden_attributes = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2) | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
        visible = []
        for item in candidates:
            if item.name.startswith(".rift-") or item.name == "Quarantine":
                continue
            try:
                attrs = getattr(item.stat(), "st_file_attributes", 0)
                if attrs & hidden_attributes or item.name.startswith("."):
                    continue
            except OSError:
                continue
            visible.append(item)
        offset = max(0, int(offset))
        page = visible[offset:offset + min(max(int(limit), 1), 200) + 1]
        has_more = len(page) > min(max(int(limit), 1), 200)
        page = page[:min(max(int(limit), 1), 200)]
        for item in page:
            try:
                resolved = item.resolve(strict=True)
                if resolved != self._root(scope) and self._root(scope) not in resolved.parents:
                    continue
                if item.is_symlink():
                    continue
                item_stat = item.stat()
            except (OSError, RuntimeError):
                continue
            child_relative = (Path(relative) / item.name).as_posix()
            token = secrets.token_urlsafe(18)
            if item.is_dir():
                self._paths[token] = (scope, child_relative)
                entry = {"pathId": token, "kind": "directory"}
            else:
                self._paths[token] = (scope, child_relative)
                entry = {"fileId": token, "kind": "file", "size": item_stat.st_size,
                         "mime": mimetypes.guess_type(item.name)[0] or "application/octet-stream"}
            entries.append({"name": item.name, "modified": int(item_stat.st_mtime), **entry})
        return {"scope": scope, "rootName": "RIFT Shared" if scope == "shared" else "RIFT Inbox",
                "rootPath": str(self._root(scope)),
                "entries": entries, "parentId": None if not relative else path_id,
                "nextOffset": offset + len(page) if has_more else None}

    def stat_file(self, file_id: str) -> dict[str, Any]:
        if file_id not in self._paths:
            raise ValueError("File link expired; refresh the folder")
        scope, relative = self._paths[file_id]
        if not relative:
            raise ValueError("That item is not a file")
        path = self._resolve(scope, relative)
        if not path.is_file():
            raise ValueError("That item is not a file")
        stat = path.stat()
        return {"size": stat.st_size, "name": path.name,
                "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream"}

    def read_file(self, file_id: str, offset: int, length: int) -> bytes:
        if not 0 <= offset <= (2**63 - 1) or not 1 <= length <= CHUNK_LIMIT:
            raise ValueError("Invalid file range")
        scope, relative = self._paths.get(file_id, ("", ""))
        if not relative:
            raise ValueError("File link expired; refresh the folder")
        path = self._resolve(scope, relative)
        if not path.is_file():
            raise ValueError("That item is not a file")
        with path.open("rb") as handle:
            handle.seek(offset)
            return handle.read(length)

    def start_upload(self, upload_id: str, name: str, size: int) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", upload_id):
            raise ValueError("Invalid upload id")
        size = int(size)
        if size < 0 or size > MAX_UPLOAD_SIZE:
            raise ValueError("File exceeds the 4 GB upload limit")
        safe_name = self._safe_name(name)
        part = self.shared_root / f".rift-{upload_id}.part"
        meta = self.shared_root / f".rift-{upload_id}.json"
        if meta.exists():
            import json
            data = json.loads(meta.read_text(encoding="utf-8"))
            if data.get("size") != size or data.get("name") != safe_name:
                raise ValueError("Upload id is already in use")
        else:
            import json
            meta.write_text(json.dumps({"name": safe_name, "size": size}), encoding="utf-8")
        current_size = part.stat().st_size if part.exists() else 0
        if current_size > size:
            part.unlink()
            current_size = 0
        part.touch(exist_ok=True)
        self._uploads[upload_id] = {"name": safe_name, "size": size}
        return {"uploadId": upload_id, "name": safe_name, "resumeFrom": current_size, "chunkSize": CHUNK_LIMIT}

    def write_upload_chunk(self, upload_id: str, offset: int, payload: bytes) -> dict[str, int]:
        if upload_id not in self._uploads:
            raise ValueError("Upload not found; start it again to resume")
        if not payload or len(payload) > CHUNK_LIMIT:
            raise ValueError("Upload chunk must be between 1 byte and 512 KB")
        info = self._uploads[upload_id]
        part = self.shared_root / f".rift-{upload_id}.part"
        current = part.stat().st_size if part.exists() else 0
        if offset != current or current + len(payload) > info["size"]:
            raise ValueError(f"Unexpected upload offset; server has {current} bytes")
        with part.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        received = current + len(payload)
        return {"received": received, "size": info["size"]}

    def finish_upload(self, upload_id: str) -> dict[str, Any]:
        info = self._uploads.get(upload_id)
        meta = self.shared_root / f".rift-{upload_id}.json"
        part = self.shared_root / f".rift-{upload_id}.part"
        if not info and meta.exists():
            import json
            info = json.loads(meta.read_text(encoding="utf-8"))
        if not info:
            raise ValueError("Upload not found")
        received = part.stat().st_size if part.exists() else 0
        if received != info["size"]:
            raise ValueError(f"Upload incomplete: received {received} of {info['size']} bytes")
        extension = Path(info["name"]).suffix.lower()
        quarantined = extension in QUARANTINED_EXTENSIONS
        destination_root = self.quarantine_root if quarantined else self.shared_root
        destination = destination_root / info["name"]
        stem, suffix = destination.stem, destination.suffix
        index = 1
        while destination.exists():
            destination = destination_root / f"{stem} ({index}){suffix}"
            index += 1
        digest = hashlib.sha256()
        with part.open("rb") as handle:
            for block in iter(lambda: handle.read(CHUNK_LIMIT), b""):
                digest.update(block)
        shutil.move(str(part), str(destination))
        meta.unlink(missing_ok=True)
        self._uploads.pop(upload_id, None)
        return {"name": destination.name, "size": received, "sha256": digest.hexdigest(),
                "quarantined": quarantined, "path": "RIFT Shared\\Quarantine" if quarantined else "RIFT Shared",
                "fullPath": str(destination), "rootPath": str(self.shared_root)}

