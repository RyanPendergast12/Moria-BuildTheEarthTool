#!/usr/bin/env bash
# Build the plugin JAR (javac only, no Gradle/Maven): tools/build_plugin.sh [out.jar]
# Classpath: Paper 1.18.2 #388 libraries (from a server that has run once) + WorldEdit 7.2.20.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="${MORIA_BUILD_SERVER:-/c/BTE-Migration/07-shelltool-test-server}"
JDK="${MORIA_JDK:-/c/BTE-Migration/runtimes/jdk-17.0.20.1+1}"
VER=$(sed -n 's/^version: *//p' "$REPO/plugin/src/main/resources/plugin.yml" | tr -d '\r')
OUT="${1:-SummerlinShellTool-$VER.jar}"
JARS=$( { find "$SERVER/libraries" "$SERVER/cache" "$SERVER/versions" -name '*.jar' 2>/dev/null; find "$SERVER/plugins" -maxdepth 1 -name 'worldedit-bukkit-*.jar'; } )
CP=$(printf '%s\n' "$JARS" | while read -r j; do cygpath -w "$j"; done | tr '\n' ';')   # javac on Windows needs C:\ paths
BD=$(mktemp -d); trap 'rm -rf "$BD"' EXIT
"$JDK/bin/javac" -nowarn -encoding UTF-8 --release 17 -cp "$CP" -d "$(cygpath -w "$BD")" "$REPO"/plugin/src/main/java/org/bte/summerlin/shelltool/*.java
cp "$REPO"/plugin/src/main/resources/*.yml "$BD/"
mkdir -p "$REPO/plugin/build"
(cd "$BD" && "$JDK/bin/jar" --create --file "$(cygpath -w "$REPO/plugin/build/$OUT")" .)
sha256sum "$REPO/plugin/build/$OUT"
