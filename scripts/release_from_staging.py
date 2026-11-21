"""Build a public GitHub Release from SHA-256-verified staging chunks.

The private build uses GitHub OIDC to stage verified files. This public GitHub
Actions workflow uses only its own GITHUB_TOKEN, never a cross-repository PAT.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen

STAGING_MANIFEST = "https://udbqsvsrbybuedqwchks.supabase.co/functions/v1/asa-terminarz-updates"
STAGING_FILES = "https://udbqsvsrbybuedqwchks.supabase.co/storage/v1/object/public/asa-terminarz-updates/"
MAX_CHUNK = 8 * 1024 * 1024
OUT = Path("release")

def sha256_text(value: object) -> str:
    value = str(value or "").lower()
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise RuntimeError("Invalid SHA-256")
    return value

def fetch_json(url: str) -> dict:
    with urlopen(Request(url, headers={"Cache-Control":"no-cache","Accept":"application/json"}), timeout=40) as resp:
        value = json.load(resp)
    if not isinstance(value, dict):
        raise RuntimeError("Invalid release manifest")
    return value

def unpack(asset: dict, version: str) -> None:
    if not isinstance(asset, dict):
        raise RuntimeError("Invalid asset")
    filename = str(asset.get("name") or "")
    if filename not in ("ASA-Terminarz-Windows.exe", f"ASA-Terminarz-Setup-v{version}.exe"):
        raise RuntimeError("Unexpected file name")
    total_size = asset.get("size")
    chunks = asset.get("chunks")
    total_hash = sha256_text(asset.get("sha256"))
    min_size = 2048 if filename == "ASA-Terminarz-Windows.exe" else 1_000_000
    if not isinstance(total_size, int) or not min_size <= total_size <= 100_000_000:
        raise RuntimeError("Invalid total file size")
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= 20:
        raise RuntimeError("Invalid chunk list")
    output_hash = hashlib.sha256()
    downloaded = 0
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / filename
    with destination.open("wb") as output:
        for index, chunk in enumerate(chunks):
            expected_path = f"v{version}/{filename}/part-{index:03d}"
            if not isinstance(chunk, dict) or chunk.get("path") != expected_path:
                raise RuntimeError("Invalid chunk path")
            size = chunk.get("size")
            digest = sha256_text(chunk.get("sha256"))
            if not isinstance(size, int) or not 1 <= size <= MAX_CHUNK:
                raise RuntimeError("Invalid chunk size")
            url = STAGING_FILES + quote(expected_path, safe="/")
            chunk_hash = hashlib.sha256()
            count = 0
            with urlopen(Request(url, headers={"Cache-Control":"no-cache"}), timeout=180) as resp:
                while True:
                    piece = resp.read(1024 * 1024)
                    if not piece:
                        break
                    count += len(piece)
                    downloaded += len(piece)
                    if count > size or downloaded > total_size:
                        raise RuntimeError("Unexpected file size")
                    chunk_hash.update(piece)
                    output_hash.update(piece)
                    output.write(piece)
            if count != size or chunk_hash.hexdigest() != digest:
                raise RuntimeError("Downloaded chunk checksum mismatch")
    if downloaded != total_size or output_hash.hexdigest() != total_hash:
        raise RuntimeError("Downloaded EXE checksum mismatch")
    with destination.open("rb") as source:
        if source.read(2) != b"MZ":
            raise RuntimeError("Published file is not a Windows EXE")
    (OUT / (filename + ".sha256")).write_text(total_hash + "  " + filename + "\n", encoding="ascii")
    print("Verified:", filename, downloaded, total_hash, flush=True)

def main() -> None:
    manifest = fetch_json(STAGING_MANIFEST)
    version = str(manifest.get("version") or "")
    if not version:
        print("No staged release. Nothing to publish.")
        return
    if not re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", version):
        raise RuntimeError("Invalid version")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != 2:
        raise RuntimeError("Expected exactly two assets")
    if {str(f.get("name") or "") for f in files} != {
        "ASA-Terminarz-Windows.exe", f"ASA-Terminarz-Setup-v{version}.exe"
    }:
        raise RuntimeError("Missing expected files")
    # Skip already published immutable versions before downloading ~110 MB.
    # This check makes the scheduled workflow cheap and avoids replacing live assets.
    release_url = (
        "https://api.github.com/repos/driver4f-ui/asa-terminarz-updates"
        "/releases/tags/" + quote("terminarz-v" + version)
    )
    try:
        release = fetch_json(release_url)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        release = None
    if release is not None:
        published = {asset.get("name") for asset in release.get("assets", [])}
        required = {
            "ASA-Terminarz-Windows.exe",
            "ASA-Terminarz-Windows.exe.sha256",
            f"ASA-Terminarz-Setup-v{version}.exe",
            f"ASA-Terminarz-Setup-v{version}.exe.sha256",
        }
        if required.issubset(published):
            print("Release already published; no download or re-upload:", version)
            return
    for asset in files:
        unpack(asset, version)
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"version={version}\n")
    print(f"Release is ready for GitHub: {version}")

if __name__ == "__main__":
    main()
