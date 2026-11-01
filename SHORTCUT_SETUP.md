# Apple Watch 語音個人助理：捷徑 (Shortcuts) 設定指南

這份指南將帶你透過 iPhone / Mac 的「捷徑」App 建立一個專屬語音捷徑，並綁定到 **Apple Watch 側邊的實體「動作按鈕 (Action Button)」** 或加入錶面複雜功能 (Complication)。

---

## 🚀 捷徑運作原理
1. 按下 Apple Watch 動作按鈕 ➜ 啟動捷徑。
2. 手錶自動開啟麥克風聽寫你的語音（轉為純文字）。
3. 捷徑發送 HTTP POST 請求至 Mac mini（透過 Tailscale 內網穿透）。
4. Mac mini 的常駐大腦直通讀寫 `agent.md` 並生成精準回覆。
5. 手錶彈出清晰文字卡片，並同步鏡像至 Telegram Bot。

---

## 🛠️ 步驟一：在 iPhone / Mac 上建立捷徑

1. 打開 **「捷徑 (Shortcuts)」** App，點擊右上角的 **「+」** 建立新捷徑。
2. 將捷徑命名為：`問問助理`（或您自訂的名字，例如 `Hey Assistant`）。
3. 點擊右上角捷徑詳細資訊（圓圈 i 圖示），勾選 **「在 Apple Watch 上顯示」**。

---

## 📝 步驟二：加入捷徑動作（共 4 個動作）

依序從動作庫中搜尋並加入以下動作：

### 動作 1：【聽寫文字 (Dictate Text)】
* 語言設定：繁體中文 (台灣)
* 停止聽寫：暫停後 (After Pause)

### 動作 2：【取得 URL 內容 (Get Contents of URL)】
* **URL**：
  ```text
  http://<YOUR_TAILSCALE_DOMAIN>:8899/api/chat
  ```
* **方法 (Method)**：`POST`
* **標頭 (Headers)**：
  * 加入新欄位 ➜ 金鑰：`Content-Type`，值：`application/json`
  * 加入新欄位 ➜ 金鑰：`X-Agent-Key`，值：`<您在 .env 設定的 ASSISTANT_API_KEY>`
* **要求內文 (Request Body)**：選 `JSON`
  * 點擊「加入新欄位」 ➜ 類型選 `文字`
  * 金鑰：`query`
  * 值：選擇前面動作 1 的 **【聽寫的文字】** 變數

### 動作 3：【取得辭典值 (Get Dictionary Value)】
* 取得：`值 (Value)`
* 金鑰 (Key)：`reply`
* 來源辭典：選擇動作 2 的 **【來自 URL 的內容】**

### 動作 4：【顯示結果 (Show Result)】（安靜卡片模式，推薦首選）
* 文字：選擇動作 3 的 **【辭典值】**
* **效果**：回覆時完全不發出聲音干擾周圍，手錶輕微震動後在螢幕直接彈出清晰卡片，點一下即可關閉。

> 💡 *若希望手錶直接唸出聲音，只需將此動作替換為「朗讀文字 (Speak Text)」即可。*

---

## ⌚ 步驟三：綁定 Apple Watch「動作按鈕」

1. 在 Apple Watch 上打開 **「設定 (Settings)」** ➜ 滾動找到 **「動作按鈕 (Action Button)」**。
2. 將「動作」改為選擇 **「捷徑 (Shortcut)」**。
3. 在下方的捷徑選單中，選取剛剛建好的 **「問問助理」**。

---

## 🌐 關於 Tailscale 連線說明

* **在手錶上運作的條件**：
  * Apple Watch 通常透過 iPhone 藍牙共用網路；只要隨身攜帶的 iPhone 上開啟 Tailscale VPN 連線，手錶發出的所有 HTTP 請求便能自動透過 iPhone 安全直連家中的 Mac mini。
