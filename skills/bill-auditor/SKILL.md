---
name: bill-auditor
description: 每月信用卡帳單自動審計、Google Sheets 總帳同步與固定訂閱雷達。支援本機安全解密各大銀行加密 PDF、增量同步至雲端試算表及每日自動巡檢。
---

# 信用卡帳單審計與總帳管理技能 (Bill Auditor)

本技能支援各大銀行信用卡對帳單的自動化解析、本機安全解密（依據 `.env` 中的 `USER_ID_NO` 與 `USER_BIRTH_MMDD`）、本地快取以及全自動 Google Sheets 總帳同步。

腳本路徑：`.agents/skills/bill-auditor/scripts/auditor.py`

---

## 常用操作指令

### 1. 輸出指定月份帳單彙總與扣繳狀態 (Terminal / Telegram)
```bash
python3 .agents/skills/bill-auditor/scripts/auditor.py summary [YYYY-MM]
```
* 查看指定月份帳單概覽：
  ```bash
  python3 .agents/skills/bill-auditor/scripts/auditor.py summary 2026-10
  ```

### 2. 全量同步至 Google Sheets 主帳本
```bash
python3 .agents/skills/bill-auditor/scripts/auditor.py sheets-sync [YYYY-MM]
```
* 同步指定月份帳單與逐筆明細至《**個人與家庭信用卡財務總帳**》試算表：
  ```bash
  python3 .agents/skills/bill-auditor/scripts/auditor.py sheets-sync 2026-10
  ```

### 3. 查看固定訂閱與公用事業扣款雷達
```bash
python3 .agents/skills/bill-auditor/scripts/auditor.py subscriptions
```
* 自動掃描歷史帳單，過濾出 Netflix、水電瓦斯、電信費等每月固定訂閱項目與最新金額。

### 4. 每日背景輕量巡檢 (供 TaskManager 定時呼叫)
```bash
python3 .agents/skills/bill-auditor/scripts/daily_bill_sync.py
```
* 秒級檢查最近 2 天郵件，遇新出帳單自動寫入 Google Sheets 並在 Telegram 推播。

---

## 支援銀行列表
- **台新銀行**（加密 PDF，密碼：身分證後2碼+生日4碼）
- **永豐銀行**（加密 PDF，密碼：身分證大寫）
- **中國信託**（加密 PDF / 純文字自動扣繳通知信）
- **星展銀行 (DBS)**（加密 PDF，密碼：身分證後4碼+生日4碼）
- **第一銀行**（加密 PDF，密碼：身分證大寫，AES-V5）
- **國泰世華**（加密 PDF）
- **聯邦銀行**（加密 PDF / 線上繳款入帳通知）

---

## 安全防護原則
- **身分證字號與生日密碼絕不上傳**，僅由本機記憶體讀取解密。
- 所有輸出與 Google Sheets 上的信用卡號一律遮蔽（保留末四碼），身分證字號自動掩碼。
