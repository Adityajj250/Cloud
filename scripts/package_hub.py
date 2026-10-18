"""Build a deployment ZIP without local credentials, certificates, or build output."""
from __future__ import annotations

from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "rift-hub.zip"
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".secrets", ".state", ".gradle", "build"}
SKIP_PARTS = {".codex", ".pytest_cache", ".mypy_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".private.key", ".pem.crt", ".public.key", ".pem", ".p12", ".pfx", ".sqlite", ".db", ".log"}
INCLUDE_ROOTS = ("server", "agent", "pwa", "infra/ec2")
INCLUDE_FILES = ("README.md",)


def allowed(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in SKIP_DIRS or part in SKIP_PARTS for part in relative.parts):
        return False
    if path.suffix.lower() in SKIP_SUFFIXES or path.name.endswith(tuple(SKIP_SUFFIXES)):
        return False
    if relative.parts[:2] == ("agent", "certs"):
        return path.name == "AmazonRootCA1.pem"
    return True


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    added: list[str] = []
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for root_name in INCLUDE_ROOTS:
            base = ROOT / root_name
            for path in sorted(base.rglob("*")):
                if path.is_file() and allowed(path):
                    archive.write(path, path.relative_to(ROOT).as_posix())
                    added.append(path.relative_to(ROOT).as_posix())
        for name in INCLUDE_FILES:
            path = ROOT / name
            if path.is_file() and allowed(path):
                archive.write(path, name)
                added.append(name)
    unsafe = [name for name in added if any(secret in name.lower() for secret in ("private.key", ".secrets/", "local.ps1"))]
    if unsafe:
        OUTPUT.unlink(missing_ok=True)
        raise SystemExit(f"Refusing to package sensitive path(s): {unsafe}")
    print(f"Created {OUTPUT} with {len(added)} files ({OUTPUT.stat().st_size:,} bytes).")
    print("Review the archive manifest before uploading it. Local secrets and private keys are excluded.")


if __name__ == "__main__":
    main()
