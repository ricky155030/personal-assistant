---
name: gmail
description: 查詢、搜尋與摘要主人的 Gmail 郵件。支援查詢最近信件、過濾重要通知（帳單、掛號、訂單、銀行）、搜尋關鍵字及未讀郵件。
---

# Gmail 郵件管理技能 (Gmail Skill)

本技能專為個人助理設計，透過 Mac mini 本機的 Google Workspace CLI (`gws`) 安全讀取 Gmail 郵件。

本技能腳本路徑：`.agents/skills/gmail/scripts/gmail_tool.py`

## 常用操作指令

### 1. 查詢重要信件（自動排除促銷與社群雜訊）
```bash
python3 .agents/skills/gmail/scripts/gmail_tool.py important [天數] [數量]
```
* 查詢近 7 天的重要信件（預設 20 則）：
  ```bash
  python3 .agents/skills/gmail/scripts/gmail_tool.py important 7 20
  ```
* 查詢近 3 天的重要信件：
  ```bash
  python3 .agents/skills/gmail/scripts/gmail_tool.py important 3 10
  ```

### 2. 查詢未讀或特定關鍵字信件
```bash
python3 .agents/skills/gmail/scripts/gmail_tool.py search "<query>" [數量]
```
* 查詢未讀信件：
  ```bash
  python3 .agents/skills/gmail/scripts/gmail_tool.py search "is:unread" 10
  ```
* 查詢信用卡或銀行帳單：
  ```bash
  python3 .agents/skills/gmail/scripts/gmail_tool.py search "帳單 OR 繳款 OR 信用卡" 10
  ```
* 查詢包裹或物流快遞：
  ```bash
  python3 .agents/skills/gmail/scripts/gmail_tool.py search "包裹 OR 出貨 OR 配送 OR 宅配" 10
  ```

### 3. 取得單一信件詳情（含已自動解碼完整內文）
```bash
python3 .agents/skills/gmail/scripts/gmail_tool.py get <message_id>
```
* **自動解碼保證**：本指令已原生將 MIME、Base64URL 與 HTML 標籤遞迴解析為乾淨純文字，並置於回傳的 `"body"` 欄位中。**嚴禁再額外撰寫自訂 Python 腳本手動解碼 Base64**，直接讀取 `"body"` 即可回答機票、驗證碼、訂單或明細！

---

## 回應規則
- 向主人回報時，自動過濾掉無意義的行銷廣告或電子報雜訊，聚焦在：
  1. **財務帳單與扣款**（信用卡、水電瓦斯、房貸等）
  2. **包裹物流與外送**（出貨進度、取件提醒）
  3. **帳號安全性與重大通知**（登入異常、條款異動、醫療掛號等）
  4. **訂閱的重要財經/技術電子報重點**
- 保持條理清晰、重點標示，不要傾倒未處理的原始 JSON。
