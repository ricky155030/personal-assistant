"""
Mac mini Assistant 效能自主審查與改進提案器 (Performance Reviewer) - LLM 深度診斷升級版

職責：
1. 定期（每日夜間 23:30 或隨時手動指令）掃描 data/history.jsonl。
2. 提取回應耗時偏長（預設 >= 15 秒）之問答紀錄。
3. 呼叫 Gemini 3.8 Flash (via agy -p) 進行嚴格、精準、有工程依據的根因診斷與改進提案。
4. 嚴格杜絕敷衍空話（如「評估是否為高頻日常」、「加入快捷常規」等），明確區分：
   - 【良性深研】(Legitimate Deep Inquiry)：外部聯網檢索冷門資訊、多方財報比對、深度推理，耗時符合預期，無需工程干預。
   - 【🚨 系統瓶頸】(System Bottleneck)：缺少定位技能、試算表多筆寫入無批次支援、路由未命中極速通道等，給出具體修改目標檔案與技術方案。
5. 聚合 Top 2~3 項系統級工程優化提案，產出美觀現代的 HTML 視覺化報告與 Telegram 摘要。
"""

import os
import re
import sys
import json
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

from config import settings

class PerformanceReviewer:
    def __init__(self):
        self.history_file = settings.HISTORY_LOG_PATH
        self.reports_dir = settings.REPORTS_DIR

    def get_slow_queries(
        self, 
        hours: int = 24, 
        min_latency: float = 15.0, 
        date_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """讀取歷史紀錄中耗時 >= min_latency 秒的問答紀錄
        
        Args:
            hours: 往前推算之小時數 (若 date_filter 為 None 時生效)
            min_latency: 門檻秒數 (預設 15.0s)
            date_filter: 指定日期前綴，如 '2026-10-12'，若有指定則以此日期優先
        """
        if not self.history_file.exists():
            return []

        cutoff_time = datetime.now() - timedelta(hours=hours)
        slow_records = []

        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts_str = entry.get("timestamp", "")
                        if not ts_str:
                            continue
                        
                        if date_filter:
                            if not ts_str.startswith(date_filter):
                                continue
                            entry_dt = datetime.fromisoformat(ts_str)
                        else:
                            entry_dt = datetime.fromisoformat(ts_str)
                            if entry_dt < cutoff_time:
                                continue

                        # 解析延遲數據
                        actions_str = " ".join(entry.get("actions", []))
                        m_total = re.search(r"total:\s*([\d\.]+)s", actions_str)
                        m_ttft = re.search(r"ttft:\s*([\d\.]+)s", actions_str)

                        total_lat = float(m_total.group(1)) if m_total else 0.0
                        ttft = float(m_ttft.group(1)) if m_ttft else 0.0

                        if total_lat >= min_latency:
                            entry["latency"] = total_lat
                            entry["ttft"] = ttft
                            entry["datetime"] = entry_dt
                            slow_records.append(entry)
                    except Exception:
                        continue
        except Exception as e:
            print(f"[PerformanceReviewer] 讀取歷史日誌錯誤: {e}")

        # 依耗時由大到小排序
        slow_records.sort(key=lambda x: x.get("latency", 0), reverse=True)
        return slow_records

    def diagnose_with_llm(self, slow_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """呼叫 Gemini 3.8 Flash (agy -p) 進行深度工程診斷"""
        if not slow_records:
            return {
                "summary": {"total_slow_queries": 0, "legitimate_inquiry_count": 0, "system_bottleneck_count": 0},
                "global_proposals": [],
                "items": []
            }

        # 準備送入 LLM 的摘要資料 (取前 25 筆最慢紀錄避免 Prompt 超載)
        audit_items = []
        for i, item in enumerate(slow_records[:25], 1):
            audit_items.append({
                "index": i,
                "timestamp": item.get("timestamp", ""),
                "latency_sec": item.get("latency", 0.0),
                "ttft_sec": item.get("ttft", 0.0),
                "query": item.get("query", ""),
                "reply_snippet": item.get("reply", "")[:140]
            })

        prompt = f"""你是一位資深 Mac mini AI 助理架構師與效能稽核專家。
助理系統架構如下：
1. 主大腦為常駐守護進程 (Gemini 3.8 Flash)，支援語音 (Apple Watch/HomePod) 與文字 (Telegram)。
2. 具備 fast_intent.py 極速通道（家電直控 0.02s，語音快速意圖）。
3. 具備擴充技能庫 (.agents/skills/)：如 google-calendar, imessage, gmail, google-sheets, shopping-list。
4. 亦支援通用外部搜尋與深度推理。

以下是待審查的 {len(audit_items)} 筆回應偏長（>15秒）的問答紀錄：
{json.dumps(audit_items, ensure_ascii=False, indent=2)}

請對這批問答進行嚴格、精確、具體、有工程依據的根因診斷與改進提案。
【絕對禁止原則】：
- 嚴禁空話與罐頭文字（例如「評估是否為高頻日常問題」、「加入快捷常規」、「優化 Prompt 捷徑」、「加強提示詞引導」等假大空敷衍語）。
- 嚴禁把每一筆問題都診斷為「深度推理 / 模型思考耗時」。

【診斷分類標準】：
1. 【良性深研】(type: "LEGITIMATE_DEEP_INQUIRY")：
   - 包含外部聯網檢索冷門即時資訊（如特定股票營收、景點/店家推薦攻略、遊戲機制解釋）。
   - category 填「良性深研 (外部聯網/深度分析)」
   - severity 填「正常 (無需干預)」
   - root_cause 寫清楚為什麼需要耗時（例如語音辨識容錯、外部多個財報網頁檢索與數字比對等）。
   - proposal 填「屬於正常深度調研產出，目前無需工程干預；保持現狀即可。」
   - estimated_saving 填「無 (符合預期)」

2. 【系統瓶頸】(type: "SYSTEM_BOTTLENECK")：
   - 明顯有架構優化空間（例如：使用者問即時位置但系統缺少專屬定位技能導致反覆搜尋推算；使用者一次交代 4~5 樣待買/備品導致多輪試算表寫入與往返；家電或高頻指令未命中 fast_intent）。
   - category 填具體瓶頸名稱（例如「待買清單缺乏批量寫入」等）
   - severity 填「高」或「中」
   - root_cause 深入剖析系統為什麼卡住。
   - proposal 必須給出具體工程改動（指定檔案或技能名稱，例如「在 shopping-list 技能實作批量解析...」）。
   - estimated_saving 具體預估節省秒數。

3. 【全域聚合提案】(global_proposals)：
   - 將系統瓶頸聚合為 Top 2~3 項最具效益的具體工程改進項目。
   - 包含 title, affected_count, target_files (清單), action_plan, expected_impact。

請輸出嚴格的 JSON 格式（不要有額外說明，以 ```json 包裹或純 JSON）：
{{
  "summary": {{
    "total_slow_queries": {len(audit_items)},
    "legitimate_inquiry_count": 0,
    "system_bottleneck_count": 0
  }},
  "global_proposals": [
    {{
      "title": "優化項目標題",
      "affected_count": 2,
      "target_files": ["相關檔案路徑"],
      "action_plan": "具體實作方案",
      "expected_impact": "預期效益"
    }}
  ],
  "items": [
    {{
      "index": 1,
      "type": "SYSTEM_BOTTLENECK 或 LEGITIMATE_DEEP_INQUIRY",
      "category": "分類名稱",
      "severity": "優先級",
      "root_cause": "深入原因",
      "proposal": "具體提案",
      "estimated_saving": "預期節省"
    }}
  ]
}}
"""

        try:
            cmd = [settings.AGY_BIN, "-p", prompt, "--model", "gemini-3.8-flash-low"]
            print(f"[PerformanceReviewer] 🤖 呼叫 Gemini 3.8 Flash (agy -p) 進行 {len(audit_items)} 筆慢問答深入架構審查...")
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=90,
                cwd=str(settings.BASE_DIR)
            )

            if res.returncode == 0 and res.stdout.strip():
                raw_out = res.stdout.strip()
                # 提取 JSON 區塊
                json_match = re.search(r"\{[\s\S]*\}", raw_out)
                if json_match:
                    diag_data = json.loads(json_match.group(0))
                    print(f"[PerformanceReviewer] ✅ LLM 診斷完成：偵測到 {diag_data.get('summary', {}).get('system_bottleneck_count', 0)} 個系統瓶頸，{diag_data.get('summary', {}).get('legitimate_inquiry_count', 0)} 筆良性深研")
                    return diag_data
                else:
                    print(f"[PerformanceReviewer] ⚠️ LLM 回傳未能提取有效 JSON，切換至備援規則診斷")
            else:
                print(f"[PerformanceReviewer] ⚠️ LLM 呼叫失敗 (RC={res.returncode}): {res.stderr[:200]}")
        except Exception as e:
            print(f"[PerformanceReviewer] ❌ 呼叫 LLM 診斷異常: {e}")

        # 備援機制 (Fallback Heuristics)
        return self._fallback_diagnosis(slow_records[:25])

    def _fallback_diagnosis(self, slow_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """當 LLM 逾時或離線時之智慧備援診斷器（絕不給假大空建議）"""
        items = []
        bottlenecks = 0
        legit = 0

        for i, item in enumerate(slow_records, 1):
            q = item.get("query", "")
            lat = item.get("latency", 0.0)
            ttft = item.get("ttft", 0.0)

            # 判斷是否為明確的系統瓶頸
            if any(w in q for w in ["買了", "紀錄我要買", "待買", "清單", "備品"]) and ("、" in q or "以及" in q or "還有" in q):
                bottlenecks += 1
                items.append({
                    "index": i,
                    "type": "SYSTEM_BOTTLENECK",
                    "category": "待買/備品清單缺乏批量寫入",
                    "severity": "高",
                    "root_cause": "單次語音交代多個待買/已買品項，大腦逐筆執行試算表查詢與修改，引發多次 Tool Call 與網路往返累積延遲。",
                    "proposal": "在 .agents/skills/shopping-list/scripts/shopping_manager.py 實作批次更新 API (batch_update)，單次指令合併寫入雙表。",
                    "estimated_saving": f"耗時預估從 {lat:.1f}s 降至 8s (節省約 {max(0, lat - 8):.0f} 秒)"
                })
            elif any(w in q for w in ["現在位置", "所在位置", "我現在在哪"]):
                bottlenecks += 1
                items.append({
                    "index": i,
                    "type": "SYSTEM_BOTTLENECK",
                    "category": "外部服務呼叫過頻",
                    "severity": "中",
                    "root_cause": "使用者單次輸入多個需求，系統未進行批量合併處理，造成多輪網路來回。",
                    "proposal": "在相應技能實作批次合併處理 (Batch Pipeline)。",
                    "estimated_saving": f"耗時預估從 {lat:.1f}s 降至 5s (節省約 {max(0, lat - 5):.0f} 秒)"
                })
            else:
                legit += 1
                items.append({
                    "index": i,
                    "type": "LEGITIMATE_DEEP_INQUIRY",
                    "category": "良性深研 (外部聯網/深度分析)",
                    "severity": "正常 (無需干預)",
                    "root_cause": f"問題涉及外部即時資訊搜尋、景點攻略或深度推理（TTFT: {ttft:.1f}s / 總計: {lat:.1f}s），耗時符合多源分析與結構化整理預期。",
                    "proposal": "屬於正常深度調研產出，目前無需工程干預；保持現狀即可。",
                    "estimated_saving": "無 (符合預期)"
                })

        return {
            "summary": {
                "total_slow_queries": len(slow_records),
                "legitimate_inquiry_count": legit,
                "system_bottleneck_count": bottlenecks
            },
            "global_proposals": [
                {
                    "title": "待買清單與備品雙表批次原子寫入 (Batch Pipeline)",
                    "affected_count": max(1, bottlenecks),
                    "target_files": [".agents/skills/shopping-list/scripts/shopping_manager.py"],
                    "action_plan": "在 shopping_manager.py 增加批次解析與 Google Sheets batchUpdate，一次同步多品項。",
                    "expected_impact": "多品項登錄耗時節省 60~80 秒。"
                }
            ],
            "items": items
        }

    def generate_html_report(
        self, 
        diagnosis: Dict[str, Any], 
        slow_records: List[Dict[str, Any]], 
        hours: int = 24,
        date_label: Optional[str] = None
    ) -> str:
        """生成優雅現代、支援篩選過濾的 HTML 效能審查與改進提案報告"""
        date_str = date_label or datetime.now().strftime("%Y-%m-%d")
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"perf_review_{timestamp_str}.html"
        file_path = self.reports_dir / filename

        summary = diagnosis.get("summary", {})
        global_proposals = diagnosis.get("global_proposals", [])
        diag_items = {it.get("index"): it for it in diagnosis.get("items", [])}

        total_cnt = len(slow_records)
        legit_cnt = summary.get("legitimate_inquiry_count", 0)
        bottleneck_cnt = summary.get("system_bottleneck_count", 0)
        max_lat = max([it.get("latency", 0) for it in slow_records], default=0.0)
        avg_lat = (sum([it.get("latency", 0) for it in slow_records]) / total_cnt) if total_cnt else 0.0

        # 構建 Top 系統優化提案卡片區塊
        global_proposals_html = ""
        if global_proposals:
            for p in global_proposals:
                files_html = " ".join([f"<code>{f}</code>" for f in p.get("target_files", [])])
                global_proposals_html += f"""
                <div class="proposal-card">
                  <div class="p-head">
                    <span class="p-badge">🚨 核心工程改進</span>
                    <h3 class="p-title">{p.get('title', '')}</h3>
                    <span class="p-affected">影響 {p.get('affected_count', 1)} 筆查詢</span>
                  </div>
                  <div class="p-body">
                    <div class="p-row">
                      <span class="p-label">🎯 目標檔案：</span>
                      <span class="p-val">{files_html}</span>
                    </div>
                    <div class="p-row">
                      <span class="p-label">🛠️ 實施方案：</span>
                      <span class="p-val">{p.get('action_plan', '')}</span>
                    </div>
                    <div class="p-row">
                      <span class="p-label">📈 預期效益：</span>
                      <span class="p-val text-success"><strong>{p.get('expected_impact', '')}</strong></span>
                    </div>
                  </div>
                </div>
                """
        else:
            global_proposals_html = """
            <div class="no-proposals">
              ✅ 今日所有長耗時查詢皆屬於合理良性深度調研，系統無明顯架構性阻塞瓶頸！
            </div>
            """

        # 構建個別問答卡片清單
        items_html = ""
        for i, item in enumerate(slow_records, 1):
            diag = diag_items.get(i, {})
            is_bottleneck = (diag.get("type") == "SYSTEM_BOTTLENECK")
            card_type = "bottleneck" if is_bottleneck else "legitimate"

            q = item.get("query", "").replace("<", "&lt;").replace(">", "&gt;")
            r = item.get("reply", "").replace("<", "&lt;").replace(">", "&gt;")
            r_snippet = r[:200] + ("..." if len(r) > 200 else "")
            lat = item.get("latency", 0.0)
            ttft = item.get("ttft", 0.0)
            ts = item.get("timestamp", "").replace("T", " ")[:19]

            badge_style = "border-color: #f87171; color: #f87171; background: rgba(248,113,113,0.1);" if is_bottleneck else "border-color: #34d399; color: #34d399; background: rgba(52,211,153,0.1);"
            badge_text = "🚨 系統瓶頸" if is_bottleneck else "🔍 良性深研"

            items_html += f"""
            <div class="review-card" data-type="{card_type}">
              <div class="card-head">
                <div class="card-num">#{i}</div>
                <div class="card-title-group">
                  <div class="card-title">{diag.get('category', '未知')}</div>
                  <div class="card-meta">
                    <span>⏱️ 總耗時: <strong>{lat:.1f}s</strong></span>
                    <span>⚡ TTFT: {ttft:.1f}s</span>
                    <span>🕒 {ts}</span>
                  </div>
                </div>
                <span class="type-badge" style="{badge_style}">
                  {badge_text}
                </span>
              </div>

              <div class="dialogue-box">
                <div class="dialogue-line"><strong>👤 主人提問:</strong> {q}</div>
                <div class="dialogue-line text-muted"><strong>🤖 助理回覆:</strong> {r_snippet}</div>
              </div>

              <div class="diag-section">
                <div class="diag-title">🔍 根本原因分析</div>
                <div class="diag-desc">{diag.get('root_cause', '')}</div>
              </div>

              <div class="diag-section {'proposal-box' if is_bottleneck else 'legit-box'}">
                <div class="diag-title">{'💡 具體工程提案 (由主人裁決)' if is_bottleneck else '📌 結論與建議'}</div>
                <div class="diag-desc">{diag.get('proposal', '')}</div>
                <div class="saving-badge">🎯 預期效益: {diag.get('estimated_saving', '')}</div>
              </div>
            </div>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
  <title>助理效能審查與改進提案報告 ({date_str})</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: #111827;
      --card-border: #1f293d;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --success: #34d399;
      --warning: #fbbf24;
      --danger: #f87171;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", sans-serif;
      line-height: 1.6;
      padding: 1.5rem 1rem 3rem;
    }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    header {{
      margin-bottom: 2rem;
      padding-bottom: 1.25rem;
      border-bottom: 1px solid var(--card-border);
    }}
    .badge {{
      display: inline-block;
      padding: 0.25rem 0.65rem;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
      background: rgba(56, 189, 248, 0.1);
      color: var(--accent);
      border: 1px solid rgba(56, 189, 248, 0.25);
      margin-bottom: 0.5rem;
    }}
    h1 {{ font-size: 1.8rem; font-weight: 800; color: #fff; margin-bottom: 0.4rem; }}
    .subtitle {{ color: var(--text-muted); font-size: 0.95rem; }}
    
    .stats-bar {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }}
    .stat-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 0.75rem;
      padding: 1rem 1.25rem;
    }}
    .stat-val {{ font-size: 1.5rem; font-weight: 800; color: #fff; }}
    .stat-lbl {{ font-size: 0.82rem; color: var(--text-muted); }}

    .section-title {{
      font-size: 1.25rem;
      font-weight: 800;
      color: #fff;
      margin-bottom: 1rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}

    .proposal-card {{
      background: rgba(30, 41, 59, 0.7);
      border: 1px solid rgba(56, 189, 248, 0.3);
      border-radius: 1rem;
      padding: 1.25rem 1.5rem;
      margin-bottom: 1.25rem;
    }}
    .p-head {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 0.75rem;
      flex-wrap: wrap;
    }}
    .p-badge {{
      background: rgba(248, 113, 113, 0.15);
      color: var(--danger);
      border: 1px solid var(--danger);
      font-size: 0.75rem;
      padding: 0.2rem 0.5rem;
      border-radius: 0.375rem;
      font-weight: 700;
    }}
    .p-title {{ font-size: 1.15rem; color: #fff; font-weight: 700; flex: 1; }}
    .p-affected {{ font-size: 0.8rem; color: var(--text-muted); background: #0f172a; padding: 0.2rem 0.5rem; border-radius: 0.3rem; }}
    .p-body {{ font-size: 0.9rem; line-height: 1.6; }}
    .p-row {{ margin-bottom: 0.4rem; }}
    .p-label {{ color: #94a3b8; font-weight: 600; }}
    .p-val code {{
      background: #0f172a;
      padding: 0.15rem 0.4rem;
      border-radius: 0.3rem;
      color: #38bdf8;
      font-size: 0.85rem;
      margin-right: 0.3rem;
    }}
    .text-success {{ color: var(--success); }}

    .filter-bar {{
      display: flex;
      gap: 0.5rem;
      margin: 2rem 0 1.25rem;
      background: #0f172a;
      padding: 0.35rem;
      border-radius: 0.6rem;
      border: 1px solid var(--card-border);
      width: fit-content;
    }}
    .filter-btn {{
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 0.4rem 0.85rem;
      border-radius: 0.4rem;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }}
    .filter-btn.active {{
      background: var(--card-bg);
      color: #fff;
      box-shadow: 0 1px 3px rgba(0,0,0,0.3);
    }}

    .review-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 1rem;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
    }}
    .card-head {{
      display: flex;
      align-items: flex-start;
      gap: 0.75rem;
      margin-bottom: 1rem;
    }}
    .card-num {{
      font-size: 1.25rem;
      font-weight: 900;
      color: var(--accent);
      padding: 0.2rem 0.5rem;
      background: rgba(56, 189, 248, 0.1);
      border-radius: 0.5rem;
    }}
    .card-title-group {{ flex: 1; }}
    .card-title {{ font-size: 1.15rem; font-weight: 700; color: #fff; }}
    .card-meta {{
      display: flex;
      gap: 1rem;
      font-size: 0.82rem;
      color: var(--text-muted);
      margin-top: 0.25rem;
    }}
    .type-badge {{
      padding: 0.2rem 0.6rem;
      border-radius: 0.4rem;
      border: 1px solid;
      font-size: 0.78rem;
      font-weight: 700;
      white-space: nowrap;
    }}
    
    .dialogue-box {{
      background: #0d131f;
      border: 1px solid #1e293b;
      border-radius: 0.6rem;
      padding: 0.85rem 1rem;
      margin-bottom: 1rem;
      font-size: 0.88rem;
    }}
    .dialogue-line {{ margin-bottom: 0.35rem; }}
    .text-muted {{ color: var(--text-muted); }}

    .diag-section {{ margin-bottom: 0.85rem; font-size: 0.9rem; }}
    .diag-title {{ font-weight: 700; color: #cbd5e1; margin-bottom: 0.25rem; }}
    .diag-desc {{ color: var(--text-muted); line-height: 1.5; }}

    .proposal-box {{
      background: rgba(248, 113, 113, 0.05);
      border: 1px solid rgba(248, 113, 113, 0.25);
      border-radius: 0.6rem;
      padding: 0.85rem 1rem;
      margin-top: 0.75rem;
    }}
    .proposal-box .diag-title {{ color: #f87171; }}
    .proposal-box .diag-desc {{ color: #f1f5f9; }}

    .legit-box {{
      background: rgba(52, 211, 153, 0.05);
      border: 1px solid rgba(52, 211, 153, 0.2);
      border-radius: 0.6rem;
      padding: 0.85rem 1rem;
      margin-top: 0.75rem;
    }}
    .legit-box .diag-title {{ color: #34d399; }}
    .legit-box .diag-desc {{ color: #cbd5e1; }}

    .saving-badge {{
      display: inline-block;
      margin-top: 0.5rem;
      font-size: 0.82rem;
      font-weight: 600;
      color: var(--success);
    }}

    .portal-link {{
      text-align: center;
      margin-top: 2rem;
      padding: 1rem;
      background: #1e293b;
      border-radius: 0.75rem;
      font-size: 0.9rem;
    }}
    .portal-link a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <span class="badge">Performance & Quality Audit (LLM Powered)</span>
      <h1>📊 助理長耗時問答審查報告</h1>
      <p class="subtitle">深度根因剖析、嚴禁假大空套話，區分良性深研與系統優化機會 ({date_str})</p>
    </header>

    <div class="stats-bar">
      <div class="stat-card">
        <div class="stat-val">{total_cnt}</div>
        <div class="stat-lbl">偏長問答總量 (&gt;15s)</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" style="color: #f87171;">{bottleneck_cnt}</div>
        <div class="stat-lbl">🚨 系統瓶頸 (待優化)</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" style="color: #34d399;">{legit_cnt}</div>
        <div class="stat-lbl">🔍 良性深研 (正常)</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{max_lat:.1f}s</div>
        <div class="stat-lbl">最長單次延遲</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{avg_lat:.1f}s</div>
        <div class="stat-lbl">慢問答平均耗時</div>
      </div>
    </div>

    <div class="section-title">🚀 系統核心優化提案 (Actionable Engineering Proposals)</div>
    {global_proposals_html}

    <div class="filter-bar">
      <button class="filter-btn active" onclick="filterCards('all', this)">全部問答 ({total_cnt})</button>
      <button class="filter-btn" onclick="filterCards('bottleneck', this)">🚨 系統瓶頸 ({bottleneck_cnt})</button>
      <button class="filter-btn" onclick="filterCards('legitimate', this)">🔍 良性深研 ({legit_cnt})</button>
    </div>

    <div id="cards-container">
      {items_html}
    </div>

    <div class="portal-link">
      📂 查看所有歷史報告：<a href="/reports">Mac mini 報告總覽中心 ➔</a>
    </div>
  </div>

  <script>
    function filterCards(type, btn) {{
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const cards = document.querySelectorAll('.review-card');
      cards.forEach(card => {{
        if (type === 'all' || card.getAttribute('data-type') === type) {{
          card.style.display = 'block';
        }} else {{
          card.style.display = 'none';
        }}
      }});
    }}
  </script>
</body>
</html>
"""
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return filename

    def run_review(
        self, 
        hours: int = 24, 
        min_latency: float = 15.0, 
        notify_telegram: bool = True,
        date_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """執行審查分析、呼叫 LLM 診斷、生成報告並推播 Telegram 通知"""
        slow_queries = self.get_slow_queries(hours=hours, min_latency=min_latency, date_filter=date_filter)

        if not slow_queries:
            date_desc = f"（日期: {date_filter}）" if date_filter else f"過去 {hours} 小時內"
            msg = f"{date_desc} 所有問答皆維持在 {min_latency} 秒以內，系統體感極佳，無偏長紀錄！"
            print(f"[PerformanceReviewer] ℹ️ {msg}")
            return {"status": "clean", "count": 0, "message": msg}

        # 呼叫 LLM 進行深入診斷
        diagnosis = self.diagnose_with_llm(slow_queries)

        # 生成 HTML 報告
        report_filename = self.generate_html_report(
            diagnosis, 
            slow_queries, 
            hours=hours, 
            date_label=date_filter or datetime.now().strftime("%Y-%m-%d")
        )
        report_url = settings.get_report_url(report_filename)
        portal_url = f"http://{settings.TAILSCALE_DOMAIN}:{settings.PORT}/reports"

        print(f"[PerformanceReviewer] 📊 已生成長耗時審查報告：{report_filename} (共 {len(slow_queries)} 筆)")

        # 構建 Telegram 推播摘要
        summary = diagnosis.get("summary", {})
        bottleneck_cnt = summary.get("system_bottleneck_count", 0)
        legit_cnt = summary.get("legitimate_inquiry_count", 0)
        global_proposals = diagnosis.get("global_proposals", [])

        proposals_tg_text = ""
        if global_proposals:
            proposals_tg_text += "\n🛠️ <b>【Top 核心系統優化提案】</b>：\n"
            for i, p in enumerate(global_proposals[:3], 1):
                files_short = ", ".join([Path(f).name for f in p.get("target_files", [])])
                proposals_tg_text += (
                    f"<b>{i}️⃣ {p.get('title')}</b> (影響 {p.get('affected_count', 1)} 筆)\n"
                    f"• 目標：<code>{files_short}</code>\n"
                    f"• 方案：{p.get('action_plan')[:70]}...\n"
                    f"• 預期效益：{p.get('expected_impact')}\n\n"
                )
        else:
            proposals_tg_text += "\n✅ <b>今日長耗時皆屬良性深研，系統無明顯架構性阻塞瓶頸。</b>\n\n"

        date_tag = f" ({date_filter})" if date_filter else ""
        tg_text = (
            f"📊 <b>【助理效能審查與改進提案{date_tag}】</b>\n"
            f"共審查 <b>{len(slow_queries)} 筆</b> 偏長問答（&gt;15s）：\n"
            f"• 🚨 系統瓶頸待優化：<b>{bottleneck_cnt} 筆</b>\n"
            f"• 🔍 良性深度調研：<b>{legit_cnt} 筆</b> (正常無需干預)\n"
            f"{proposals_tg_text}"
            f"🌐 <b>線上完整分析報告</b>：\n"
            f"👉 <a href=\"{report_url}\">點此查看詳情報告</a> ｜ 📂 <a href=\"{portal_url}\">報告總覽中心</a>\n\n"
            f"💡 <b>由主人決定是否採納</b>：若想執行哪項優化，直接向助理交代即可！"
        )

        if notify_telegram:
            try:
                from telegram_service import send_telegram_sync
                send_telegram_sync(tg_text, parse_mode="HTML")
            except Exception as e:
                print(f"[PerformanceReviewer] ⚠️ Telegram 推播失敗: {e}")

        return {
            "status": "reviewed",
            "count": len(slow_queries),
            "report_file": report_filename,
            "report_url": report_url,
            "diagnosis": diagnosis
        }

performance_reviewer = PerformanceReviewer()

if __name__ == "__main__":
    # 支援: python3 performance_reviewer.py [hours]
    # 支援: python3 performance_reviewer.py --date 2026-10-12
    # 支援: python3 performance_reviewer.py --no-notify
    import argparse
    parser = argparse.ArgumentParser(description="Performance Reviewer CLI")
    parser.add_argument("hours", nargs="?", type=int, default=24, help="審查過去 N 小時 (預設 24)")
    parser.add_argument("--date", type=str, default=None, help="審查指定日期，如 2026-10-12")
    parser.add_argument("--notify", action="store_true", default=False, help="推播至 Telegram")
    args = parser.parse_args()

    res = performance_reviewer.run_review(
        hours=args.hours, 
        min_latency=15.0, 
        notify_telegram=args.notify,
        date_filter=args.date
    )
    print("\n審查執行結果：")
    print(f"狀態: {res.get('status')}")
    print(f"偏長問答筆數: {res.get('count')}")
    print(f"報告網址: {res.get('report_url')}")
