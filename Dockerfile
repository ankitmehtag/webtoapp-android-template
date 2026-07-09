# ─────────────────────────────────────────────────────────────────────────────
# WebToApp Android build image
#
# Four-stage build for optimal layer caching and warm build speed:
#
#   sdk       — Ubuntu 22.04 + JDK 17 + Android SDK + Python/Pillow
#               Rebuilt only when SDK/tool versions change.
#
#   deps      — Gradle descriptors only + dependency resolution.
#               Downloads ~700 MB of Maven artefacts into ~/.gradle/caches.
#               Rebuilt only when build.gradle.kts / libs.versions.toml change.
#
#   compiled  — Real app source added on top of deps, then assembleDebug.
#               Warms the Gradle build cache (~/.gradle/caches/build-cache-1).
#               Rebuilt when app/src/ changes.
#
#   final     — Clean sdk base + ~/.gradle from compiled + full template.
#               This is the shipped image.  ~/.gradle contains pre-warmed
#               Gradle distribution, all Maven artifacts, and build-cache
#               entries so container builds skip ~2–3 min of downloads/setup.
#
# Expected warm build time: ~90–120 s (assembleRelease + bundleRelease)
#
# Build:  docker build -t webtoapp-builder .
# Run:    docker run --rm \
#           -v /path/to/input:/input:ro \
#           -v /path/to/output:/output \
#           -v /path/to/keystores:/keystores \
#           webtoapp-builder
# ─────────────────────────────────────────────────────────────────────────────

# ─── Stage 1: sdk ─────────────────────────────────────────────────────────────
# eclipse-temurin gives us a well-maintained Ubuntu 22.04 LTS + JDK 17 base.
FROM eclipse-temurin:17-jdk-jammy AS sdk

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        curl \
        unzip \
        git \
    && rm -rf /var/lib/apt/lists/*

# Pillow — used by build_app.py for icon generation
RUN pip3 install --no-cache-dir "Pillow==10.4.0"

# ── Android command-line tools ────────────────────────────────────────────────
# Pin versions via build args so a single ARG change triggers a targeted rebuild.
ARG CMDLINE_TOOLS_BUILD=11076708
ARG BUILD_TOOLS_VERSION=35.0.0
ARG COMPILE_SDK=35

ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk

RUN mkdir -p "${ANDROID_HOME}/cmdline-tools" \
    && curl -fsSL \
       "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS_BUILD}_latest.zip" \
       -o /tmp/cmdtools.zip \
    && unzip -q /tmp/cmdtools.zip -d /tmp/cmdtools \
    && mv /tmp/cmdtools/cmdline-tools "${ANDROID_HOME}/cmdline-tools/latest" \
    && rm -rf /tmp/cmdtools.zip /tmp/cmdtools

ENV PATH="${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/build-tools/${BUILD_TOOLS_VERSION}:${PATH}"

# Accept licences and install SDK components in one layer
RUN yes | sdkmanager --licenses > /dev/null 2>&1 \
    && sdkmanager \
        "platform-tools" \
        "build-tools;${BUILD_TOOLS_VERSION}" \
        "platforms;android-${COMPILE_SDK}"


# ─── Stage 2: deps ────────────────────────────────────────────────────────────
# Copy ONLY the Gradle descriptor files, then resolve all dependencies.
# This layer is cached even when app/src/ changes, so Maven artifacts (~700 MB)
# are never re-downloaded unless build.gradle.kts or libs.versions.toml change.
FROM sdk AS deps

WORKDIR /template

# Gradle wrapper (pins the Gradle distribution version)
COPY gradle/   ./gradle/
COPY gradlew   ./gradlew
COPY gradlew.bat ./gradlew.bat
RUN chmod +x ./gradlew

# Root build scripts and version catalog
COPY build.gradle.kts    ./build.gradle.kts
COPY settings.gradle.kts ./settings.gradle.kts
COPY gradle.properties   ./gradle.properties

# App build descriptor (dependency declarations live here)
COPY app/build.gradle.kts ./app/build.gradle.kts

# Minimal AndroidManifest — AGP needs it to configure the :app project.
# With `namespace` set in build.gradle.kts (AGP 7.3+) an empty manifest works.
RUN mkdir -p app/src/main \
    && printf '<?xml version="1.0" encoding="utf-8"?>\n<manifest xmlns:android="http://schemas.android.com/apk/res/android"/>\n' \
       > app/src/main/AndroidManifest.xml

# Download Gradle distribution and every Maven artefact that appears on the
# compile + runtime + annotation classpaths.
# GRADLE_USER_HOME is ~/. gradle — this is the cache we preserve across stages.
RUN GRADLE_USER_HOME=/root/.gradle \
    ./gradlew \
        :app:dependencies \
        --no-daemon \
        --build-cache \
        --no-configuration-cache \
        2>&1 \
        | grep -E "(Downloading |Download |BUILD |FAILED|Exception)" \
        || true \
    && echo ">>> Dependency resolution complete."


# ─── Stage 3: compiled ────────────────────────────────────────────────────────
# Layer real source on top of the dep-warmed image and run assembleDebug.
# This populates the Gradle build cache with compiled bytecode and processed
# resources so the container's release build can restore unchanged modules
# from cache instead of recompiling from scratch.
FROM deps AS compiled

# Overwrite the stub manifest with the full real source tree
COPY app/src/ ./app/src/

# Also need proguard rules if present
COPY app/proguard-rules.pro ./app/proguard-rules.pro

# assembleDebug needs no signing config and exercises the same
# compile + resource-processing paths as assembleRelease.
RUN GRADLE_USER_HOME=/root/.gradle \
    ./gradlew \
        assembleDebug \
        --no-daemon \
        --build-cache \
        --no-configuration-cache \
        2>&1 \
        | tail -30 \
    && echo ">>> Build-cache warm complete." \
    # Discard build output — only the ~/.gradle caches matter
    && rm -rf ./app/build


# ─── Stage 4: final ───────────────────────────────────────────────────────────
# Clean sdk base + pre-warmed ~/.gradle + complete unmodified template.
# build_app.py copies /template to a writable workspace and patches it at run time.
FROM sdk AS final

# Import pre-warmed Gradle caches from stage 3
# Contents:
#   ~/.gradle/wrapper/dists/          — Gradle distribution (~120 MB)
#   ~/.gradle/caches/modules-2/       — Maven artefacts   (~600 MB)
#   ~/.gradle/caches/build-cache-1/   — Compiled bytecode / resource cache
COPY --from=compiled /root/.gradle /root/.gradle

# Full unmodified Android template (patched in /workspace at run time, not here)
COPY gradle/          /template/gradle/
COPY gradlew          /template/gradlew
COPY gradlew.bat      /template/gradlew.bat
COPY build.gradle.kts /template/build.gradle.kts
COPY settings.gradle.kts /template/settings.gradle.kts
COPY gradle.properties   /template/gradle.properties
COPY app/             /template/app/
RUN chmod +x /template/gradlew

# Build script and entrypoint
COPY build_app.py         /app/build_app.py
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# ── Runtime configuration ─────────────────────────────────────────────────────
ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    GRADLE_USER_HOME=/root/.gradle \
    PATH="${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/build-tools/35.0.0:${PATH}"

# /input      — bind-mount: config.json + optional assets/ (read-only)
# /output     — bind-mount: receives signed APK + AAB after the build
# /keystores  — bind-mount: per-customer .jks and .creds.json (must persist!)
VOLUME ["/input", "/output", "/keystores"]

WORKDIR /workspace
ENTRYPOINT ["/app/docker-entrypoint.sh"]
