#!/usr/bin/env python3
"""
Frida Gadget APK injector for Kingdom Guard (split APK).

Kingdom Guard uses Android App Bundle with 3 splits:
  - base.apk (DEX, manifest, Java resources)
  - split_config.arm64_v8a.apk (native .so libraries including libil2cpp.so)
  - split_UnityDataAssetPack.apk (Unity game data assets)

This script patches the arm64 split to inject frida-gadget.so via LIEF,
re-signs all splits with apksigner (v2/v3), and installs with adb install-multiple.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

# ── Paths ──────────────────────────────────────────────────────────────────

BASE_DIR = "/tmp/kg_apk"
GADGET_SO = os.path.join(BASE_DIR, "frida-gadget-cache", "frida-gadget.so")
KEYSTORE = os.path.join(BASE_DIR, "debug.keystore")

# Tools
JAVA_HOME = "/opt/homebrew/opt/openjdk"
JAVA = os.path.join(JAVA_HOME, "bin", "java")
BUILD_TOOLS = "/opt/homebrew/share/android-commandlinetools/build-tools/35.0.0"
APKSIGNER = os.path.join(BUILD_TOOLS, "apksigner")
ZIPALIGN = os.path.join(BUILD_TOOLS, "zipalign")
KEYTOOL = os.path.join(JAVA_HOME, "bin", "keytool")

# Original APK splits
ORIGINAL_SPLITS = {
    "base": os.path.join(BASE_DIR, "base.apk"),
    "arm64": os.path.join(BASE_DIR, "split_arm64.apk"),
    "unity": os.path.join(BASE_DIR, "split_unity_data.apk"),
}

# Patched output
SIGNED_DIR = os.path.join(BASE_DIR, "signed_splits")
WORK_DIR = os.path.join(BASE_DIR, "arm64_patched")

GADGET_CONFIG = {
    "interaction": {
        "type": "listen",
        "address": "0.0.0.0",
        "port": 27042,
        "on_port_conflict": "pick-next",
        "on_load": "resume"
    }
}

PACKAGE = "com.tap4fun.odin.kingdomguard"


def step(n, msg):
    print(f"\n{'='*60}")
    print(f"  Step {n}: {msg}")
    print(f"{'='*60}")


def run(cmd, **kwargs):
    """Run a command, setting JAVA_HOME for Java tools."""
    env = dict(os.environ, JAVA_HOME=JAVA_HOME,
               PATH=f"{JAVA_HOME}/bin:{BUILD_TOOLS}:{os.environ.get('PATH', '')}")
    print(f"  $ {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    result = subprocess.run(
        cmd, shell=isinstance(cmd, str),
        capture_output=True, text=True, env=env, **kwargs
    )
    if result.stdout.strip():
        for line in result.stdout.strip().split('\n')[:15]:
            print(f"    {line}")
    if result.returncode != 0:
        print(f"  ERROR (exit {result.returncode}):")
        for line in (result.stderr or result.stdout or "").strip().split('\n')[:10]:
            print(f"    {line}")
    return result


def strip_meta_inf(zip_path):
    """Remove META-INF/ entries from a ZIP to clear old signatures."""
    tmp = zip_path + ".tmp"
    removed = 0
    with zipfile.ZipFile(zip_path, 'r') as zin, \
         zipfile.ZipFile(tmp, 'w') as zout:
        for item in zin.infolist():
            if item.filename.startswith("META-INF/"):
                removed += 1
                continue
            data = zin.read(item.filename)
            zout.writestr(item, data)
    shutil.move(tmp, zip_path)
    return removed


def main():
    print("+" + "="*58 + "+")
    print("|  Frida Gadget Injector — Kingdom Guard (Split APK)       |")
    print("+" + "="*58 + "+")

    # ── Pre-checks ─────────────────────────────────────────────────────
    for name, path in ORIGINAL_SPLITS.items():
        if not os.path.exists(path):
            print(f"  ERROR: Missing {name} split: {path}")
            sys.exit(1)
        print(f"  {name}: {os.path.getsize(path) / 1024 / 1024:.1f} MB")

    if not os.path.exists(GADGET_SO):
        print(f"  ERROR: Frida gadget not found: {GADGET_SO}")
        sys.exit(1)
    print(f"  gadget: {os.path.getsize(GADGET_SO) / 1024 / 1024:.1f} MB")

    for tool_name, tool_path in [("apksigner", APKSIGNER), ("zipalign", ZIPALIGN)]:
        if not os.path.exists(tool_path):
            print(f"  ERROR: {tool_name} not found: {tool_path}")
            sys.exit(1)
    print(f"  apksigner: {APKSIGNER}")
    print(f"  zipalign:  {ZIPALIGN}")

    try:
        import lief
    except ImportError:
        print("ERROR: LIEF not installed. Run: pip install lief")
        sys.exit(1)

    # ── Step 1: Unpack arm64 split ─────────────────────────────────────
    step(1, "Unpacking arm64 split APK")

    if os.path.exists(WORK_DIR):
        shutil.rmtree(WORK_DIR)
    os.makedirs(WORK_DIR)

    with zipfile.ZipFile(ORIGINAL_SPLITS["arm64"], 'r') as zf:
        zf.extractall(WORK_DIR)
        print(f"  Extracted {len(zf.namelist())} files")

    lib_dir = os.path.join(WORK_DIR, "lib", "arm64-v8a")
    il2cpp_path = os.path.join(lib_dir, "libil2cpp.so")

    if not os.path.exists(il2cpp_path):
        print(f"  ERROR: libil2cpp.so not found!")
        sys.exit(1)

    print(f"  libil2cpp.so: {os.path.getsize(il2cpp_path) / 1024 / 1024:.1f} MB")

    # ── Step 2: Patch libil2cpp.so with LIEF ───────────────────────────
    step(2, "Patching libil2cpp.so to load frida-gadget")

    print("  Loading ELF binary with LIEF (~10s for 135MB)...")
    binary = lief.parse(il2cpp_path)

    existing_needed = [str(e) for e in binary.libraries]
    print(f"  Current NEEDED ({len(existing_needed)}):")
    for lib in existing_needed:
        print(f"    - {lib}")

    gadget_name = "libfrida-gadget.so"
    if gadget_name in existing_needed:
        print(f"  Already patched — skipping LIEF modification")
    else:
        binary.add_library(gadget_name)
        print(f"  Added {gadget_name} to NEEDED")

        patched_path = il2cpp_path + ".patched"
        print("  Writing patched binary (~15s)...")
        binary.write(patched_path)
        shutil.move(patched_path, il2cpp_path)

    patched_size = os.path.getsize(il2cpp_path)
    print(f"  Patched libil2cpp.so: {patched_size / 1024 / 1024:.1f} MB")

    # Verify
    verify = lief.parse(il2cpp_path)
    verify_needed = [str(e) for e in verify.libraries]
    if gadget_name in verify_needed:
        print(f"  Verified: {gadget_name} in NEEDED")
    else:
        print(f"  ERROR: Verification failed!")
        sys.exit(1)

    # ── Step 3: Copy gadget + config ───────────────────────────────────
    step(3, "Injecting frida-gadget.so + config")

    gadget_dest = os.path.join(lib_dir, gadget_name)
    shutil.copy2(GADGET_SO, gadget_dest)
    print(f"  Copied gadget: {os.path.getsize(gadget_dest) / 1024 / 1024:.1f} MB")

    config_dest = os.path.join(lib_dir, "libfrida-gadget.config.so")
    with open(config_dest, 'w') as f:
        json.dump(GADGET_CONFIG, f, indent=2)
    print(f"  Config: listen on 0.0.0.0:27042, on_load=resume")

    # ── Step 4: Repack arm64 split ─────────────────────────────────────
    step(4, "Repacking arm64 split APK")

    patched_arm64 = os.path.join(BASE_DIR, "split_arm64_patched.apk")
    if os.path.exists(patched_arm64):
        os.remove(patched_arm64)

    # Exclude META-INF (old signatures) during repack
    with zipfile.ZipFile(patched_arm64, 'w') as zf:
        for root, dirs, files in os.walk(WORK_DIR):
            for file in sorted(files):
                full_path = os.path.join(root, file)
                arcname = os.path.relpath(full_path, WORK_DIR)
                # Skip old signature files
                if arcname.startswith("META-INF/"):
                    continue
                # .so files MUST be stored uncompressed and page-aligned
                if file.endswith('.so'):
                    zf.write(full_path, arcname, compress_type=zipfile.ZIP_STORED)
                else:
                    zf.write(full_path, arcname, compress_type=zipfile.ZIP_DEFLATED)

    print(f"  Repacked: {os.path.getsize(patched_arm64) / 1024 / 1024:.1f} MB")

    # ── Step 5: Generate keystore ──────────────────────────────────────
    step(5, "Preparing signing key")

    if not os.path.exists(KEYSTORE):
        print("  Generating debug keystore...")
        result = run([
            KEYTOOL, "-genkey", "-v",
            "-keystore", KEYSTORE,
            "-alias", "debug",
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-storepass", "android",
            "-keypass", "android",
            "-dname", "CN=Debug,O=Debug,C=US"
        ])
        if result.returncode != 0:
            sys.exit(1)
    else:
        print(f"  Using existing keystore: {KEYSTORE}")

    # ── Step 6: Zipalign + Sign all splits with apksigner (v2/v3) ─────
    step(6, "Zipalign + Sign all APK splits (v2/v3)")

    if os.path.exists(SIGNED_DIR):
        shutil.rmtree(SIGNED_DIR)
    os.makedirs(SIGNED_DIR)

    # Map of split name → source APK
    splits_to_sign = {
        "base.apk": ORIGINAL_SPLITS["base"],
        "split_config.arm64_v8a.apk": patched_arm64,
        "split_UnityDataAssetPack.apk": ORIGINAL_SPLITS["unity"],
    }

    signed_paths = []
    for name, source in splits_to_sign.items():
        dest = os.path.join(SIGNED_DIR, name)
        unsigned = dest + ".unsigned"
        aligned = dest + ".aligned"

        # Copy and strip old signatures
        shutil.copy2(source, unsigned)
        removed = strip_meta_inf(unsigned)
        if removed:
            print(f"\n  Stripped {removed} META-INF entries from {name}")

        # Zipalign (must be done BEFORE apksigner v2)
        print(f"  Zipaligning {name}...")
        result = run([ZIPALIGN, "-f", "4", unsigned, aligned])
        if result.returncode != 0:
            print(f"  WARNING: zipalign failed for {name}, using unaligned")
            shutil.copy2(unsigned, aligned)

        # Sign with apksigner (v2 + v3)
        shutil.copy2(aligned, dest)
        print(f"  Signing {name} ({os.path.getsize(dest) / 1024 / 1024:.1f} MB)...")
        result = run([
            APKSIGNER, "sign",
            "--ks", KEYSTORE,
            "--ks-pass", "pass:android",
            "--key-pass", "pass:android",
            "--ks-key-alias", "debug",
            "--v1-signing-enabled", "true",
            "--v2-signing-enabled", "true",
            "--v3-signing-enabled", "true",
            dest
        ])
        if result.returncode != 0:
            print(f"  ERROR: Failed to sign {name}")
            sys.exit(1)

        # Verify
        verify_result = run([APKSIGNER, "verify", "--verbose", dest])
        signed_paths.append(dest)
        print(f"  Signed: {os.path.getsize(dest) / 1024 / 1024:.1f} MB")

        # Cleanup
        for tmp in [unsigned, aligned]:
            if os.path.exists(tmp):
                os.remove(tmp)

    # ── Step 7: Install ────────────────────────────────────────────────
    step(7, "Installing on emulator")

    print("  NOTE: Signature has changed — must uninstall first")
    print(f"  Game data will be lost — log in via account to recover!")
    print()

    # Force stop the game
    run(f"adb shell am force-stop {PACKAGE}")
    time.sleep(1)

    # Uninstall existing
    print("  Uninstalling existing app...")
    run(f"adb uninstall {PACKAGE}")
    time.sleep(1)

    # Install all splits together
    print("  Installing split APKs...")
    install_cmd = ["adb", "install-multiple", "-r", "-d", "-t"] + signed_paths
    result = run(install_cmd, timeout=300)

    if result.returncode != 0:
        print("\n  Install failed!")
        print("  Signed APKs are in:", SIGNED_DIR)
        sys.exit(1)

    print("  Install SUCCESS!")

    # ── Step 8: Verify ─────────────────────────────────────────────────
    step(8, "Verification")

    print("  Launching game...")
    run(f"adb shell am start -n {PACKAGE}/com.unity3d.player.UnityPlayerActivity")

    print("  Waiting 15s for game + gadget initialization...")
    time.sleep(15)

    # Check if gadget port is listening
    result = run("adb shell 'cat /proc/net/tcp6 2>/dev/null; cat /proc/net/tcp 2>/dev/null'")
    stdout = result.stdout or ""
    # 27042 decimal = 0x69B2
    gadget_ok = "69B2" in stdout.upper()

    if not gadget_ok:
        print("  Port 27042 not detected yet, waiting 20s more...")
        time.sleep(20)
        result = run("adb shell 'cat /proc/net/tcp6 2>/dev/null; cat /proc/net/tcp 2>/dev/null'")
        gadget_ok = "69B2" in (result.stdout or "").upper()

    if gadget_ok:
        print("  Frida gadget port 27042 is LISTENING!")
        run("adb forward tcp:27042 tcp:27042")
        print("  Attempting Frida connection...")

        try:
            import frida
            mgr = frida.get_device_manager()
            device = mgr.add_remote_device("127.0.0.1:27042")
            session = device.attach("Gadget")
            print("  Connected to Frida gadget!")

            script = session.create_script("""
                var il2cpp = Process.findModuleByName("libil2cpp.so");
                if (il2cpp) {
                    send({
                        type: "success",
                        base: il2cpp.base.toString(),
                        size: il2cpp.size
                    });
                } else {
                    send({type: "error", msg: "libil2cpp.so not found in process"});
                }
            """)
            messages = []
            script.on('message', lambda msg, data: messages.append(msg))
            script.load()
            time.sleep(2)

            if messages:
                payload = messages[0].get('payload', {})
                if payload.get('type') == 'success':
                    print(f"  libil2cpp.so: base={payload['base']}, size={payload['size']}")
                    print(f"\n  FULLY OPERATIONAL!")
                else:
                    print(f"  {payload}")

            script.unload()
            session.detach()

        except Exception as e:
            print(f"  Frida connect error: {e}")
    else:
        print("  WARNING: Gadget port not detected")
        print("  The game may have crashed or the gadget didn't load")
        print("  Check: adb logcat -s Frida:* linker:* | head -50")

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  SUMMARY")
    print("="*60)
    print(f"  Split APKs:    3 (base + arm64 + unity data)")
    print(f"  Patched:       split_config.arm64_v8a.apk")
    print(f"  Gadget:        {os.path.getsize(GADGET_SO) / 1024 / 1024:.1f} MB (listen mode)")
    print(f"  Method:        LIEF NEEDED injection into libil2cpp.so")
    print(f"  Signing:       apksigner v1+v2+v3")
    print(f"  Signed APKs:   {SIGNED_DIR}/")
    print()
    print("  To connect:")
    print("    adb forward tcp:27042 tcp:27042")
    print("    frida -H 127.0.0.1:27042 Gadget -l protocol/frida_hook.js")
    print()
    print("  From Python:")
    print("    python protocol/interceptor.py --gadget")


if __name__ == "__main__":
    main()
