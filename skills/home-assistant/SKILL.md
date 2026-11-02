---
name: home-assistant
description: 控制與查詢 Home Assistant 智慧家電設備。當主人要求開關燈光、查詢或設定冷氣溫度、控制插座、新風機、電視或查詢溫濕度感測器時使用本技能。
---

# Home Assistant 控制技能 (Home Assistant Skill)

當主人要求查詢或控制家中的智慧家電時：
本技能腳本路徑：`.agents/skills/home-assistant/scripts/ha_tool.py`

> **重要原則**：
> 1. 請優先查閱 `memory/homeassistant.md` 獲取主人家所有設備之專屬實體 ID (Entity Mapping)。
> 2. 若 `memory/homeassistant.md` 已記載實體 ID，請直接調用對應指令，一步到位！切勿隨意遍歷查詢。

---

## 常用控制指令範例：
1. **開設備**：
   ```bash
   python3 .agents/skills/home-assistant/scripts/ha_tool.py on <entity_id>
   ```
2. **關設備**：
   ```bash
   python3 .agents/skills/home-assistant/scripts/ha_tool.py off <entity_id>
   ```
3. **查狀態**：
   ```bash
   python3 .agents/skills/home-assistant/scripts/ha_tool.py get <entity_id>
   ```
4. **調整冷氣溫度**：
   ```bash
   python3 .agents/skills/home-assistant/scripts/ha_tool.py temp <climate_id> <溫度數字>
   ```
5. **呼叫自訂服務**：
   ```bash
   python3 .agents/skills/home-assistant/scripts/ha_tool.py service <domain> <service> '<json_data>'
   ```

## 回應規則：
- 執行完指令後，向主人簡明匯報執行結果（例如：「已經為你關閉廁所電燈了。」或「客廳目前溫度 27.9 度，濕度 45%。」）。
- 保持 1~2 句話，口語化，不要輸出任何 Markdown 標題符號或星號。
