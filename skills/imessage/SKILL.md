---
name: imessage
description: 透過 Mac mini 原生 iMessage 發送訊息給主人或重要家人。嚴格落實白名單保護，僅允許發送給已登記之聯絡人。
---

# iMessage 發送技能 (iMessage Skill)

當主人要求「傳 iMessage 給家人」、「傳訊息給我自己」、「發 iMessage 說...」時使用本技能。

本技能腳本路徑：`.agents/skills/imessage/scripts/send_imessage.py`

## 嚴格安全白名單 (Security Whitelist)

為防範誤發或濫發，本技能具備嚴格的白名單檢驗，**僅允許發送給已登記之白名單對象**（可由 `.env` 或 `data/imessage_contacts.json` 配置）：
1. **主人自己**：`OWNER_PHONE`（別名：`我`、`自己`、`主人`、`我自己`）
2. **家人/伴侶**：`FAMILY_PHONE`（別名：`家人`、`老婆`、`先生`）

> **安全鐵律**：任何非白名單內的電話號碼或姓名，腳本將直接拒絕執行。

---

## 執行指令格式

### 1. 傳送給主人自己 (測試或備忘)：
```bash
python3 .agents/skills/imessage/scripts/send_imessage.py --to "我" --message "這是來自 Mac mini 助理的測試訊息！"
```

### 2. 傳送給家人/伴侶：
```bash
python3 .agents/skills/imessage/scripts/send_imessage.py --to "家人" --message "我剛下班囉，等等回家順便買晚餐回去 🥰❤️"
```

---

## 語氣規範與回應規則：
- **傳送給家人的專屬語氣規範**：
  - **維持大腦 LLM 決策**：不走生硬的靜態模板，由大腦根據主人當前情境潤飾。
  - **語句多加可愛溫馨 Emoji**：適量加入貼切表情符號（如 🥰, ❤️, 💕, 🏃‍♂️💨, 🚗, ✨, 🍱 等）。
  - **親切日常口氣**：符合家人平常互動的親暱口吻，切勿冷冰冰或公事化。
- **回報主人**：
  - 發送成功後，簡明回報主人：「👌 已經透過 iMessage 傳送囉：『...』」
  - 保持 1~2 句話，口語自然，不要輸出 Markdown 標題符號。
  - 若遇到 macOS 權限錯誤（-1743），主動提醒需在 Mac mini 系統設定點選「允許」。
