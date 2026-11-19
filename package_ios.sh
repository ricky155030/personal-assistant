#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUMP_MODE=""
EXPLICIT_VERSION=""
LOCAL_ONLY=false
CHANGELOG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bump)
      BUMP_MODE="${2:-patch}"
      shift 2
      ;;
    --version)
      EXPLICIT_VERSION="${2:-}"
      shift 2
      ;;
    --local-only)
      LOCAL_ONLY=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--bump none|patch|minor|major] [--version X.Y.Z] [--local-only] [changelog]"
      exit 0
      ;;
    *)
      if [[ -z "$CHANGELOG" ]]; then
        CHANGELOG="$1"
      else
        CHANGELOG="$CHANGELOG $1"
      fi
      shift
      ;;
  esac
done

if [[ -z "$CHANGELOG" ]]; then
  CHANGELOG="iOS 隨身語音對話與口語 Vibe Coding 隨身助理更新"
fi

if [[ -z "$BUMP_MODE" ]]; then
  if [[ "$LOCAL_ONLY" == true || -n "$EXPLICIT_VERSION" ]]; then
    BUMP_MODE="none"
  else
    BUMP_MODE="patch"
  fi
fi

DATE_STR=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GITHUB_REPO="${GITHUB_REPO:-ricky155030/personal-assistant}"
GITHUB_RAW_BASE="https://raw.githubusercontent.com/${GITHUB_REPO}/main"

mkdir -p "$DIR/static"

# 1. 計算下一個 Build 編號與語意化版號 (SemVer)
VERSION_INFO=$(python3 - "$DIR" "$BUMP_MODE" "$EXPLICIT_VERSION" << 'PYEOF'
import json, os, re, sys

dir_path, bump_mode, explicit_ver = sys.argv[1], sys.argv[2], sys.argv[3]

builds = [0]
current_ver = "1.0.0"

public_apps_json = os.path.join(dir_path, "ios", "apps.json")
if os.path.exists(public_apps_json):
    try:
        with open(public_apps_json, "r", encoding="utf-8") as f:
            d = json.load(f)
        for app in d.get("apps", []):
            for v in app.get("versions", []):
                builds.append(int(v.get("buildVersion", 0)))
        if d.get("apps") and d["apps"][0].get("versions"):
            current_ver = d["apps"][0]["versions"][0].get("version", current_ver)
    except Exception:
        pass
else:
    proj_yml = os.path.join(dir_path, "ios", "project.yml")
    if os.path.exists(proj_yml):
        with open(proj_yml, "r", encoding="utf-8") as f:
            m = re.search(r'MARKETING_VERSION:\s*"?(\d+\.\d+\.\d+)"?', f.read())
            if m:
                current_ver = m.group(1)

for p in [
    os.path.join(dir_path, "static", "apps.json"),
    os.path.expanduser("~/Workspace/market-ticker/backend/static/apps.json"),
]:
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            for app in d.get("apps", []):
                if "MacMiniAssistant" in app.get("name", "") or "MacMiniAssistant" in app.get("bundleIdentifier", ""):
                    for v in app.get("versions", []):
                        builds.append(int(v.get("buildVersion", 0)))
        except Exception:
            pass

next_build = max(builds) + 1

if explicit_ver:
    next_ver = explicit_ver.lstrip("v")
else:
    parts = [int(x) for x in (current_ver.split(".") + ["0", "0", "0"])[:3]]
    major, minor, patch = parts[0], parts[1], parts[2]
    if bump_mode == "major":
        next_ver = f"{major + 1}.0.0"
    elif bump_mode == "minor":
        next_ver = f"{major}.{minor + 1}.0"
    elif bump_mode == "patch":
        next_ver = f"{major}.{minor}.{patch + 1}"
    else:
        next_ver = f"{major}.{minor}.{patch}"

print(f"{next_ver}|{next_build}")
PYEOF
)

NEXT_VERSION="${VERSION_INFO%%|*}"
NEXT_BUILD="${VERSION_INFO##*|}"
echo "🔢 即將打包版本: v${NEXT_VERSION} (Build ${NEXT_BUILD})"

# 2. 更新 project.yml 的 MARKETING_VERSION 並重新生成 Xcode 專案檔
python3 - "$DIR/ios/project.yml" "$NEXT_VERSION" << 'PYEOF'
import re, sys

path, ver = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

if re.search(r'MARKETING_VERSION:\s*"?[^"\n]+"?', text):
    text = re.sub(r'MARKETING_VERSION:\s*"?[^"\n]+"?', f'MARKETING_VERSION: "{ver}"', text)
else:
    text = text.replace('DEVELOPMENT_TEAM: "T25G5Y2426"', f'DEVELOPMENT_TEAM: "T25G5Y2426"\n    MARKETING_VERSION: "{ver}"')

with open(path, "w", encoding="utf-8") as f:
    f.write(text)
PYEOF

echo "⚙️ 正在重新生成 Xcode 專案檔..."
(cd "$DIR/ios" && /opt/homebrew/bin/xcodegen generate)

# 3. 同步寫入主 App、Widget 與 Share Extension 的版號與 Build 號
for plist in \
  "$DIR/ios/MacMiniAssistant/Info.plist" \
  "$DIR/ios/MacMiniAssistantWidget/Info.plist" \
  "$DIR/ios/MacMiniAssistantShare/Info.plist"; do
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $NEXT_VERSION" "$plist" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $NEXT_BUILD" "$plist" 2>/dev/null || true
done

echo "🔨 正在編譯 MacMiniAssistant v${NEXT_VERSION} (Build ${NEXT_BUILD})..."
cd "$DIR/ios"
xcodebuild -project MacMiniAssistant.xcodeproj \
  -scheme MacMiniAssistant \
  -destination "generic/platform=iOS" \
  -derivedDataPath build \
  MARKETING_VERSION="$NEXT_VERSION" \
  CURRENT_PROJECT_VERSION="$NEXT_BUILD" \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  build -quiet

echo "📦 正在打包 MacMiniAssistant.ipa..."
rm -rf /tmp/macmini_assistant_pkg
mkdir -p /tmp/macmini_assistant_pkg/Payload
cp -R "$DIR/ios/build/Build/Products/Debug-iphoneos/MacMiniAssistant.app" /tmp/macmini_assistant_pkg/Payload/
(cd /tmp/macmini_assistant_pkg && zip -r -q "$DIR/static/MacMiniAssistant.ipa" Payload)

FILE_SIZE=$(stat -f%z "$DIR/static/MacMiniAssistant.ipa")
export DIR NEXT_VERSION NEXT_BUILD DATE_STR CHANGELOG FILE_SIZE GITHUB_REPO GITHUB_RAW_BASE

# 4. 更新公開 GitHub SideStore 軟體源 (ios/apps.json) 與本機靜態軟體源
echo "📝 正在更新軟體源清單 (檔案大小: ${FILE_SIZE} bytes)..."
python3 - << 'PYEOF'
import json, os, shutil

dir_path = os.environ["DIR"]
next_ver = os.environ["NEXT_VERSION"]
next_build = os.environ["NEXT_BUILD"]
date_str = os.environ["DATE_STR"]
changelog = os.environ["CHANGELOG"]
file_size = int(os.environ["FILE_SIZE"])
github_repo = os.environ["GITHUB_REPO"]
github_raw_base = os.environ["GITHUB_RAW_BASE"]

# 4a. 更新公開託管於 GitHub 的 ios/apps.json（零機敏資訊）
public_apps_json = os.path.join(dir_path, "ios", "apps.json")
existing_versions = []
if os.path.exists(public_apps_json):
    try:
        with open(public_apps_json, "r", encoding="utf-8") as f:
            old_data = json.load(f)
        if old_data.get("apps"):
            existing_versions = old_data["apps"][0].get("versions", [])
    except Exception:
        existing_versions = []

github_download_url = f"https://github.com/{github_repo}/releases/download/v{next_ver}/MacMiniAssistant.ipa"
new_version_entry = {
    "version": next_ver,
    "buildVersion": str(next_build),
    "date": date_str,
    "localizedDescription": changelog,
    "downloadURL": github_download_url,
    "size": file_size,
    "minOSVersion": "17.0"
}

filtered_history = [v for v in existing_versions if v.get("version") != next_ver and v.get("buildVersion") != str(next_build)]
public_versions = [new_version_entry] + filtered_history[:9]

public_data = {
    "name": "Personal Assistant Repo",
    "identifier": os.environ.get("REPO_IDENTIFIER", "com.hungwei.altstore.repo"),
    "sourceURL": f"{github_raw_base}/ios/apps.json",
    "apps": [
        {
            "name": "MacMiniAssistant",
            "bundleIdentifier": os.environ.get("BUNDLE_IDENTIFIER", "com.hungwei.MacMiniAssistant"),
            "developerName": os.environ.get("DEVELOPER_NAME", "Personal Assistant"),
            "subtitle": "散步/開車免持連續語音對話與口語 Vibe Coding 隨身助理",
            "localizedDescription": "專為邊走路散步、邊開車時免持連續語音聊天與口語 Vibe Coding 打造的 iOS 原生 SwiftUI 客戶端。0.05s 零延遲首句朗讀、智慧靜音 VAD 斷句與工具狀態播報。",
            "iconURL": f"{github_raw_base}/ios/MacMiniAssistant/Assets.xcassets/AppIcon.appiconset/icon_1024.png",
            "tintColor": "007AFF",
            "versions": public_versions,
        }
    ],
}

with open(public_apps_json, "w", encoding="utf-8") as f:
    json.dump(public_data, f, ensure_ascii=False, indent=2)
print(f"✅ 公開軟體源 ios/apps.json 已更新為 v{next_ver} (Build {next_build})")

# 4b. 更新本機 static/apps.json（向下相容）
env_path = os.path.join(dir_path, ".env")
tailscale_domain = ""
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as ef:
        for line in ef:
            if line.startswith("TAILSCALE_DOMAIN="):
                tailscale_domain = line.split("=", 1)[1].strip().strip('"').strip("'")

default_server_base = f"http://{tailscale_domain}:8899" if tailscale_domain else "http://127.0.0.1:8899"
base_server_url = os.environ.get("BASE_SERVER_URL", default_server_base)

local_app_entry = dict(public_data["apps"][0])
local_app_entry["iconURL"] = f"{base_server_url}/static/icon.png"
local_app_entry["versions"] = [{
    "version": next_ver,
    "buildVersion": str(next_build),
    "date": date_str,
    "localizedDescription": changelog,
    "downloadURL": f"{base_server_url}/static/MacMiniAssistant.ipa",
    "size": file_size,
    "minOSVersion": "17.0",
}]

local_data = {
    "name": "Personal Assistant Repo",
    "identifier": os.environ.get("REPO_IDENTIFIER", "com.hungwei.altstore.repo"),
    "sourceURL": f"{base_server_url}/static/apps.json",
    "apps": [local_app_entry],
}

with open(os.path.join(dir_path, "static", "apps.json"), "w", encoding="utf-8") as f:
    json.dump(local_data, f, ensure_ascii=False, indent=2)

# 4c. 同步至外部已訂閱軟體源 (market-ticker/backend/static)
default_market_repo = os.path.expanduser("~/Workspace/market-ticker/backend/static/apps.json")
market_repo_path = os.environ.get("MARKET_TICKER_REPO_PATH", default_market_repo if os.path.exists(default_market_repo) else "")
if market_repo_path and os.path.exists(market_repo_path):
    try:
        with open(market_repo_path, "r", encoding="utf-8") as mf:
            mdata = json.load(mf)
        target_dir = os.path.dirname(market_repo_path)
        shutil.copyfile(os.path.join(dir_path, "static", "MacMiniAssistant.ipa"), os.path.join(target_dir, "MacMiniAssistant.ipa"))

        apps_list = mdata.get("apps", [])
        bundle_id = local_app_entry["bundleIdentifier"]
        source_url = mdata.get("sourceURL", "")
        if source_url and "/" in source_url:
            inferred_base = "/".join(source_url.split("/")[:3])
        elif tailscale_domain:
            inferred_base = f"http://{tailscale_domain}:8765"
        else:
            inferred_base = "http://127.0.0.1:8765"
        market_base_url = os.environ.get("MARKET_BASE_URL", inferred_base)

        apps_list = [a for a in apps_list if not (a.get("name") == "MacMiniAssistant" and a.get("bundleIdentifier") != bundle_id)]
        found = False
        for app in apps_list:
            if app.get("bundleIdentifier") == bundle_id:
                app["versions"] = [{
                    "version": next_ver,
                    "buildVersion": str(next_build),
                    "date": date_str,
                    "localizedDescription": changelog,
                    "downloadURL": f"{market_base_url}/static/MacMiniAssistant.ipa",
                    "size": file_size,
                    "minOSVersion": "17.0",
                }]
                found = True
                break
        if not found:
            market_app_entry = dict(local_app_entry)
            market_app_entry["iconURL"] = f"{market_base_url}/static/assistant_icon.png"
            market_app_entry["versions"][0]["downloadURL"] = f"{market_base_url}/static/MacMiniAssistant.ipa"
            apps_list.append(market_app_entry)
        mdata["apps"] = apps_list
        with open(market_repo_path, "w", encoding="utf-8") as mf:
            json.dump(mdata, mf, ensure_ascii=False, indent=2)
        print("✅ 同步至外部已訂閱軟體源 (market-ticker) 成功！")
    except Exception as e:
        print(f"⚠️ 同步至外部軟體源失敗: {e}")
PYEOF

# 5. 若非 --local-only，提交版本清單並自動建立 GitHub Release
if [[ "$LOCAL_ONLY" == false ]]; then
  cd "$DIR"
  echo "☁️ 正在推送軟體源與版本更新至 GitHub..."
  git add ios/apps.json ios/project.yml \
    ios/MacMiniAssistant/Info.plist \
    ios/MacMiniAssistantWidget/Info.plist \
    ios/MacMiniAssistantShare/Info.plist
  if ! git diff --cached --quiet; then
    git commit -m "chore(ios): release v${NEXT_VERSION} (Build ${NEXT_BUILD})"
  fi
  git push origin HEAD:main

  TAG="v${NEXT_VERSION}"
  if gh release view "$TAG" --repo "$GITHUB_REPO" >/dev/null 2>&1; then
    echo "🔄 Release $TAG 已存在，覆蓋上傳新版 MacMiniAssistant.ipa..."
    gh release upload "$TAG" "$DIR/static/MacMiniAssistant.ipa" --clobber --repo "$GITHUB_REPO"
    gh release edit "$TAG" --title "v${NEXT_VERSION} (Build ${NEXT_BUILD})" --notes "$CHANGELOG" --repo "$GITHUB_REPO"
  else
    echo "🚀 建立 GitHub Release: $TAG..."
    gh release create "$TAG" "$DIR/static/MacMiniAssistant.ipa" \
      --title "v${NEXT_VERSION} (Build ${NEXT_BUILD})" \
      --notes "$CHANGELOG" \
      --repo "$GITHUB_REPO"
  fi
  echo "🎉 GitHub Release 上線完成！"
  echo "   SideStore 軟體源: ${GITHUB_RAW_BASE}/ios/apps.json"
else
  echo "🧪 本機預檢打包完成 (--local-only)：v${NEXT_VERSION} (Build ${NEXT_BUILD})"
fi
