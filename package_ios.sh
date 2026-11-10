#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHANGELOG="${1:-iOS 隨身語音對話與口語 Vibe Coding 隨身助理初版發布}"
DATE_STR=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

mkdir -p "$DIR/static"

# 1. 預先計算下一個 Build 編號 (比對本機與 market-ticker 外部軟體源)
CURRENT_BUILD=$(python3 -c "
import json, os
builds = [0]
for p in ['$DIR/static/apps.json', os.path.expanduser('~/Workspace/market-ticker/backend/static/apps.json')]:
    if os.path.exists(p):
        try:
            with open(p) as f:
                d = json.load(f)
            for app in d.get('apps', []):
                if 'MacMiniAssistant' in app.get('name', '') or 'MacMiniAssistant' in app.get('bundleIdentifier', ''):
                    for v in app.get('versions', []):
                        builds.append(int(v.get('buildVersion', 0)))
        except Exception:
            pass
print(max(builds))
")
NEXT_BUILD=$((CURRENT_BUILD + 1))
echo "🔢 即將發布版本: Build $NEXT_BUILD"

# 2. 重新產生 Xcode 專案檔
echo "⚙️ 正在重新生成 Xcode 專案檔..."
(cd "$DIR/ios" && /opt/homebrew/bin/xcodegen generate)

# 3. 將新 Build 寫入 Info.plist (主 App 與 Widget 必須嚴格同步，否則 IXPlaceholder 會報錯)
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $NEXT_BUILD" "$DIR/ios/MacMiniAssistant/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $NEXT_BUILD" "$DIR/ios/MacMiniAssistantWidget/Info.plist" 2>/dev/null || true

echo "🔨 正在編譯 MacMiniAssistant (Build $NEXT_BUILD)..."
cd "$DIR/ios"
xcodebuild -project MacMiniAssistant.xcodeproj \
  -scheme MacMiniAssistant \
  -destination "generic/platform=iOS" \
  -derivedDataPath build \
  CURRENT_PROJECT_VERSION="$NEXT_BUILD" \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  build -quiet

echo "📦 正在打包 MacMiniAssistant.ipa..."
rm -rf /tmp/macmini_assistant_pkg
mkdir -p /tmp/macmini_assistant_pkg/Payload
cp -R "$DIR/ios/build/Build/Products/Debug-iphoneos/MacMiniAssistant.app" /tmp/macmini_assistant_pkg/Payload/
(cd /tmp/macmini_assistant_pkg && zip -r -q "$DIR/static/MacMiniAssistant.ipa" Payload)

FILE_SIZE=$(stat -f%z "$DIR/static/MacMiniAssistant.ipa")
export DIR NEXT_BUILD DATE_STR CHANGELOG FILE_SIZE

# 4. 更新本專案專屬軟體源 static/apps.json
echo "📝 正在更新本專案軟體源 static/apps.json (檔案大小: ${FILE_SIZE} bytes)..."
python3 - << 'PYEOF'
import json, sys, os

dir_path = os.environ.get("DIR", "")
apps_json_path = os.path.join(dir_path, "static", "apps.json")
next_build = os.environ.get("NEXT_BUILD", "1")
date_str = os.environ.get("DATE_STR", "")
changelog = os.environ.get("CHANGELOG", "")
file_size = int(os.environ.get("FILE_SIZE", 0))

app_entry = {
    "name": "MacMiniAssistant",
    "bundleIdentifier": os.environ.get("BUNDLE_IDENTIFIER", "com.personalassistant.MacMiniAssistant"),
    "developerName": os.environ.get("DEVELOPER_NAME", "Personal Assistant"),
    "subtitle": "散步/開車免持連續語音對話與口語 Vibe Coding 隨身助理",
    "localizedDescription": "專為邊走路散步、邊開車時免持連續語音聊天與口語 Vibe Coding 打造的 iOS 原生 SwiftUI 客戶端。0.05s 零延遲首句朗讀、智慧靜音 VAD 斷句與工具狀態播報。",
    "iconURL": f"{os.environ.get('BASE_SERVER_URL', 'http://127.0.0.1:8899')}/static/icon.png",
    "tintColor": "007AFF",
    "versions": [
        {
            "version": "1.0.0",
            "buildVersion": str(next_build),
            "date": date_str,
            "localizedDescription": changelog,
            "downloadURL": f"{os.environ.get('BASE_SERVER_URL', 'http://127.0.0.1:8899')}/static/MacMiniAssistant.ipa",
            "size": file_size,
            "minOSVersion": "17.0"
        }
    ]
}

data = {
    "name": "Personal Assistant Repo",
    "identifier": os.environ.get("REPO_IDENTIFIER", "com.personalassistant.altstore.repo"),
    "sourceURL": f"{os.environ.get('BASE_SERVER_URL', 'http://127.0.0.1:8899')}/static/apps.json",
    "apps": [app_entry]
}

with open(apps_json_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"✅ mac-mini-assistant 軟體源已更新為 Build {next_build}！")

# 5. 同步至外部已訂閱軟體源 (market-ticker/backend/static)
default_market_repo = os.path.expanduser("~/Workspace/market-ticker/backend/static/apps.json")
market_repo_path = os.environ.get("MARKET_TICKER_REPO_PATH", default_market_repo if os.path.exists(default_market_repo) else "")
if market_repo_path and os.path.exists(market_repo_path):
    try:
        with open(market_repo_path, "r", encoding="utf-8") as mf:
            mdata = json.load(mf)
        
        # 拷貝 IPA 到該目錄
        import shutil
        target_dir = os.path.dirname(market_repo_path)
        shutil.copyfile(os.path.join(dir_path, "static", "MacMiniAssistant.ipa"), os.path.join(target_dir, "MacMiniAssistant.ipa"))

        # 檢查是否已存在 MacMiniAssistant
        apps_list = mdata.get("apps", [])
        bundle_id = app_entry.get("bundleIdentifier")
        market_base_url = os.environ.get("MARKET_BASE_URL", "http://127.0.0.1:8765")
        found = False
        for app in apps_list:
            if app.get("bundleIdentifier") == bundle_id:
                app["versions"] = [{
                    "version": "1.0.0",
                    "buildVersion": str(next_build),
                    "date": date_str,
                    "localizedDescription": changelog,
                    "downloadURL": f"{market_base_url}/static/MacMiniAssistant.ipa",
                    "size": file_size,
                    "minOSVersion": "17.0"
                }]
                found = True
                break
        
        if not found:
            market_app_entry = dict(app_entry)
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

echo "🚀 發布完成！已在專屬軟體源上線："
echo "   軟體源: ${BASE_SERVER_URL:-http://127.0.0.1:8899}/static/apps.json"
