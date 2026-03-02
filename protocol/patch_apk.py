#!/usr/bin/env python3
"""
Automated APK patcher for Kingdom Guard protocol interception.

Takes an XAPK, a directory of split APKs, or individual APK files and produces
a patched, signed, installable set of splits with Frida Gadget injected.

Usage:
    python -m protocol.patch_apk KingdomGuard.xapk
    python -m protocol.patch_apk ./splits/
    python -m protocol.patch_apk base.apk split_arm64.apk split_unity.apk
    python -m protocol.patch_apk KingdomGuard.xapk --install
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import io
import json
import lzma
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Pure Python signing (when Java/Android SDK build-tools are not installed)
try:
    import cryptography as _cryptography  # noqa: F401
    _HAVE_CRYPTOGRAPHY = True
except ImportError:
    _HAVE_CRYPTOGRAPHY = False

# ---------------------------------------------------------------------------
# ANSI helpers (disabled on Windows unless modern terminal detected)
# ---------------------------------------------------------------------------

_COLOR = (
    os.name != "nt"
    or "WT_SESSION" in os.environ
    or os.environ.get("TERM_PROGRAM") == "vscode"
)


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def green(t: str) -> str: return _c("32", t)
def red(t: str) -> str: return _c("31", t)
def yellow(t: str) -> str: return _c("33", t)
def cyan(t: str) -> str: return _c("36", t)
def bold(t: str) -> str: return _c("1", t)
def dim(t: str) -> str: return _c("2", t)


OK = green("[OK]")
FAIL = red("[FAIL]")
WARN = yellow("[WARN]")
INFO = cyan("[*]")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKAGE = "com.tap4fun.odin.kingdomguard"

GADGET_CONFIG = {
    "interaction": {
        "type": "listen",
        "address": "0.0.0.0",
        "port": 27042,
        "on_port_conflict": "pick-next",
        "on_load": "resume",
    }
}

# Names we look for when identifying the three required split APKs
_ARM64_PATTERNS = ("split_config.arm64_v8a", "split_arm64", "arm64")
_UNITY_PATTERNS = ("split_unitydataassetpack", "split_unity_data", "unity")
_BASE_PATTERNS = ("base",)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class PatchError(Exception):
    """Raised when a patching step fails."""


def _abort(msg: str, code: int = 1) -> None:
    print(f"\n{FAIL} {red(msg)}")
    sys.exit(code)


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

def _step(n: int, total: int, msg: str) -> None:
    print(f"\n{bold(cyan(f'[{n}/{total}]'))} {bold(msg)}")


def _run(cmd: list[str], env: dict | None = None,
         timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a command, print it, return result."""
    display = " ".join(cmd) if len(" ".join(cmd)) < 120 else " ".join(cmd[:4]) + " ..."
    print(f"  $ {dim(display)}")
    result = subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=timeout
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        for line in stderr.split("\n")[:5]:
            print(f"    {red(line)}")
    return result


# ---------------------------------------------------------------------------
# Step 1: Resolve input
# ---------------------------------------------------------------------------

def _classify_apk(filename: str) -> str | None:
    """Classify an APK filename as 'base', 'arm64', or 'unity'. None if unknown."""
    lower = filename.lower().replace(".apk", "")
    for pat in _ARM64_PATTERNS:
        if pat in lower:
            return "arm64"
    for pat in _UNITY_PATTERNS:
        if pat in lower:
            return "unity"
    for pat in _BASE_PATTERNS:
        if lower == pat or lower.endswith(pat):
            return "base"
    return None


def resolve_input(inputs: list[str], output_dir: str | None,
                  ) -> tuple[dict[str, str], str]:
    """Resolve CLI input(s) to a dict of {role: apk_path} and an output dir.

    Supports:
      - Single .xapk file (ZIP containing splits)
      - Single directory containing split APKs
      - 1-3 individual APK paths
    """
    print(f"\n{INFO} Resolving input ...")

    splits: dict[str, str] = {}
    resolved_output = output_dir

    if len(inputs) == 1:
        path = inputs[0]

        # XAPK file
        if path.lower().endswith(".xapk") or (
            os.path.isfile(path) and zipfile.is_zipfile(path)
            and not path.lower().endswith(".apk")
        ):
            return _resolve_xapk(path, resolved_output)

        # Directory
        if os.path.isdir(path):
            return _resolve_directory(path, resolved_output)

        # Single APK (must be the arm64 split at minimum)
        if os.path.isfile(path):
            role = _classify_apk(os.path.basename(path))
            if role:
                splits[role] = os.path.abspath(path)
            else:
                # Assume it's the arm64 split if we can't classify
                splits["arm64"] = os.path.abspath(path)
            if resolved_output is None:
                resolved_output = os.path.join(os.path.dirname(os.path.abspath(path)), "patched")
        else:
            _abort(f"Input not found: {path}")

    else:
        # Multiple APK files
        for p in inputs:
            if not os.path.isfile(p):
                _abort(f"File not found: {p}")
            role = _classify_apk(os.path.basename(p))
            if role is None:
                _abort(f"Cannot classify APK: {os.path.basename(p)}\n"
                       f"  Expected base.apk, split_config.arm64_v8a.apk, or "
                       f"split_UnityDataAssetPack.apk")
            if role in splits:
                _abort(f"Duplicate {role} APK: {os.path.basename(p)}")
            splits[role] = os.path.abspath(p)

        if resolved_output is None:
            resolved_output = os.path.join(
                os.path.dirname(os.path.abspath(inputs[0])), "patched"
            )

    if "arm64" not in splits:
        _abort("Missing arm64 split APK (the one containing libil2cpp.so).\n"
               "  Expected a file matching: split_config.arm64_v8a.apk")

    for role, path in sorted(splits.items()):
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"  {OK} {role}: {os.path.basename(path)} ({size_mb:.1f} MB)")

    if "base" not in splits:
        print(f"  {WARN} No base.apk found — only the arm64 split will be patched + signed")
    if "unity" not in splits:
        print(f"  {WARN} No unity data split found — only provided splits will be processed")

    return splits, resolved_output


def _resolve_xapk(xapk_path: str, output_dir: str | None,
                   ) -> tuple[dict[str, str], str]:
    """Extract splits from an XAPK (ZIP) file."""
    xapk_path = os.path.abspath(xapk_path)
    print(f"  XAPK: {os.path.basename(xapk_path)} "
          f"({os.path.getsize(xapk_path) / (1024 * 1024):.1f} MB)")

    extract_dir = os.path.join(os.path.dirname(xapk_path), "xapk_extracted")
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir)

    with zipfile.ZipFile(xapk_path, "r") as zf:
        apk_entries = [n for n in zf.namelist() if n.lower().endswith(".apk")]
        if not apk_entries:
            _abort("No .apk files found inside XAPK")
        for entry in apk_entries:
            zf.extract(entry, extract_dir)
            print(f"  Extracted: {entry}")

    return _resolve_directory(extract_dir, output_dir or os.path.join(
        os.path.dirname(xapk_path), "patched"
    ))


def _resolve_directory(dir_path: str, output_dir: str | None,
                       ) -> tuple[dict[str, str], str]:
    """Find split APKs in a directory."""
    dir_path = os.path.abspath(dir_path)
    apks = sorted(glob.glob(os.path.join(dir_path, "*.apk")))
    if not apks:
        _abort(f"No .apk files found in {dir_path}")

    splits: dict[str, str] = {}
    for apk in apks:
        role = _classify_apk(os.path.basename(apk))
        if role and role not in splits:
            splits[role] = apk

    if "arm64" not in splits:
        _abort(f"No arm64 split found in {dir_path}\n"
               f"  Files: {', '.join(os.path.basename(a) for a in apks)}")

    for role, path in sorted(splits.items()):
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"  {OK} {role}: {os.path.basename(path)} ({size_mb:.1f} MB)")

    if output_dir is None:
        output_dir = os.path.join(dir_path, "patched")

    return splits, output_dir


# ---------------------------------------------------------------------------
# Pure Python APK signing (v1 / JAR signing)
# ---------------------------------------------------------------------------
# Used automatically when Java JDK and Android SDK build-tools are not
# installed.  Requires: pip install cryptography
#
# Implements: MANIFEST.MF + CERT.SF + CERT.RSA (PKCS#7) + zipalign.
# Produces APK Signature Scheme v1 signatures — sufficient for all
# Android versions (v2/v3 improve install speed but are not required).


def _generate_debug_key(key_dir: str):
    """Generate or load RSA-2048 debug signing key + self-signed certificate."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key_path = os.path.join(key_dir, "debug.key")
    cert_path = os.path.join(key_dir, "debug.cert")

    if os.path.isfile(key_path) and os.path.isfile(cert_path):
        with open(key_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        print(f"  {OK} Using existing debug key")
        return key, cert

    os.makedirs(key_dir, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Debug"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Debug"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=10000))
        .sign(key, hashes.SHA256())
    )
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"  {OK} Generated debug signing key")
    return key, cert


def _build_manifest_mf(entries: list[tuple[str, bytes]]) -> bytes:
    """Build MANIFEST.MF content for JAR/APK v1 signing.

    *entries* is a list of ``(zip_entry_name, uncompressed_data)`` tuples.
    """
    mf = b"Manifest-Version: 1.0\r\nCreated-By: 1.0 (Android SignApk)\r\n\r\n"
    for name, data in entries:
        digest = base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")
        mf += f"Name: {name}\r\nSHA-256-Digest: {digest}\r\n\r\n".encode("utf-8")
    return mf


def _build_cert_sf(manifest_mf: bytes) -> bytes:
    """Build CERT.SF — per-entry digests of MANIFEST.MF sections."""
    whole_digest = base64.b64encode(
        hashlib.sha256(manifest_mf).digest()
    ).decode("ascii")
    sf = (
        f"Signature-Version: 1.0\r\n"
        f"SHA-256-Digest-Manifest: {whole_digest}\r\n"
        f"Created-By: 1.0 (Android SignApk)\r\n\r\n"
    ).encode("utf-8")

    # Each per-entry section in MANIFEST.MF is hashed individually.
    # Sections are separated by \r\n\r\n.  The hash covers the section
    # text *including* its trailing \r\n\r\n.
    parts = manifest_mf.split(b"\r\n\r\n")
    # parts[0] = main attributes, parts[1..n-1] = per-entry, parts[-1] = ""
    for part in parts[1:]:
        if not part.strip():
            continue
        section_bytes = part + b"\r\n\r\n"
        digest = base64.b64encode(
            hashlib.sha256(section_bytes).digest()
        ).decode("ascii")
        for line in part.split(b"\r\n"):
            if line.startswith(b"Name: "):
                sf += line + b"\r\n"
                break
        sf += f"SHA-256-Digest: {digest}\r\n\r\n".encode("utf-8")
    return sf


def _build_cert_rsa(cert_sf: bytes, private_key, certificate) -> bytes:
    """Build CERT.RSA — PKCS#7 detached signature over CERT.SF."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.hazmat.primitives.serialization.pkcs7 import (
        PKCS7Options,
        PKCS7SignatureBuilder,
    )
    return (
        PKCS7SignatureBuilder()
        .set_data(cert_sf)
        .add_signer(certificate, private_key, hashes.SHA256())
        .sign(Encoding.DER, [PKCS7Options.DetachedSignature,
                             PKCS7Options.NoCapabilities])
    )


def _sign_and_align_apk(apk_path: str, private_key, certificate,
                         alignment: int = 4) -> None:
    """Sign (APK v1 / JAR) and zipalign an APK using pure Python.

    Combines signing and alignment in a single rewrite pass.
    """
    # Read all entries (drop old signatures)
    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    with zipfile.ZipFile(apk_path, "r") as zf:
        for info in zf.infolist():
            if info.filename.startswith("META-INF/"):
                continue
            entries.append((info, zf.read(info.filename)))

    # Build signing artifacts from uncompressed entry data
    manifest_entries = [(info.filename, data) for info, data in entries]
    manifest_mf = _build_manifest_mf(manifest_entries)
    cert_sf = _build_cert_sf(manifest_mf)
    cert_rsa = _build_cert_rsa(cert_sf, private_key, certificate)

    # Rewrite APK: META-INF first, then content entries with alignment
    tmp_path = apk_path + ".signed"
    with open(tmp_path, "wb") as raw_f:
        with zipfile.ZipFile(raw_f, "w") as zf:
            # Signing metadata (compressed, alignment irrelevant)
            zf.writestr("META-INF/MANIFEST.MF", manifest_mf)
            zf.writestr("META-INF/CERT.SF", cert_sf)
            zf.writestr("META-INF/CERT.RSA", cert_rsa)

            for info, data in entries:
                out = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                out.compress_type = info.compress_type
                out.external_attr = info.external_attr
                if info.compress_type == zipfile.ZIP_STORED and alignment > 1:
                    # Pad extra field so file data starts at aligned offset.
                    # Local header = 30 + len(filename) + len(extra).
                    header_base = 30 + len(out.filename.encode("utf-8"))
                    data_start = raw_f.tell() + header_base
                    pad = (alignment - (data_start % alignment)) % alignment
                    out.extra = b"\x00" * pad
                zf.writestr(out, data)

    shutil.move(tmp_path, apk_path)


def _sign_splits_python(splits: dict[str, str], patched_arm64: str,
                         output_dir: str) -> list[str]:
    """Zipalign and sign all APK splits using pure Python (v1 signing)."""
    print(f"\n{INFO} Signing APK splits (pure Python v1) ...")

    os.makedirs(output_dir, exist_ok=True)
    private_key, certificate = _generate_debug_key(output_dir)

    _CANONICAL = {
        "base": "base.apk",
        "arm64": "split_config.arm64_v8a.apk",
        "unity": "split_UnityDataAssetPack.apk",
    }

    signed_paths: list[str] = []
    for role, source in splits.items():
        if role == "arm64":
            source = patched_arm64
        name = _CANONICAL.get(role, os.path.basename(source))
        dest = os.path.join(output_dir, name)
        shutil.copy2(source, dest)
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        print(f"  Signing + aligning {name} ({size_mb:.1f} MB) ...")

        _sign_and_align_apk(dest, private_key, certificate)

        signed_paths.append(dest)
        print(f"  {OK} {name}: {os.path.getsize(dest) / (1024 * 1024):.1f} MB (v1 signed)")

    return signed_paths


# ---------------------------------------------------------------------------
# Step 2: Discover tools
# ---------------------------------------------------------------------------

def _find_build_tool(name: str) -> str | None:
    """Find an Android build tool (zipalign, apksigner) across platforms."""
    # 1. On PATH
    found = shutil.which(name)
    if found:
        return found

    # 2. ANDROID_HOME / ANDROID_SDK_ROOT
    sdk_roots: list[str] = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        val = os.environ.get(var)
        if val and os.path.isdir(val):
            sdk_roots.append(val)

    # 3. Platform-specific known locations
    system = platform.system()
    home = Path.home()

    if system == "Darwin":
        sdk_roots += [
            "/opt/homebrew/share/android-commandlinetools",
            str(home / "Library" / "Android" / "sdk"),
        ]
    elif system == "Windows":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        sdk_roots += [
            os.path.join(localappdata, "Android", "Sdk") if localappdata else "",
            r"C:\Android\sdk",
        ]
    else:  # Linux
        sdk_roots += [str(home / "Android" / "Sdk")]

    # Search build-tools dirs, prefer highest version
    for sdk in sdk_roots:
        bt_dir = os.path.join(sdk, "build-tools")
        if not os.path.isdir(bt_dir):
            continue
        versions = sorted(os.listdir(bt_dir), reverse=True)
        for ver in versions:
            candidate = os.path.join(bt_dir, ver, name)
            if os.path.isfile(candidate):
                return candidate
            # Windows: try .bat/.exe
            if system == "Windows":
                for ext in (".bat", ".exe"):
                    c = candidate + ext
                    if os.path.isfile(c):
                        return c

    return None


def _find_keytool() -> str | None:
    """Find the Java keytool binary."""
    # JAVA_HOME
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = os.path.join(java_home, "bin", "keytool")
        if os.path.isfile(candidate):
            return candidate

    # macOS Homebrew
    if platform.system() == "Darwin":
        for jh in ("/opt/homebrew/opt/openjdk/bin/keytool",
                    "/usr/local/opt/openjdk/bin/keytool"):
            if os.path.isfile(jh):
                return jh

    # On PATH
    return shutil.which("keytool")


def discover_tools() -> dict[str, str]:
    """Find zipalign, apksigner, keytool. Abort if missing."""
    print(f"\n{INFO} Discovering build tools ...")
    tools: dict[str, str] = {}

    for name in ("zipalign", "apksigner"):
        path = _find_build_tool(name)
        if path:
            tools[name] = path
            print(f"  {OK} {name}: {path}")
        else:
            print(f"  {FAIL} {name} not found")

    keytool = _find_keytool()
    if keytool:
        tools["keytool"] = keytool
        print(f"  {OK} keytool: {keytool}")
    else:
        print(f"  {FAIL} keytool not found")

    missing = [n for n in ("zipalign", "apksigner", "keytool") if n not in tools]
    if missing:
        if _HAVE_CRYPTOGRAPHY:
            print(f"  {INFO} Not found: {', '.join(missing)}")
            print(f"  {OK} Will use pure Python signing (v1 / JAR)")
            tools["_python_signing"] = True
        else:
            print()
            system = platform.system()
            if system == "Darwin":
                print(yellow("  Install Android command-line tools:"))
                print("    brew install --cask android-commandlinetools")
                print("    sdkmanager 'build-tools;35.0.0'")
                print("    brew install openjdk  (for keytool)")
            elif system == "Windows":
                print(yellow("  Install Android SDK Build Tools:"))
                print("    1. Install Android Studio or SDK command-line tools")
                print("    2. sdkmanager 'build-tools;35.0.0'")
                print("    3. Ensure Java JDK is installed (for keytool)")
            else:
                print(yellow("  Install Android SDK Build Tools:"))
                print("    sdkmanager 'build-tools;35.0.0'")
                print("    sudo apt install default-jdk  (for keytool)")
            _abort(f"Missing tools: {', '.join(missing)}\n"
                   "  Or: pip install cryptography  (for pure Python signing)")

    return tools


# ---------------------------------------------------------------------------
# Step 3: Download Frida Gadget
# ---------------------------------------------------------------------------

def _get_frida_version() -> str | None:
    """Try to get the installed frida module version."""
    try:
        import frida  # type: ignore[import-untyped]
        return frida.__version__
    except ImportError:
        return None


def _get_latest_gadget_version() -> str:
    """Query GitHub API for the latest Frida release version."""
    url = "https://api.github.com/repos/frida/frida/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "patch_apk/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["tag_name"]
    except Exception as exc:
        _abort(f"Could not determine latest Frida version: {exc}\n"
               "  Use --gadget-version to specify manually.")
    return ""  # unreachable


def download_gadget(version: str | None, cache_dir: str) -> tuple[str, str]:
    """Download and cache frida-gadget for android-arm64.

    Returns (path_to_so, version_used).
    """
    print(f"\n{INFO} Preparing Frida Gadget ...")

    # Resolve version
    if version:
        ver = version
        print(f"  Version (specified): {ver}")
    else:
        ver = _get_frida_version()
        if ver:
            print(f"  Version (from frida module): {ver}")
        else:
            print("  frida module not installed, querying GitHub ...")
            ver = _get_latest_gadget_version()
            print(f"  Version (latest release): {ver}")

    os.makedirs(cache_dir, exist_ok=True)

    cached_so = os.path.join(cache_dir, f"frida-gadget-{ver}.so")
    if os.path.isfile(cached_so):
        size_mb = os.path.getsize(cached_so) / (1024 * 1024)
        print(f"  {OK} Cached: {cached_so} ({size_mb:.1f} MB)")
        return cached_so, ver

    # Also check for unversioned "frida-gadget.so" in cache
    unversioned = os.path.join(cache_dir, "frida-gadget.so")
    if os.path.isfile(unversioned):
        size_mb = os.path.getsize(unversioned) / (1024 * 1024)
        print(f"  {OK} Found existing: {unversioned} ({size_mb:.1f} MB)")
        return unversioned, ver

    # Download
    asset = f"frida-gadget-{ver}-android-arm64.so.xz"
    url = f"https://github.com/frida/frida/releases/download/{ver}/{asset}"
    xz_path = os.path.join(cache_dir, asset)

    print(f"  Downloading {url} ...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "patch_apk/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            buf = io.BytesIO()
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                buf.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    mb = downloaded / (1024 * 1024)
                    print(f"\r  Downloading ... {mb:.1f} MB ({pct}%)",
                          end="", flush=True)
            print()  # newline after progress
            data = buf.getvalue()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            _abort(f"Gadget release not found (404): {url}\n"
                   "  Check the version number or use --gadget-version.")
        raise
    except Exception as exc:
        _abort(f"Download failed: {exc}\n"
               "  Download manually from https://github.com/frida/frida/releases\n"
               f"  and place the .so in {cache_dir}/")

    # Decompress .xz
    with open(xz_path, "wb") as f:
        f.write(data)
    print(f"  {OK} Downloaded {len(data) / (1024 * 1024):.1f} MB")

    print("  Extracting .xz ...")
    try:
        with lzma.open(xz_path) as xz_f:
            with open(cached_so, "wb") as out_f:
                shutil.copyfileobj(xz_f, out_f)
    except lzma.LZMAError as exc:
        _abort(f"Extraction failed (corrupt .xz?): {exc}")

    try:
        os.remove(xz_path)
    except OSError:
        pass

    size_mb = os.path.getsize(cached_so) / (1024 * 1024)
    print(f"  {OK} Extracted: {cached_so} ({size_mb:.1f} MB)")
    return cached_so, ver


# ---------------------------------------------------------------------------
# Step 4: Unpack arm64 split
# ---------------------------------------------------------------------------

def unpack_arm64(arm64_apk: str, work_dir: str) -> str:
    """Extract the arm64 split APK to a working directory.

    Returns path to libil2cpp.so.
    """
    print(f"\n{INFO} Unpacking arm64 split ...")

    if os.path.exists(work_dir):
        shutil.rmtree(work_dir)
    os.makedirs(work_dir)

    with zipfile.ZipFile(arm64_apk, "r") as zf:
        zf.extractall(work_dir)
        print(f"  Extracted {len(zf.namelist())} files")

    # Find libil2cpp.so — could be in lib/arm64-v8a/ or lib/arm64/
    for subdir in ("lib/arm64-v8a", "lib/arm64"):
        il2cpp = os.path.join(work_dir, subdir, "libil2cpp.so")
        if os.path.isfile(il2cpp):
            size_mb = os.path.getsize(il2cpp) / (1024 * 1024)
            print(f"  {OK} libil2cpp.so: {size_mb:.1f} MB")
            return il2cpp

    _abort("libil2cpp.so not found in arm64 split.\n"
           "  Expected at lib/arm64-v8a/libil2cpp.so")
    return ""  # unreachable


# ---------------------------------------------------------------------------
# Step 5: LIEF patch
# ---------------------------------------------------------------------------

def patch_libil2cpp(il2cpp_path: str) -> int:
    """Add libfrida-gadget.so to the NEEDED list of libil2cpp.so.

    Returns the LIEF delta (size change in bytes).
    """
    print(f"\n{INFO} Patching libil2cpp.so with LIEF ...")

    try:
        import lief  # type: ignore[import-untyped]
    except ImportError:
        _abort("LIEF not installed.\n  Fix: pip install lief")

    original_size = os.path.getsize(il2cpp_path)
    print(f"  Loading ELF binary ({original_size / (1024 * 1024):.1f} MB) ...")
    binary = lief.parse(il2cpp_path)

    existing_needed = [str(e) for e in binary.libraries]
    gadget_name = "libfrida-gadget.so"

    if gadget_name in existing_needed:
        print(f"  {WARN} Already patched — {gadget_name} already in NEEDED")
        print("  Skipping LIEF modification")
        return 0

    print(f"  Current NEEDED ({len(existing_needed)}): "
          f"{', '.join(existing_needed[:5])}{'...' if len(existing_needed) > 5 else ''}")
    binary.add_library(gadget_name)
    print(f"  Added {gadget_name} to NEEDED")

    patched_path = il2cpp_path + ".patched"
    print("  Writing patched binary ...")
    binary.write(patched_path)
    shutil.move(patched_path, il2cpp_path)

    patched_size = os.path.getsize(il2cpp_path)
    delta = patched_size - original_size
    print(f"  Patched: {patched_size / (1024 * 1024):.1f} MB (delta: +{delta} bytes)")

    # Verify
    verify = lief.parse(il2cpp_path)
    verify_needed = [str(e) for e in verify.libraries]
    if gadget_name in verify_needed:
        print(f"  {OK} Verified: {gadget_name} in NEEDED")
    else:
        _abort("LIEF verification failed — gadget not in NEEDED after patching")

    return delta


# ---------------------------------------------------------------------------
# Step 6: Inject gadget files
# ---------------------------------------------------------------------------

def inject_gadget(lib_dir: str, gadget_so_path: str) -> None:
    """Copy gadget .so and write config JSON into the lib directory."""
    print(f"\n{INFO} Injecting Frida Gadget ...")

    dest_so = os.path.join(lib_dir, "libfrida-gadget.so")
    shutil.copy2(gadget_so_path, dest_so)
    size_mb = os.path.getsize(dest_so) / (1024 * 1024)
    print(f"  {OK} Copied gadget: {size_mb:.1f} MB")

    dest_config = os.path.join(lib_dir, "libfrida-gadget.config.so")
    with open(dest_config, "w") as f:
        json.dump(GADGET_CONFIG, f, indent=2)
    print(f"  {OK} Config: listen 0.0.0.0:27042, on_load=resume")


# ---------------------------------------------------------------------------
# Step 7: Repack arm64 split
# ---------------------------------------------------------------------------

def repack_arm64(work_dir: str, output_path: str) -> None:
    """Repack the arm64 working directory into an APK.

    .so files are stored uncompressed (ZIP_STORED) for Android's
    direct-from-APK loading. META-INF is stripped.
    """
    print(f"\n{INFO} Repacking arm64 split ...")

    if os.path.exists(output_path):
        os.remove(output_path)

    file_count = 0
    with zipfile.ZipFile(output_path, "w") as zf:
        for root, dirs, files in os.walk(work_dir):
            for filename in sorted(files):
                full_path = os.path.join(root, filename)
                arcname = os.path.relpath(full_path, work_dir)
                # Skip old signatures
                if arcname.startswith("META-INF/") or arcname.startswith("META-INF\\"):
                    continue
                # .so files MUST be stored uncompressed and page-aligned
                if filename.endswith(".so"):
                    zf.write(full_path, arcname, compress_type=zipfile.ZIP_STORED)
                else:
                    zf.write(full_path, arcname, compress_type=zipfile.ZIP_DEFLATED)
                file_count += 1

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"  {OK} Repacked: {size_mb:.1f} MB ({file_count} files)")


# ---------------------------------------------------------------------------
# Step 8: Sign all splits
# ---------------------------------------------------------------------------

def _strip_meta_inf(zip_path: str) -> int:
    """Remove META-INF/ entries from a ZIP file. Returns count removed."""
    tmp = zip_path + ".tmp"
    removed = 0
    with zipfile.ZipFile(zip_path, "r") as zin, \
         zipfile.ZipFile(tmp, "w") as zout:
        for item in zin.infolist():
            if item.filename.startswith("META-INF/"):
                removed += 1
                continue
            data = zin.read(item.filename)
            zout.writestr(item, data)
    shutil.move(tmp, zip_path)
    return removed


def _make_env(tools: dict[str, str]) -> dict[str, str]:
    """Build a subprocess env with JAVA_HOME and tool dirs on PATH."""
    env = dict(os.environ)

    # Derive JAVA_HOME from keytool path
    keytool = tools.get("keytool", "")
    if keytool:
        java_bin = os.path.dirname(keytool)
        java_home = os.path.dirname(java_bin)
        env["JAVA_HOME"] = java_home
        extra_path = java_bin
    else:
        extra_path = ""

    # Add build-tools dir to PATH
    apksigner = tools.get("apksigner", "")
    if apksigner:
        bt_dir = os.path.dirname(apksigner)
        extra_path = f"{bt_dir}{os.pathsep}{extra_path}" if extra_path else bt_dir

    if extra_path:
        env["PATH"] = f"{extra_path}{os.pathsep}{env.get('PATH', '')}"

    return env


def sign_splits(splits: dict[str, str], patched_arm64: str,
                output_dir: str, tools: dict[str, str]) -> list[str]:
    """Zipalign and sign all APK splits. Returns list of signed APK paths."""
    if tools.get("_python_signing"):
        return _sign_splits_python(splits, patched_arm64, output_dir)

    print(f"\n{INFO} Signing APK splits ...")

    os.makedirs(output_dir, exist_ok=True)
    env = _make_env(tools)
    keystore = os.path.join(output_dir, "debug.keystore")
    zipalign = tools["zipalign"]
    apksigner = tools["apksigner"]

    # Generate keystore if needed
    if not os.path.exists(keystore):
        print("  Generating debug keystore ...")
        result = _run([
            tools["keytool"], "-genkey", "-v",
            "-keystore", keystore,
            "-alias", "debug",
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-storepass", "android",
            "-keypass", "android",
            "-dname", "CN=Debug,O=Debug,C=US",
        ], env=env)
        if result.returncode != 0:
            _abort("Failed to generate debug keystore")
        print(f"  {OK} Keystore: {keystore}")
    else:
        print(f"  {OK} Using existing keystore: {keystore}")

    # Build the map of output_name → source_path
    splits_to_sign: dict[str, str] = {}

    # Use canonical names for output
    _CANONICAL = {
        "base": "base.apk",
        "arm64": "split_config.arm64_v8a.apk",
        "unity": "split_UnityDataAssetPack.apk",
    }

    for role, source in splits.items():
        if role == "arm64":
            source = patched_arm64
        out_name = _CANONICAL.get(role, os.path.basename(source))
        splits_to_sign[out_name] = source

    signed_paths: list[str] = []
    for name, source in splits_to_sign.items():
        dest = os.path.join(output_dir, name)
        unsigned = dest + ".unsigned"
        aligned = dest + ".aligned"

        # Copy and strip old signatures
        shutil.copy2(source, unsigned)
        removed = _strip_meta_inf(unsigned)
        if removed:
            print(f"  Stripped {removed} META-INF entries from {name}")

        # Zipalign (must be done BEFORE apksigner v2)
        print(f"  Zipaligning {name} ...")
        result = _run([zipalign, "-f", "4", unsigned, aligned], env=env)
        if result.returncode != 0:
            print(f"  {WARN} zipalign failed, using unaligned")
            shutil.copy2(unsigned, aligned)

        # Sign with apksigner (v1 + v2 + v3)
        shutil.copy2(aligned, dest)
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        print(f"  Signing {name} ({size_mb:.1f} MB) ...")
        result = _run([
            apksigner, "sign",
            "--ks", keystore,
            "--ks-pass", "pass:android",
            "--key-pass", "pass:android",
            "--ks-key-alias", "debug",
            "--v1-signing-enabled", "true",
            "--v2-signing-enabled", "true",
            "--v3-signing-enabled", "true",
            dest,
        ], env=env)
        if result.returncode != 0:
            _abort(f"Failed to sign {name}")

        # Verify
        _run([apksigner, "verify", "--verbose", dest], env=env)
        signed_paths.append(dest)
        print(f"  {OK} {name}: {os.path.getsize(dest) / (1024 * 1024):.1f} MB (signed)")

        # Cleanup temp files
        for tmp in (unsigned, aligned):
            if os.path.exists(tmp):
                os.remove(tmp)

    return signed_paths


# ---------------------------------------------------------------------------
# Step 9 (optional): Install
# ---------------------------------------------------------------------------

def _find_adb() -> str:
    """Find ADB binary: bundled platform-tools first, then PATH."""
    # Bundled ADB next to the project root
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(project_root, "platform-tools", "adb.exe" if os.name == "nt" else "adb")
    if os.path.isfile(bundled):
        return bundled
    # Fall back to PATH
    found = shutil.which("adb")
    if found:
        return found
    _abort("adb not found. Expected at platform-tools/adb or on PATH.")
    return ""  # unreachable


def pull_from_device(device: str | None, output_dir: str) -> dict[str, str]:
    """Pull APK splits from a connected device via ADB.

    Returns a dict of {role: local_apk_path}.
    """
    print(f"\n{INFO} Pulling APK splits from device ...")
    adb = _find_adb()

    adb_cmd = [adb]
    if device:
        adb_cmd += ["-s", device]

    # Get APK paths on device
    result = _run(adb_cmd + ["shell", "pm", "path", PACKAGE])
    if result.returncode != 0 or not result.stdout:
        _abort(f"Could not find {PACKAGE} on device.\n"
               "  Is the game installed?")

    remote_paths: list[str] = []
    for line in result.stdout.strip().splitlines():
        if line.startswith("package:"):
            remote_paths.append(line[len("package:"):])

    if not remote_paths:
        _abort(f"No APK paths found for {PACKAGE}")

    print(f"  Found {len(remote_paths)} split(s) on device")

    # Pull each APK
    os.makedirs(output_dir, exist_ok=True)
    splits: dict[str, str] = {}
    for remote in remote_paths:
        filename = os.path.basename(remote)
        local = os.path.join(output_dir, filename)
        print(f"  Pulling {filename} ...")
        result = _run(adb_cmd + ["pull", remote, local], timeout=120)
        if result.returncode != 0:
            _abort(f"Failed to pull {remote}")

        role = _classify_apk(filename)
        if role:
            splits[role] = local
            size_mb = os.path.getsize(local) / (1024 * 1024)
            print(f"  {OK} {role}: {filename} ({size_mb:.1f} MB)")
        else:
            print(f"  {WARN} Skipping unrecognized: {filename}")

    if "arm64" not in splits:
        _abort("arm64 split not found on device")

    return splits


def install_splits(signed_paths: list[str], device: str | None = None) -> None:
    """Install the signed splits via adb install-multiple."""
    print(f"\n{INFO} Installing on device ...")

    adb = _find_adb()
    adb_cmd = [adb]
    if device:
        adb_cmd += ["-s", device]

    print(f"  {WARN} Signature has changed — must uninstall first.")
    print(f"  {WARN} Game data will be lost — log in via account to recover!")
    print()

    # Force stop
    _run(adb_cmd + ["shell", "am", "force-stop", PACKAGE])
    time.sleep(1)

    # Uninstall
    print("  Uninstalling existing app ...")
    _run(adb_cmd + ["uninstall", PACKAGE])
    time.sleep(1)

    # Install
    print("  Installing split APKs ...")
    cmd = adb_cmd + ["install-multiple", "-r", "-d", "-t"] + signed_paths
    result = _run(cmd, timeout=300)

    if result.returncode != 0:
        print(f"\n  {FAIL} Install failed!")
        print(f"  Signed APKs are in: {os.path.dirname(signed_paths[0])}")
        print("  Try manually: adb install-multiple -r -d -t " +
              " ".join(os.path.basename(p) for p in signed_paths))
        return

    print(f"  {OK} Install succeeded!")


# ---------------------------------------------------------------------------
# Write patch_info.json
# ---------------------------------------------------------------------------

def write_patch_info(output_dir: str, gadget_version: str,
                     lief_delta: int, source_splits: dict[str, str],
                     signed_paths: list[str]) -> None:
    """Write metadata about the patching operation."""
    info = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gadget_version": gadget_version,
        "lief_delta": lief_delta,
        "source_splits": {role: os.path.basename(p) for role, p in source_splits.items()},
        "output_files": [os.path.basename(p) for p in signed_paths],
        "signed_with": "debug.keystore (auto-generated)",
    }
    info_path = os.path.join(output_dir, "patch_info.json")
    with open(info_path, "w") as f:
        json.dump(info, f, indent=2)
    print(f"  {OK} Metadata: {info_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="patch_apk",
        description="Patch Kingdom Guard APK with Frida Gadget for protocol interception.",
        epilog="Examples:\n"
               "  python -m protocol.patch_apk --device emulator-5554 --install\n"
               "  python -m protocol.patch_apk KingdomGuard.xapk\n"
               "  python -m protocol.patch_apk ./splits/\n"
               "  python -m protocol.patch_apk base.apk split_arm64.apk split_unity.apk\n"
               "  python -m protocol.patch_apk KingdomGuard.xapk --install\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input", nargs="*",
        help="XAPK file, directory with split APKs, or individual APK files",
    )
    parser.add_argument(
        "--device", metavar="ID",
        help="Pull APK splits from connected device (e.g. emulator-5554)",
    )
    parser.add_argument(
        "--install", action="store_true",
        help="Install patched APKs on connected device via adb install-multiple",
    )
    parser.add_argument(
        "--output", "-o", metavar="DIR",
        help="Output directory for patched APKs (default: <input_dir>/patched/)",
    )
    parser.add_argument(
        "--gadget-version", metavar="VER",
        help="Frida Gadget version (default: match installed frida module or latest)",
    )

    args = parser.parse_args(argv)

    if not args.device and not args.input:
        parser.error("either positional input or --device is required")

    print(bold("APK Patcher — Kingdom Guard Protocol Interception"))
    print(bold("=" * 52))

    total_steps = (10 if args.device else 8) + (1 if args.install else 0)
    cur_step = 0

    # Step: Pull from device (if --device)
    if args.device:
        cur_step += 1
        _step(cur_step, total_steps, "Pull APK splits from device")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        pull_dir = os.path.join(os.path.dirname(script_dir), "apk-pulled")
        device_splits = pull_from_device(args.device, pull_dir)
        # Use pulled splits as input
        args.input = list(device_splits.values())

    # Step 1: Resolve input
    cur_step += 1
    _step(cur_step, total_steps, "Resolve input")
    splits, output_dir = resolve_input(args.input, args.output)

    # Step: Discover tools
    cur_step += 1
    _step(cur_step, total_steps, "Discover build tools")
    tools = discover_tools()

    # Check LIEF early
    try:
        import lief  # type: ignore[import-untyped]  # noqa: F401
        print(f"  {OK} lief: {lief.__version__}")
    except ImportError:
        _abort("LIEF not installed.\n  Fix: pip install lief")

    # Step: Download Frida Gadget
    cur_step += 1
    _step(cur_step, total_steps, "Download Frida Gadget")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(os.path.dirname(script_dir), "frida-gadget-cache")
    gadget_so, gadget_version = download_gadget(args.gadget_version, cache_dir)

    # Step: Unpack arm64 split
    cur_step += 1
    _step(cur_step, total_steps, "Unpack arm64 split")
    work_dir = os.path.join(output_dir, "_arm64_work")
    il2cpp_path = unpack_arm64(splits["arm64"], work_dir)
    lib_dir = os.path.dirname(il2cpp_path)

    # Step: LIEF patch
    cur_step += 1
    _step(cur_step, total_steps, "Patch libil2cpp.so")
    lief_delta = patch_libil2cpp(il2cpp_path)

    # Step: Inject gadget
    cur_step += 1
    _step(cur_step, total_steps, "Inject Frida Gadget")
    inject_gadget(lib_dir, gadget_so)

    # Step: Repack arm64 split
    cur_step += 1
    _step(cur_step, total_steps, "Repack arm64 split")
    patched_arm64 = os.path.join(output_dir, "_arm64_patched.apk")
    repack_arm64(work_dir, patched_arm64)

    # Clean up work dir
    shutil.rmtree(work_dir, ignore_errors=True)

    # Step: Sign all splits
    cur_step += 1
    _step(cur_step, total_steps, "Sign all splits")
    signed_paths = sign_splits(splits, patched_arm64, output_dir, tools)

    # Clean up intermediate patched APK
    if os.path.exists(patched_arm64):
        os.remove(patched_arm64)

    # Write metadata
    write_patch_info(output_dir, gadget_version, lief_delta, splits, signed_paths)

    # Step (optional): Install
    if args.install:
        cur_step += 1
        _step(cur_step, total_steps, "Install on device")
        install_splits(signed_paths, device=args.device)

    # Summary
    print()
    print(green("=" * 52))
    print(green("  Patching complete!"))
    print(green("=" * 52))
    print(f"  Output:     {output_dir}/")
    for p in signed_paths:
        size_mb = os.path.getsize(p) / (1024 * 1024)
        print(f"              {os.path.basename(p)} ({size_mb:.1f} MB)")
    print(f"  Gadget:     v{gadget_version} (listen 0.0.0.0:27042)")
    if tools.get("_python_signing"):
        print(f"  Signed:     Python v1 (JAR signing)")
    else:
        print(f"  Signed:     apksigner v1+v2+v3")
    print()
    if not args.install:
        print("  To install:")
        print(f"    adb install-multiple -r -d -t {' '.join(os.path.basename(p) for p in signed_paths)}")
        print()
    print("  To connect after install:")
    print("    adb forward tcp:27042 tcp:27042")
    print("    python -m protocol.interceptor --gadget")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{yellow('Interrupted.')}")
        sys.exit(130)
    except PatchError as exc:
        _abort(str(exc))
