#!/usr/bin/env python3
"""
build_app.py — Build a branded Android WebView APK and AAB from a JSON config.

Usage
-----
    python build_app.py \\
        --config      build-config.json \\
        --template    /path/to/webtoapp-android-template \\
        --output      ./dist \\
        --keystore-dir ./keystores

Build-config schema
-------------------
    {
        "appName":      "My App",
        "packageName":  "com.example.myapp",
        "websiteUrl":   "https://example.com",
        "primaryColor": "#6366F1",
        "splashBgColor": "#6366F1",      // optional — defaults to primaryColor
        "versionCode":  1,
        "versionName":  "1.0",
        "features": {
            "pullToRefresh": true,
            "push":          false,       // not yet implemented, logged as warning
            "admob":         false        // not yet implemented, logged as warning
        },
        "iconPath":    "assets/icon.png",   // optional — keeps template icon if absent
        "splashPath":  "assets/splash.png"  // optional — keeps template splash if absent
    }

Requirements
------------
    pip install Pillow

    System tools (must be on PATH or configured via --sdk-root):
        • Java 17+  (java, keytool)
        • Android SDK build-tools  (ANDROID_HOME env var or --sdk-root)
        • gradlew in the template repo  (chmod +x on Unix; .bat used on Windows)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit(
        "ERROR: Pillow is required.\n"
        "Install it with:  pip install Pillow"
    )

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_app")

# ── Constants ─────────────────────────────────────────────────────────────────

PACKAGE_RE   = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,}$')
HEX_COLOR_RE = re.compile(r'^#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$')

# Legacy launcher icon side length (px) at each density bucket (base dp = 48)
LEGACY_ICON_SIZES: dict[str, int] = {
    "mipmap-mdpi":    48,
    "mipmap-hdpi":    72,
    "mipmap-xhdpi":   96,
    "mipmap-xxhdpi":  144,
    "mipmap-xxxhdpi": 192,
}

# Adaptive icon foreground canvas size (dp = 108) at each density.
# The safe zone (always visible on any launcher) is the inner 72 dp.
ADAPTIVE_CANVAS_PX: dict[str, int] = {
    "mipmap-mdpi":    108,
    "mipmap-hdpi":    162,
    "mipmap-xhdpi":   216,
    "mipmap-xxhdpi":  324,
    "mipmap-xxxhdpi": 432,
}

# Matches the values baked into the template source files
TEMPLATE_PACKAGE   = "com.example.webtoapp"
TEMPLATE_URL       = "https://www.google.com"
TEMPLATE_HOST      = "www.google.com"
TEMPLATE_APP_NAME  = "WebToApp"
TEMPLATE_COLOR     = "#6366F1"
TEMPLATE_SPLASH_BG = "#6366F1"

KEY_ALIAS = "app"

# Directories to skip when copying the template
_SKIP_DIRS = {".git", ".idea", "build", ".gradle", "__pycache__"}

# ── Adaptive icon XML written to mipmap-anydpi-v26/ ──────────────────────────

_ADAPTIVE_ICON_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
        <background android:drawable="@drawable/ic_launcher_background"/>
        <foreground android:drawable="@drawable/ic_launcher_foreground"/>
    </adaptive-icon>
""")

_LAUNCHER_BG_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <!-- Adaptive icon background — solid white; swap colour per brand -->
    <shape xmlns:android="http://schemas.android.com/apk/res/android">
        <solid android:color="#FFFFFF" />
    </shape>
""")

# ─────────────────────────────────────────────────────────────────────────────
# 1. Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_config(cfg: dict[str, Any]) -> None:
    """Raise ValueError listing every problem found in the config dict."""
    errors: list[str] = []

    for field in ("appName", "packageName", "websiteUrl", "primaryColor"):
        if not cfg.get(field):
            errors.append(f"Missing required field: '{field}'")

    pkg = cfg.get("packageName", "")
    if pkg and not PACKAGE_RE.match(pkg):
        errors.append(
            f"packageName '{pkg}' is invalid — must be lowercase dot-separated "
            "identifiers with at least two segments (e.g. com.example.app)."
        )

    url = cfg.get("websiteUrl", "")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            errors.append(
                f"websiteUrl '{url}' must be an absolute http/https URL "
                "with a hostname (e.g. https://example.com)."
            )

    for color_field in ("primaryColor", "splashBgColor"):
        color = cfg.get(color_field, "")
        if color and not HEX_COLOR_RE.match(color):
            errors.append(
                f"'{color_field}' value '{color}' is not a valid hex colour "
                "(expected #RRGGBB or #AARRGGBB)."
            )

    for asset_field in ("iconPath", "splashPath"):
        path_str = cfg.get(asset_field, "")
        if path_str and not Path(path_str).is_file():
            errors.append(f"'{asset_field}' path not found: {path_str}")

    vc = cfg.get("versionCode", 1)
    if not isinstance(vc, int) or vc < 1:
        errors.append("'versionCode' must be a positive integer.")

    # Warn about unimplemented optional features but don't block the build
    for feat, enabled in cfg.get("features", {}).items():
        if feat not in ("pullToRefresh",) and enabled:
            log.warning(
                "Feature '%s' is enabled in config but is not yet wired into "
                "the template — it will be ignored for this build.",
                feat,
            )

    if errors:
        msg = "Config validation failed:\n" + "\n".join(f"  • {e}" for e in errors)
        raise ValueError(msg)

    log.info("Config validated OK — building '%s' (%s)", cfg["appName"], cfg["packageName"])


# ─────────────────────────────────────────────────────────────────────────────
# 2. Build directory
# ─────────────────────────────────────────────────────────────────────────────

def prepare_build_dir(template_dir: Path, build_dir: Path) -> None:
    """
    Copy the template repo to build_dir, skipping build artefacts and VCS dirs.
    Makes gradlew executable on Unix so Gradle can run without a prior chmod.
    """
    if build_dir.exists():
        shutil.rmtree(build_dir)

    def _ignore(src: str, names: list[str]) -> set[str]:
        return {n for n in names if n in _SKIP_DIRS}

    shutil.copytree(template_dir, build_dir, ignore=_ignore)

    # Ensure gradlew is executable (no-op on Windows)
    gradlew = build_dir / "gradlew"
    if gradlew.exists():
        gradlew.chmod(gradlew.stat().st_mode | 0o111)

    log.info("Template copied → %s", build_dir)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Source-file patching helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")

def _replace_exact(p: Path, old: str, new: str, required: bool = True) -> None:
    """Replace the first occurrence of `old` with `new` in file `p`."""
    text = _read(p)
    if old not in text:
        msg = f"Patch target not found in {p.name}: {old!r:.80}"
        if required:
            raise RuntimeError(msg)
        log.warning(msg)
        return
    _write(p, text.replace(old, new, 1))

def _replace_re(p: Path, pattern: str, replacement: str) -> None:
    _write(p, re.sub(pattern, replacement, _read(p)))


# ─────────────────────────────────────────────────────────────────────────────
# 4. Patch Config.kt
# ─────────────────────────────────────────────────────────────────────────────

def patch_config_kt(build_dir: Path, cfg: dict) -> None:
    path = (
        build_dir
        / "app/src/main/java/com/example/webtoapp/Config.kt"
    )

    primary_color = cfg["primaryColor"]
    splash_bg     = cfg.get("splashBgColor", primary_color)
    pull_refresh  = str(
        cfg.get("features", {}).get("pullToRefresh", True)
    ).lower()   # "true" / "false"

    _replace_exact(path,
        'const val URL = "https://www.google.com"',
        f'const val URL = "{cfg["websiteUrl"]}"')

    _replace_exact(path,
        'const val APP_NAME      = "WebToApp"',
        f'const val APP_NAME      = "{cfg["appName"]}"')

    _replace_exact(path,
        'const val PRIMARY_COLOR = "#6366F1"',
        f'const val PRIMARY_COLOR = "{primary_color}"')

    _replace_exact(path,
        'const val SPLASH_BG_COLOR = "#6366F1"',
        f'const val SPLASH_BG_COLOR = "{splash_bg}"')

    _replace_exact(path,
        "const val PULL_TO_REFRESH = true",
        f"const val PULL_TO_REFRESH = {pull_refresh}")

    log.info("Patched Config.kt")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Patch build.gradle.kts
# ─────────────────────────────────────────────────────────────────────────────

def patch_build_gradle(
    build_dir: Path,
    cfg: dict,
    ks_path: Path,
    store_pass: str,
    key_pass: str,
) -> None:
    path    = build_dir / "app/build.gradle.kts"
    content = _read(path)

    # applicationId
    content = content.replace(
        f'applicationId = "{TEMPLATE_PACKAGE}"',
        f'applicationId = "{cfg["packageName"]}"',
        1,
    )

    # versionCode / versionName  (use regex so whitespace doesn't matter)
    content = re.sub(
        r'versionCode\s*=\s*\d+',
        f'versionCode   = {cfg.get("versionCode", 1)}',
        content,
    )
    content = re.sub(
        r'versionName\s*=\s*"[^"]*"',
        f'versionName   = "{cfg.get("versionName", "1.0")}"',
        content,
    )

    # ── signingConfigs block ─────────────────────────────────────────────
    # Use forward slashes so the Kotlin DSL file() call works on all OS.
    ks_posix = str(ks_path).replace("\\", "/")

    signing_block = (
        "    signingConfigs {\n"
        '        create("release") {\n'
        f'            storeFile     = file("{ks_posix}")\n'
        f'            storePassword = "{store_pass}"\n'
        f'            keyAlias      = "{KEY_ALIAS}"\n'
        f'            keyPassword   = "{key_pass}"\n'
        "        }\n"
        "    }\n\n"
    )

    if "signingConfigs" not in content:
        content = content.replace(
            "    buildTypes {",
            signing_block + "    buildTypes {",
            1,
        )

    # Wire signingConfig into the release build type
    old_release = (
        "        release {\n"
        "            isMinifyEnabled = false\n"
        "        }"
    )
    new_release = (
        "        release {\n"
        "            isMinifyEnabled = false\n"
        '            signingConfig   = signingConfigs.getByName("release")\n'
        "        }"
    )
    if 'signingConfig' not in content:
        content = content.replace(old_release, new_release, 1)

    _write(path, content)
    log.info("Patched build.gradle.kts (applicationId, version, signing config)")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Patch strings.xml, colors.xml, AndroidManifest.xml
# ─────────────────────────────────────────────────────────────────────────────

def patch_strings(build_dir: Path, cfg: dict) -> None:
    path = build_dir / "app/src/main/res/values/strings.xml"
    _replace_exact(
        path,
        f'<string name="app_name">{TEMPLATE_APP_NAME}</string>',
        f'<string name="app_name">{cfg["appName"]}</string>',
    )
    log.info("Patched strings.xml")


def patch_colors(build_dir: Path, cfg: dict) -> None:
    path      = build_dir / "app/src/main/res/values/colors.xml"
    splash_bg = cfg.get("splashBgColor", cfg["primaryColor"])
    _replace_exact(
        path,
        f'<color name="splash_background">{TEMPLATE_SPLASH_BG}</color>',
        f'<color name="splash_background">{splash_bg}</color>',
    )
    log.info("Patched colors.xml")


def patch_manifest(build_dir: Path, cfg: dict) -> None:
    """Replace the deep-link host with the host of websiteUrl."""
    path  = build_dir / "app/src/main/AndroidManifest.xml"
    host  = urlparse(cfg["websiteUrl"]).netloc   # e.g. "example.com" or "www.example.com"
    text  = _read(path)
    # Both http and https data elements share the same host attribute value
    patched = text.replace(
        f'android:host="{TEMPLATE_HOST}"',
        f'android:host="{host}"',
    )
    if patched == text:
        log.warning("Deep-link host not patched — template host %r not found", TEMPLATE_HOST)
    else:
        _write(path, patched)
        log.info("Patched AndroidManifest.xml (deep-link host → %s)", host)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Icon generation
# ─────────────────────────────────────────────────────────────────────────────

def _open_rgba(path: Path) -> Image.Image:
    img = Image.open(path)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return img


def _make_circle_mask(size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


def _square_icon(src: Image.Image, size: int) -> Image.Image:
    """Fit `src` into a `size×size` white-background PNG (letterboxed)."""
    thumb = src.copy()
    thumb.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    ox = (size - thumb.width)  // 2
    oy = (size - thumb.height) // 2
    canvas.paste(thumb, (ox, oy), thumb)
    return canvas.convert("RGB")


def _round_icon(src: Image.Image, size: int) -> Image.Image:
    """Fit `src` into a circular `size×size` RGBA PNG."""
    thumb = src.copy()
    thumb.thumbnail((size, size), Image.LANCZOS)
    # White circle background
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask   = _make_circle_mask(size)
    bg     = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    canvas.paste(bg, mask=mask)
    # Paste icon centered
    ox = (size - thumb.width)  // 2
    oy = (size - thumb.height) // 2
    canvas.paste(thumb, (ox, oy), thumb)
    return canvas


def _adaptive_foreground(src: Image.Image, canvas_px: int) -> Image.Image:
    """
    Return a `canvas_px × canvas_px` RGBA image with `src` centered and
    scaled to fit within the inner 72/108 safe zone — the area guaranteed
    to be visible on every launcher regardless of mask shape.
    """
    safe_px = int(canvas_px * 72 / 108)
    thumb   = src.copy()
    thumb.thumbnail((safe_px, safe_px), Image.LANCZOS)
    canvas  = Image.new("RGBA", (canvas_px, canvas_px), (0, 0, 0, 0))
    ox = (canvas_px - thumb.width)  // 2
    oy = (canvas_px - thumb.height) // 2
    canvas.paste(thumb, (ox, oy), thumb)
    return canvas


def generate_launcher_icons(build_dir: Path, icon_path: Path) -> None:
    """
    From a single source image produce:

    • res/mipmap-{mdpi…xxxhdpi}/ic_launcher.png       — legacy square
    • res/mipmap-{mdpi…xxxhdpi}/ic_launcher_round.png — legacy circle
    • res/drawable/ic_launcher_foreground.png          — adaptive layer (xxxhdpi quality)
    • res/drawable/ic_launcher_background.xml          — solid-white background layer
    • res/mipmap-anydpi-v26/ic_launcher{,_round}.xml  — adaptive-icon descriptors
    """
    src = _open_rgba(icon_path)
    res = build_dir / "app/src/main/res"

    # ── Legacy density PNGs ───────────────────────────────────────────────
    for folder, px in LEGACY_ICON_SIZES.items():
        dest = res / folder
        dest.mkdir(parents=True, exist_ok=True)

        # Remove any existing WebP files the template ships with
        for stale in dest.glob("ic_launcher*.webp"):
            stale.unlink()

        _square_icon(src, px).save(dest / "ic_launcher.png",       "PNG", optimize=True)
        _round_icon(src,  px).save(dest / "ic_launcher_round.png", "PNG", optimize=True)
        log.debug("  %s: %d px legacy icons", folder, px)

    # ── Adaptive foreground layer (single xxxhdpi file in drawable/) ──────
    drawable = res / "drawable"
    drawable.mkdir(parents=True, exist_ok=True)

    # Replace the XML vector foreground the template ships with a PNG
    (drawable / "ic_launcher_foreground.xml").unlink(missing_ok=True)

    xxxhdpi_canvas = ADAPTIVE_CANVAS_PX["mipmap-xxxhdpi"]   # 432 px
    fg = _adaptive_foreground(src, xxxhdpi_canvas)
    fg.save(drawable / "ic_launcher_foreground.png", "PNG", optimize=True)
    log.debug("  drawable/ic_launcher_foreground.png: %d×%d px", xxxhdpi_canvas, xxxhdpi_canvas)

    # ── Adaptive background layer ─────────────────────────────────────────
    (drawable / "ic_launcher_background.xml").write_text(_LAUNCHER_BG_XML, encoding="utf-8")

    # ── Adaptive icon descriptors ─────────────────────────────────────────
    anydpi = res / "mipmap-anydpi-v26"
    anydpi.mkdir(parents=True, exist_ok=True)
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        (anydpi / name).write_text(_ADAPTIVE_ICON_XML, encoding="utf-8")

    log.info("Generated launcher icons at all densities from %s", icon_path.name)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Splash image
# ─────────────────────────────────────────────────────────────────────────────

def place_splash_image(build_dir: Path, splash_path: Path) -> None:
    """
    Replace drawable/splash_icon with a PNG derived from splash_path.

    The AndroidX SplashScreen API renders windowSplashScreenAnimatedIcon
    (@drawable/splash_icon) in a 240 dp circle.  We produce a 288 dp canvas
    at xxxhdpi (1 152 px) with the image centered within the 240 dp safe
    circle (960 px inner) so there is a comfortable 24 dp margin on each side.
    """
    drawable = build_dir / "app/src/main/res/drawable"
    drawable.mkdir(parents=True, exist_ok=True)

    # Remove existing XML splash icon
    (drawable / "splash_icon.xml").unlink(missing_ok=True)

    CANVAS_PX = 1152   # 288 dp × 4  (xxxhdpi)
    INNER_PX  = 960    # 240 dp × 4  (visible circle diameter)

    src    = _open_rgba(splash_path)
    thumb  = src.copy()
    thumb.thumbnail((INNER_PX, INNER_PX), Image.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS_PX, CANVAS_PX), (0, 0, 0, 0))
    ox = (CANVAS_PX - thumb.width)  // 2
    oy = (CANVAS_PX - thumb.height) // 2
    canvas.paste(thumb, (ox, oy), thumb)
    canvas.save(drawable / "splash_icon.png", "PNG", optimize=True)

    log.info("Placed splash image → res/drawable/splash_icon.png")


# ─────────────────────────────────────────────────────────────────────────────
# 9. Per-customer keystore
# ─────────────────────────────────────────────────────────────────────────────

def _random_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ensure_keystore(keystore_dir: Path, cfg: dict) -> tuple[Path, str, str]:
    """
    Return ``(keystore_path, store_password, key_password)``.

    If a keystore for this packageName does not already exist it is created
    with keytool and the credentials are saved alongside it as a JSON sidecar.
    The sidecar is re-read on subsequent builds so the same signing key is used
    for every version — Play Store requires this for app updates.
    """
    safe     = cfg["packageName"].replace(".", "_")
    ks_path  = keystore_dir / f"{safe}.jks"
    cr_path  = keystore_dir / f"{safe}.creds.json"
    keystore_dir.mkdir(parents=True, exist_ok=True)

    if ks_path.exists() and cr_path.exists():
        creds = json.loads(cr_path.read_text(encoding="utf-8"))
        log.info("Re-using existing keystore: %s", ks_path)
        return ks_path, creds["storePassword"], creds["keyPassword"]

    if ks_path.exists() and not cr_path.exists():
        raise RuntimeError(
            f"Keystore {ks_path} exists but its credentials file {cr_path} is missing. "
            "Cannot sign without the passwords.  Delete the .jks file and retry to "
            "generate a fresh key (you will need to re-upload to the Play Store)."
        )

    keytool = shutil.which("keytool")
    if not keytool:
        raise EnvironmentError(
            "keytool not found on PATH.  "
            "Ensure a Java JDK (not JRE) is installed, e.g.:\n"
            "  sudo apt install openjdk-17-jdk   # Debian/Ubuntu\n"
            "  brew install openjdk@17            # macOS"
        )

    store_pass = _random_password()
    key_pass   = _random_password()
    dname      = (
        f"CN={cfg['appName']}, "
        f"OU={cfg['packageName']}, "
        "O=WebToApp, C=US"
    )

    cmd = [
        keytool,
        "-genkeypair",
        "-v",
        "-keystore",  str(ks_path),
        "-alias",     KEY_ALIAS,
        "-keyalg",    "RSA",
        "-keysize",   "2048",
        "-validity",  "10000",
        "-storepass", store_pass,
        "-keypass",   key_pass,
        "-dname",     dname,
        "-storetype", "JKS",
    ]

    log.info("Generating new keystore for %s …", cfg["packageName"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"keytool failed:\n{result.stderr}")

    creds = {
        "storePassword": store_pass,
        "keyAlias":      KEY_ALIAS,
        "keyPassword":   key_pass,
    }
    cr_path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    log.info("Keystore created:     %s", ks_path)
    log.info("Credentials saved to: %s  — keep this file secure!", cr_path)
    return ks_path, store_pass, key_pass


# ─────────────────────────────────────────────────────────────────────────────
# 10. Gradle build
# ─────────────────────────────────────────────────────────────────────────────

def _gradle_wrapper(build_dir: Path) -> str:
    if sys.platform == "win32":
        bat = build_dir / "gradlew.bat"
        return str(bat) if bat.exists() else "gradlew.bat"
    gw = build_dir / "gradlew"
    return str(gw) if gw.exists() else "./gradlew"


def run_gradle(
    build_dir: Path,
    tasks: list[str],
    sdk_root: str | None,
) -> None:
    """
    Execute `tasks` via the Gradle wrapper, streaming output.
    Raises RuntimeError with the tail of the build log on failure.
    """
    env = os.environ.copy()
    if sdk_root:
        env["ANDROID_HOME"]     = sdk_root
        env["ANDROID_SDK_ROOT"] = sdk_root

    cmd = [
        _gradle_wrapper(build_dir),
        *tasks,
        "--no-daemon",
        "--build-cache",
        "--parallel",
        "-x", "lint",
        "-x", "test",
    ]
    log.info("Running Gradle: %s", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        cwd=build_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    lines: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        lines.append(line)
        # Surface only high-signal lines to keep the terminal readable
        if any(kw in line for kw in (
            "BUILD", "FAILED", "error:", "Error:", "> Task", "Exception", "warning:"
        )):
            log.info("gradle | %s", line)

    proc.wait()

    if proc.returncode != 0:
        tail = "\n".join(lines[-80:])
        raise RuntimeError(
            f"Gradle exited with code {proc.returncode}.\n"
            f"──── last 80 lines of build output ────\n{tail}\n"
            f"────────────────────────────────────────"
        )

    log.info("Gradle tasks completed: %s", tasks)


# ─────────────────────────────────────────────────────────────────────────────
# 11. Artifact collection
# ─────────────────────────────────────────────────────────────────────────────

def collect_artifacts(
    build_dir: Path,
    output_dir: Path,
    cfg: dict,
) -> dict[str, Path]:
    """
    Locate the signed APK and AAB produced by Gradle, copy them to output_dir
    with human-readable names, and return ``{"apk": Path, "aab": Path}``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-zA-Z0-9._-]", "_", cfg["appName"])
    ver  = cfg.get("versionName", "1.0")
    arts: dict[str, Path] = {}

    searches: list[tuple[str, Path]] = [
        ("apk", build_dir / "app/build/outputs/apk/release"),
        ("aab", build_dir / "app/build/outputs/bundle/release"),
    ]
    ext_map = {"apk": "*.apk", "aab": "*.aab"}

    for kind, search_dir in searches:
        candidates = sorted(search_dir.glob(ext_map[kind])) if search_dir.exists() else []
        if not candidates:
            log.warning("No %s found in %s", kind.upper(), search_dir)
            continue
        src = candidates[0]
        dst = output_dir / f"{slug}-{ver}-release.{kind}"
        shutil.copy2(src, dst)
        arts[kind] = dst
        size_mb = dst.stat().st_size / 1_048_576
        log.info("%-3s → %s  (%.1f MB)", kind.upper(), dst, size_mb)

    if not arts:
        raise RuntimeError(
            "No APK or AAB found after the build.  "
            f"Inspect {build_dir}/app/build/outputs/ for clues."
        )

    return arts


# ─────────────────────────────────────────────────────────────────────────────
# 12. CLI entry-point
# ─────────────────────────────────────────────────────────────────────────────

def build(
    cfg: dict,
    template_dir: Path,
    output_dir: Path,
    keystore_dir: Path,
    sdk_root: str | None,
    build_dir: Path,
    tasks: list[str],
) -> dict[str, Path]:
    """Core build pipeline — called by main() and usable as a library."""

    # ── 1. Copy template ──────────────────────────────────────────────────
    prepare_build_dir(template_dir, build_dir)

    # ── 2. Keystore ───────────────────────────────────────────────────────
    ks_path, store_pass, key_pass = ensure_keystore(keystore_dir, cfg)

    # ── 3. Patch source files ─────────────────────────────────────────────
    patch_config_kt(build_dir, cfg)
    patch_build_gradle(build_dir, cfg, ks_path, store_pass, key_pass)
    patch_strings(build_dir, cfg)
    patch_colors(build_dir, cfg)
    patch_manifest(build_dir, cfg)

    # ── 4. Assets ─────────────────────────────────────────────────────────
    icon_path_str   = cfg.get("iconPath")
    splash_path_str = cfg.get("splashPath")

    if icon_path_str:
        generate_launcher_icons(build_dir, Path(icon_path_str).resolve())
    else:
        log.info("No iconPath — keeping template launcher icons")

    if splash_path_str:
        place_splash_image(build_dir, Path(splash_path_str).resolve())
    else:
        log.info("No splashPath — keeping template splash icon")

    # ── 5. Build ──────────────────────────────────────────────────────────
    run_gradle(build_dir, tasks, sdk_root)

    # ── 6. Collect artifacts ──────────────────────────────────────────────
    return collect_artifacts(build_dir, output_dir, cfg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_app.py",
        description="Build a branded Android WebView APK + AAB from a JSON config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples
            --------
            # Minimal — uses $ANDROID_HOME, puts keystores in ./keystores
            python build_app.py --config myapp.json --template ./template --output ./dist

            # Explicit SDK root, named keystore dir, keep the build tree
            python build_app.py \\
                --config       myapp.json \\
                --template     ./webtoapp-android-template \\
                --output       ./dist \\
                --keystore-dir /opt/keystores \\
                --sdk-root     /opt/android-sdk \\
                --keep-build

            # Only produce an APK (skip bundleRelease)
            python build_app.py --config myapp.json --template ./template \\
                --output ./dist --tasks assembleRelease
        """),
    )
    parser.add_argument("--config",        required=True,
                        help="Path to build-config JSON")
    parser.add_argument("--template",      required=True,
                        help="Path to the Android template repo")
    parser.add_argument("--output",        required=True,
                        help="Output directory for signed APK / AAB")
    parser.add_argument("--keystore-dir",  default="./keystores",
                        help="Directory for per-app keystores  [./keystores]")
    parser.add_argument("--sdk-root",      default=None,
                        help="Android SDK root  [$ANDROID_HOME]")
    parser.add_argument("--build-dir",     default=None,
                        help="Explicit build directory  [auto temp dir]")
    parser.add_argument("--tasks",         nargs="+",
                        default=["assembleRelease", "bundleRelease"],
                        metavar="TASK",
                        help="Gradle tasks to run  [assembleRelease bundleRelease]")
    parser.add_argument("--keep-build",    action="store_true",
                        help="Do not delete the build directory on completion")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show debug-level log output")
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Load config ───────────────────────────────────────────────────────
    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        log.error("Config file not found: %s", config_path)
        return 1

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.error("Invalid JSON in config: %s", exc)
        return 1

    # ── Validate ──────────────────────────────────────────────────────────
    try:
        validate_config(cfg)
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    template_dir = Path(args.template).resolve()
    if not template_dir.is_dir():
        log.error("Template directory not found: %s", template_dir)
        return 1

    sdk_root = args.sdk_root or os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk_root:
        log.warning(
            "ANDROID_HOME is not set and --sdk-root was not supplied.  "
            "Gradle will attempt to use the SDK configured in local.properties."
        )

    # ── Prepare build dir ─────────────────────────────────────────────────
    owns_tmp   = False
    tmp_handle = None

    if args.build_dir:
        build_dir = Path(args.build_dir).resolve()
    else:
        tmp_handle = tempfile.mkdtemp(prefix="webtoapp_")
        build_dir  = Path(tmp_handle) / "project"
        owns_tmp   = True

    try:
        artifacts = build(
            cfg          = cfg,
            template_dir = template_dir,
            output_dir   = Path(args.output).resolve(),
            keystore_dir = Path(args.keystore_dir).resolve(),
            sdk_root     = sdk_root,
            build_dir    = build_dir,
            tasks        = args.tasks,
        )
    except (RuntimeError, EnvironmentError, OSError) as exc:
        log.error("Build failed: %s", exc)
        if args.keep_build:
            log.info("Build directory kept for inspection: %s", build_dir)
        elif owns_tmp and tmp_handle:
            shutil.rmtree(tmp_handle, ignore_errors=True)
        return 1
    finally:
        if not args.keep_build and owns_tmp and tmp_handle and Path(tmp_handle).exists():
            shutil.rmtree(tmp_handle, ignore_errors=True)
        elif args.keep_build:
            log.info("Build directory kept: %s", build_dir)

    # ── Summary ───────────────────────────────────────────────────────────
    sep = "─" * 60
    log.info(sep)
    log.info("BUILD SUCCESSFUL  —  %s  v%s", cfg["appName"], cfg.get("versionName", "1.0"))
    for kind, path in artifacts.items():
        log.info("  %-3s  %s", kind.upper(), path)
    log.info(sep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
