#!/usr/bin/env python3
"""
酷澎 (Coupang Taiwan) 價格追蹤與比價工具 (scripts/coupang_price_tracker.py)

雙軌引擎：
1. 購物車精準直取 (Cart Engine)：
   - 直接連入 https://cart.tw.coupang.com/cart
   - 100% 避開搜尋頁的 Akamai WAF 防火牆（購物車屬會員路徑，綠燈不擋）
   - 同時解析「購物車內商品」與「之前想購買的商品 (Saved for Later)」即時售價
2. 搜尋頁面廣度巡檢 (Search Query Engine)：
   - 針對未加入購物車的品項，透過首頁預熱 + 動態 traceId 執行全網比價
"""

import os
import sys
import json
import time
import re
import argparse
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

import importlib.util
BR_PATH = BASE_DIR / "skills" / "browser-relay" / "scripts" / "browser_relay.py"
if not BR_PATH.exists():
    BR_PATH = BASE_DIR / ".agents" / "skills" / "browser-relay" / "scripts" / "browser_relay.py"
spec = importlib.util.spec_from_file_location("browser_relay", BR_PATH)
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)

DATA_DIR = BASE_DIR / "data"
HISTORY_FILE = DATA_DIR / "coupang_price_history.json"


def send_telegram_alert(message: str) -> bool:
    """發送 Telegram 即時通知至主人手機與手錶"""
    bot_token = os.getenv("COUPANG_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("COUPANG_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("⚠️ 未配置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，略過推播", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"❌ Telegram 發送失敗: {e}", file=sys.stderr)
        return False


class CoupangPriceTracker:
    def __init__(self):
        self.client = br.ChromeAssistantClient()
        self.last_login_valid: bool = True
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def fetch_cart_items(self) -> List[Dict[str, Any]]:
        """向後相容介面：取得購物車商品清單"""
        items, _ = self.fetch_cart_items_with_status()
        return items

    def fetch_cart_items_with_status(self) -> Tuple[List[Dict[str, Any]], bool]:
        """
        軌道一：從已登入的購物車取得即時商品與價格，並嚴謹回傳登入驗證狀態
        - 包含購物車中商品
        - 嚴謹區分「已登入但車內為空」與「Session 過期/未登入」
        - 回傳: (parsed_items, is_logged_in)
        """
        self.client.ensure_chrome()
        cart_url = "https://cart.tw.coupang.com/cart"
        is_logged_in = True

        with self.client.session_lock("coupang_tracker:cart", timeout=30.0):
            with br.sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.client.cdp_url)
                context = browser.contexts[0]

                target_page = None
                for page in context.pages:
                    if "cart.tw.coupang.com" in page.url or "cart" in page.url:
                        target_page = page
                        break
                if not target_page:
                    target_page = context.new_page()

                target_page.goto(cart_url, timeout=30000, wait_until="domcontentloaded")
                target_page.wait_for_timeout(2000)

                # 💡 自動勾選「全選」核取方塊以觸發酷澎折扣與折價券計算
                target_page.evaluate("""() => {
                    const cbs = Array.from(document.querySelectorAll("input[type=checkbox]"));
                    const selectAll = cbs.find(cb => {
                        const p = cb.closest("label") || cb.parentElement;
                        return p && p.innerText.includes("全選");
                    }) || cbs[cbs.length - 1];
                    if (selectAll && !selectAll.checked) {
                        selectAll.click();
                    }
                    for (const cb of cbs) {
                        if (!cb.checked) cb.click();
                    }
                }""")
                target_page.wait_for_timeout(2500)

                # 讀取結構化 DOM (第一優先，精準抗干擾) 與純文字 (備援)
                dom_items = target_page.evaluate("""() => {
                    const items = [];
                    const itemEls = document.querySelectorAll("div[id^='item_']");
                    for (const el of itemEls) {
                        let title = "";
                        let option = "";

                        const aEl = el.querySelector("a[href*='/products/']");
                        if (aEl) {
                            const spans = Array.from(aEl.querySelectorAll("span"));
                            if (spans.length > 0) {
                                title = spans[0].innerText.trim();
                                for (let k = 1; k < spans.length; k++) {
                                    const st = spans[k].innerText.trim();
                                    if (st.startsWith("選項:")) {
                                        option = st.replace("選項:", "").trim();
                                        break;
                                    }
                                }
                            } else {
                                title = aEl.innerText.trim();
                            }
                        }

                        if (!title) {
                            const lines = el.innerText.split("\\n").map(s => s.trim()).filter(Boolean);
                            title = lines[0] || "";
                        }

                        if (!option) {
                            const optEl = Array.from(el.querySelectorAll("div, span, p")).find(n => n.innerText && n.innerText.startsWith("選項:"));
                            if (optEl) {
                                option = optEl.innerText.replace("選項:", "").trim();
                            }
                        }

                        if (option && title.includes(option)) {
                            title = title.replace(`選項: ${option}`, "").replace(`選項:${option}`, "").replace(option, "").trim();
                        }

                        let originalPrice = null;
                        let salePrice = null;
                        const priceArea = el.querySelector("[data-component-id='price-area']");
                        if (priceArea) {
                            const lineThroughEl = priceArea.querySelector("[class*='line-through']");
                            if (lineThroughEl) {
                                const num = parseInt(lineThroughEl.innerText.replace(/[^0-9]/g, ""), 10);
                                if (!isNaN(num) && num > 10) originalPrice = num;
                            }

                            // 實付售價：抓取主金額，排除「已套用$xx優惠券」、「滿$xx再省$xx」等折價標籤
                            const boldSpans = priceArea.querySelectorAll("span[class*='font-bold'], span[class*='text-[20px]']");
                            for (const s of boldSpans) {
                                const parentText = s.parentElement ? s.parentElement.innerText : "";
                                if (parentText.includes("已套用") || parentText.includes("再省") || parentText.includes("滿")) {
                                    continue;
                                }
                                if (s.closest("[class*='line-through']")) {
                                    continue;
                                }
                                const num = parseInt(s.innerText.replace(/[^0-9]/g, ""), 10);
                                if (!isNaN(num) && num > 10) {
                                    salePrice = num;
                                    break;
                                }
                            }

                            if (!salePrice) {
                                const priceBlocks = priceArea.querySelectorAll("div[translate='no']");
                                for (const pb of priceBlocks) {
                                    if (pb.querySelector("[class*='line-through']")) continue;
                                    const num = parseInt(pb.innerText.replace(/[^0-9]/g, ""), 10);
                                    if (!isNaN(num) && num > 10) {
                                        salePrice = num;
                                        break;
                                    }
                                }
                            }
                        }

                        if (salePrice) {
                            items.push({
                                source: "cart",
                                title: title,
                                option: option,
                                original_price: originalPrice || salePrice,
                                sale_price: salePrice,
                                url: aEl ? aEl.href : "https://cart.tw.coupang.com/cart"
                            });
                        }
                    }
                    return items;
                }""")

                body_text = target_page.evaluate("document.body.innerText")

                auth_check = target_page.evaluate("""() => {
                    const title = document.title || "";
                    const url = window.location.href || "";
                    if (title.includes("Access Denied")) return { logged_in: false, reason: "Access Denied" };
                    if (url.includes("login") || url.includes("member.tw.coupang.com")) return { logged_in: false, reason: "Login page redirected" };

                    const elements = Array.from(document.querySelectorAll("a, button, span, div"));
                    const hasLogin = elements.some(el => (el.innerText || "").trim() === "登入");
                    const hasUser = elements.some(el => {
                        const t = (el.innerText || "").trim();
                        return t.includes("我的酷澎") || t.includes("登出") || t.includes("會員專區") || t.includes("訂單查詢");
                    });
                    if (hasLogin && !hasUser) {
                        return { logged_in: false, reason: "Login button detected without active user session" };
                    }
                    return { logged_in: true, reason: "OK" };
                }""")
                is_logged_in = auth_check.get("logged_in", True)
                self.last_login_valid = is_logged_in

        parsed_cart = []
        if dom_items:
            for item in dom_items:
                if not any(it["title"] == item["title"] and it.get("option", "") == item.get("option", "") for it in parsed_cart):
                    parsed_cart.append(item)

        # 備援容錯：若 DOM 提取完全為空，才走降級純文字切塊解析
        if not parsed_cart and body_text:
            cart_main = body_text.split("預計訂單金額")[0] if "預計訂單金額" in body_text else body_text
            lines = [l.strip() for l in cart_main.split("\n") if l.strip()]

            for i, line in enumerate(lines):
                if line.startswith("選項:"):
                    continue

                is_item_title = False
                option = ""
                if i + 1 < len(lines) and lines[i+1].startswith("選項:"):
                    is_item_title = True
                    option = lines[i+1].replace("選項:", "").strip()
                elif i + 1 < len(lines) and lines[i+1] == "刪除" and len(line) > 3 and not any(tag in line for tag in ["全選", "酷澎", "購物車", "免費送貨", "火箭速配", "選項:"]):
                    is_item_title = True

                if is_item_title:
                    title = line
                    prices = []
                    # 在商品區域內尋找所有價格 (嚴格排除「比加入購物車時」、「再省」、「已套用」等折價標籤)
                    for j in range(i + 1, min(len(lines), i + 25)):
                        if j > i + 2 and (lines[j].startswith("選項:") or (j + 1 < len(lines) and lines[j+1].startswith("選項:"))):
                            break
                        if lines[j] in ["添加火箭速配商品", "全選", "刪除選擇"]:
                            break

                        # 排除干擾行：降價差額、滿額折扣、優惠券
                        prev_line = lines[j-1] if j > 0 else ""
                        cur_line = lines[j]
                        if any(kw in prev_line for kw in ["比加入購物車時", "加入購物車後", "再省", "滿", "已套用"]):
                            continue
                        if any(kw in cur_line for kw in ["已套用", "再省", "滿", "比加入購物車"]):
                            continue

                        if cur_line == "$" and j + 1 < len(lines):
                            val_str = lines[j+1].replace(",", "")
                            if val_str.isdigit() and int(val_str) > 10:
                                prices.append(int(val_str))
                        elif cur_line.startswith("$") and not cur_line.startswith("($"):
                            val_str = cur_line.replace("$", "").replace(",", "")
                            if val_str.isdigit() and int(val_str) > 10:
                                prices.append(int(val_str))

                    if prices:
                        discounted_price = min(prices)
                        original_price = max(prices)
                        if not any(it["title"] == title and it.get("option", "") == option for it in parsed_cart):
                            parsed_cart.append({
                                "source": "cart",
                                "title": title,
                                "option": option,
                                "original_price": original_price,
                                "sale_price": discounted_price,
                                "url": "https://cart.tw.coupang.com/cart"
                            })

        # 2. 保留清單（你之前想購買的商品）已依指示停用追蹤，僅專注於購物車主區
        # （若未來需要啟用可開啟）

        if not is_logged_in:
            print("⚠️ [酷澎購物車] 偵測到登入已過期或處於訪客模式，無法同步個人購物車商品。", file=sys.stderr)

        # 巡檢完成後，精準關閉購物車分頁，釋放記憶體與視窗（與 FB 巡檢保持一致）
        try:
            self.client.cleanup_domain_targets(("cart.tw.coupang.com", "coupang.com"))
            self.client.gc_tabs(max_tabs=8)
        except Exception as e:
            print(f"⚠️ [酷澎購物車] 關閉分頁警告: {e}", file=sys.stderr)

        return parsed_cart, is_logged_in

    def search_products(self, query: str, wait_seconds: float = 3.0) -> List[Dict[str, Any]]:
        """軌道二：透過 Chrome-Assistant 開啟搜尋頁並抓取解析商品列表"""
        self.client.ensure_chrome()

        trace_id = hex(int(time.time() * 1000))[2:]
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.tw.coupang.com/search?component=&q={encoded_query}&traceId={trace_id}&channel=user"

        with self.client.session_lock("coupang_tracker:search", timeout=30.0):
            with br.sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.client.cdp_url)
                context = browser.contexts[0]

                target_page = None
                for page in context.pages:
                    if "coupang.com" in page.url and "cart" not in page.url:
                        target_page = page
                        break
                if not target_page:
                    target_page = context.new_page()

                # 首頁預熱流程
                try:
                    target_page.goto("https://www.tw.coupang.com/", timeout=15000, wait_until="domcontentloaded")
                    target_page.wait_for_timeout(1000)
                except Exception:
                    pass

                target_page.goto(
                    search_url,
                    timeout=30000,
                    wait_until="domcontentloaded",
                    referer="https://www.tw.coupang.com/"
                )
                target_page.wait_for_timeout(int(wait_seconds * 1000))

                self.client.check_and_bypass_captcha(target_page)

                raw_items = target_page.evaluate("""() => {
                    const links = Array.from(document.querySelectorAll('a[href*="products"]'));
                    return links.map(a => {
                        const rawText = a.innerText.replace(/\\s+/g, ' ').trim();
                        return {
                            raw_text: rawText,
                            href: a.href
                        };
                    });
                }""")

        return self._parse_items(raw_items, query)

    def _parse_items(self, raw_items: List[Dict[str, str]], query: str) -> List[Dict[str, Any]]:
        parsed = []
        seen_keys = set()

        for item in raw_items:
            text = item.get("raw_text", "")
            href = item.get("href", "")
            if not text or not href:
                continue

            # 產品關鍵字比對
            if "iphone" in query.lower():
                if "iphone" not in text.lower():
                    continue
            else:
                keywords = query.split()
                if not all(k.lower() in text.lower() for k in keywords if len(k) > 1):
                    continue

            # 擷取商品型號
            if re.search(r"iPhone-?17e", href, re.IGNORECASE) or re.search(r"\b17e\b", text, re.IGNORECASE) or "17e" in text.lower():
                model_name = "Apple iPhone 17e"
            elif re.search(r"iPhone-?16e", href, re.IGNORECASE) or re.search(r"\b16e\b", text, re.IGNORECASE) or "16e" in text.lower():
                model_name = "Apple iPhone 16e"
            elif re.search(r"iPhone-?Air", href, re.IGNORECASE) or "iphone air" in text.lower():
                model_name = "Apple iPhone Air"
            else:
                model_match = re.search(r"(Apple\s+iPhone\s+(?:1[78]\s+Pro(?:\s+Max)?|17e|16e|1[78]))(?=[^a-zA-Z0-9]|$)", text, re.IGNORECASE)
                model_name = model_match.group(1) if model_match else query

            # 擷取顏色
            color_match = re.search(r"(冰川藍色|勃根地紅色|銀色|黑色|沙漠色|原色鈦金屬|白色鈦金屬|黑色鈦金屬|宇宙橙色|藏藍色|白色|黑色|藍色|綠色|粉色|黃色)", text)
            color = color_match.group(1) if color_match else "標準款"

            # 擷取容量
            cap_match = re.search(r"(\d+\s*(?:GB|TB|kg|g))", text, re.IGNORECASE)
            capacity = cap_match.group(1).upper().replace(" ", "") if cap_match else "未標示"

            # 擷取價格資訊 (過濾掉每公克單價)
            # 先拿掉 (100g 54元) 或 ($74.2/100g) 這類干擾
            clean_text = re.sub(r"\([^)]*100g[^)]*\)", "", text)
            all_prices_str = re.findall(r"\$([0-9,]+)", clean_text)
            all_prices = [int(p.replace(",", "")) for p in all_prices_str if int(p.replace(",", "")) > 100]

            coin_match = re.search(r"\$([0-9,]+)\s*酷澎幣", text)
            coin_reward = int(coin_match.group(1).replace(",", "")) if coin_match else 0

            item_prices = [p for p in all_prices if p != coin_reward] if coin_reward else all_prices

            original_price = 0
            sale_price = 0
            if len(item_prices) >= 2:
                original_price = max(item_prices)
                sale_price = min(item_prices)
            elif len(item_prices) == 1:
                original_price = item_prices[0]
                sale_price = item_prices[0]

            if sale_price == 0:
                continue

            disc_match = re.search(r"(\d+)%", text)
            discount_percent = int(disc_match.group(1)) if disc_match else 0
            if discount_percent == 0 and original_price > sale_price:
                discount_percent = round((1.0 - (sale_price / original_price)) * 100)

            is_low_stock = "即將售完" in text or "要買要快" in text
            clean_url = href.split("&searchId=")[0].split("?searchId=")[0]

            dedup_key = f"{model_name}_{color}_{capacity}_{sale_price}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            parsed.append({
                "model": model_name,
                "color": color,
                "capacity": capacity,
                "original_price": original_price,
                "sale_price": sale_price,
                "discount_percent": discount_percent,
                "coin_reward": coin_reward,
                "is_low_stock": is_low_stock,
                "url": clean_url,
                "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S")
            })

        return parsed


def main():
    tracker = CoupangPriceTracker()
    print("🛒 測試抓取購物車品項...")
    cart_items = tracker.fetch_cart_items()
    for ci in cart_items:
        print(f"  • [{ci['source']}] {ci['title']} ({ci['option']}) -> 現價: ${ci['sale_price']}")


if __name__ == "__main__":
    main()
