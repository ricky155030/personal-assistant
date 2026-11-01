#!/usr/bin/env python3
"""
Antigravity (agy) 專屬真實 Chrome 瀏覽器自動化客戶端 (Chrome-Assistant Engine)
直接操控 Mac mini 本機真實 Google Chrome 應用程式，使用獨立持久化 Profile (Chrome-Assistant)。

特色：
1. 零外掛依賴：不依賴任何 Chrome Extension，免受 Service Worker 休眠或協定版本失聯干擾。
2. 零外部伺服器：不需要常駐外部轉發服務，Python 原生 CDP 直連。
3. 永久保留 Profile：Cookies、Sessions、登入狀態永久儲存於專屬目錄，重啟不丟失。
4. 天然抗防爬蟲盾：真實 Chrome 核心 + 抹除 AutomationControlled 特徵 + 自動長按通關常見驗證盾 (PerimeterX, Cloudflare)。
5. 與日常 Chrome 互不衝突：專屬 user-data-dir，主人日常上網與助理背景自動化完全並行不打架。
6. 通用高階 AI 介面：
   - 元素語意快照 (`inspect`)：一鍵看清所有可點擊與輸入欄位，杜絕盲寫 JavaScript 試探。
   - 智慧多策略定位 (`smart locator`)：直接以文字、Label 或 Placeholder 點擊與填寫，免猜動態 class。
   - 連鎖動作批次管線 (`batch`)：一次傳入多步驟流水線，大幅減少 LLM 往返思考等待時間。
"""

import sys
import os
import json
import time
import fcntl
import argparse
import subprocess
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any, Union, Tuple

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.venv_guard import ensure_venv
ensure_venv()

try:
    from playwright.sync_api import sync_playwright, Page, BrowserContext, Locator
except ImportError:
    print("請使用虛擬環境中的 Python 執行：.venv/bin/python", file=sys.stderr)
    sys.exit(1)

CHROME_BIN = os.getenv("CHROME_PATH", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PROFILE_DIR = os.getenv(
    "CHROME_ASSISTANT_PROFILE",
    str(Path.home() / "Library" / "Application Support" / "Google" / "Chrome-Assistant")
)
CDP_PORT = int(os.getenv("CHROME_ASSISTANT_PORT", "9223"))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
CDP_LOCK_FILE = Path("/tmp/chrome_assistant_cdp.lock")
CDP_LOCK_META_FILE = Path("/tmp/chrome_assistant_cdp.lock.json")


class ChromeAssistantClient:
    def __init__(self, port: int = CDP_PORT, profile_dir: str = PROFILE_DIR):
        self.port = port
        self.cdp_url = f"http://127.0.0.1:{port}"
        self.profile_dir = profile_dir
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)

    def is_running(self) -> bool:
        """檢查 Chrome-Assistant 是否已在該 port 提供 CDP 服務"""
        try:
            req = urllib.request.Request(f"{self.cdp_url}/json/version")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def ensure_chrome(self, headless: bool = False, timeout: float = 10.0) -> None:
        """確保 Chrome-Assistant 正在運行，若未啟動則自動以真實 Chrome 核心拉起"""
        if self.is_running():
            return

        if not os.path.exists(CHROME_BIN):
            raise FileNotFoundError(f"找不到 Google Chrome 執行檔：{CHROME_BIN}")

        cmd = [
            CHROME_BIN,
            f"--user-data-dir={self.profile_dir}",
            f"--remote-debugging-port={self.port}",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,AutoDeElevate",
            "--window-size=1280,900"
        ]
        if headless:
            cmd.append("--headless=new")

        # 以獨立 session 在後台脫鉤啟動，終端結束時不被隨之銷毀
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        start_t = time.perf_counter()
        while time.perf_counter() - start_t < timeout:
            if self.is_running():
                return
            time.sleep(0.3)

        raise TimeoutError(f"Chrome-Assistant 啟動超時 ({timeout}s)，未能於 {self.cdp_url} 回應。")

    @contextmanager
    def session_lock(self, owner: str = "browser-relay", timeout: float = 45.0):
        """
        [INFRA-02] 跨進程輕量級 CDP 操作互斥鎖 (Cross-Process Mutex)
        防止背景任務 (FB 社團監控、蝦皮上架) 與前景訂位同時爭奪 Chrome 視窗焦點或誤關彼此分頁。
        若持有鎖之進程異常崩潰，作業系統核心會自動釋放 flock，保證零死鎖。
        """
        CDP_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(CDP_LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        start_t = time.perf_counter()
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (IOError, OSError):
                    if time.perf_counter() - start_t >= timeout:
                        # 逾時降級：印出警告但允許繼續，避免徹底卡死前景任務
                        print(f"⚠️ [Chrome-Assistant] 等待 CDP 互斥鎖逾時 ({timeout}s)，強制繼續 ({owner})...", file=sys.stderr)
                        break
                    time.sleep(0.15)

            if acquired:
                try:
                    CDP_LOCK_META_FILE.write_text(
                        json.dumps({
                            "owner": owner,
                            "pid": os.getpid(),
                            "acquired_at": time.strftime("%Y-%m-%d %H:%M:%S")
                        }, ensure_ascii=False),
                        encoding="utf-8"
                    )
                except Exception:
                    pass
            yield
        finally:
            if acquired:
                try:
                    CDP_LOCK_META_FILE.unlink(missing_ok=True)
                except Exception:
                    pass
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except Exception:
                    pass
            try:
                os.close(fd)
            except Exception:
                pass

    def create_persistent_tab(self, url: str = "about:blank") -> Optional[str]:
        """透過 Chrome 原生 HTTP CDP 端點建立持久化分頁（不會隨 Playwright 連線結束而銷毀），回傳 CDP Target ID"""
        encoded = urllib.parse.quote(url, safe=":/?&=#")
        req = urllib.request.Request(f"{self.cdp_url}/json/new?{encoded}", method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("id")
        except Exception:
            try:
                with urllib.request.urlopen(f"{self.cdp_url}/json/new?{encoded}", timeout=5.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("id")
            except Exception:
                return None

    def cleanup_domain_targets(self, domain_keywords: Tuple[str, ...]) -> int:
        """
        [INFRA-02] 專屬域名分頁與背景 Worker 清理器
        在保留至少 1 個空白分頁保活視窗的前提下，精準關閉指定域名之 page / shared_worker / iframe，
        絕不誤關其他技能或主人正在操作之分頁。
        """
        if not self.is_running():
            return 0
        closed_count = 0
        try:
            with urllib.request.urlopen(f"{self.cdp_url}/json/list", timeout=5.0) as r:
                targets = json.loads(r.read().decode("utf-8"))
            has_blank = any(
                t.get("type") == "page" and t.get("url", "") in ("about:blank", "chrome://newtab/")
                for t in targets
            )
            if not has_blank:
                self.create_persistent_tab("about:blank")
                time.sleep(0.15)
            for t in targets:
                tid = t.get("id")
                url = t.get("url", "")
                ttype = t.get("type", "")
                if tid and ttype in ("page", "shared_worker", "iframe") and any(k in url for k in domain_keywords):
                    try:
                        with urllib.request.urlopen(f"{self.cdp_url}/json/close/{tid}", timeout=3.0):
                            closed_count += 1
                    except Exception:
                        pass
        except Exception as e:
            print(f"⚠️ [Chrome-Assistant] 清理域名分頁時發生非致命警告: {e}", file=sys.stderr)
        return closed_count

    def gc_tabs(self, max_tabs: int = 8, keep_domains: Tuple[str, ...] = ()) -> Dict[str, Any]:
        """
        [INFRA-02] 殭屍分頁自動回收機制 (Zombie Tab Garbage Collector)
        1. 自動收斂多餘的 about:blank / chrome://newtab/ 空白頁（最多保留 1 個保活頁）
        2. 當總分頁數超過 max_tabs 時，依序回收最舊且非保護中的殭屍分頁，防止記憶體洩漏
        """
        if not self.is_running():
            return {"ok": True, "closed": 0, "remaining": 0}

        closed_ids = []
        try:
            with urllib.request.urlopen(f"{self.cdp_url}/json/list", timeout=5.0) as r:
                targets = json.loads(r.read().decode("utf-8"))

            pages = [t for t in targets if t.get("type") == "page" and t.get("id")]
            blank_pages = [p for p in pages if p.get("url", "") in ("about:blank", "chrome://newtab/")]
            content_pages = [p for p in pages if p.get("url", "") not in ("about:blank", "chrome://newtab/")]

            # 1. 若完全沒有空白保活頁且要清空所有內容頁時，保留至少 1 個空白頁；若有多個空白頁則只留 1 個
            if len(blank_pages) > 1:
                for bp in blank_pages[1:]:
                    tid = bp["id"]
                    try:
                        with urllib.request.urlopen(f"{self.cdp_url}/json/close/{tid}", timeout=3.0):
                            closed_ids.append(tid)
                    except Exception:
                        pass
                blank_pages = blank_pages[:1]

            # 2. 若內容分頁數超過上限，從最舊的分頁（列表尾端或非活躍端）回收
            remaining_total = len(content_pages) + len(blank_pages)
            if remaining_total > max_tabs:
                excess = remaining_total - max_tabs
                # 排除受保護域名，優先回收舊分頁
                candidates = [
                    p for p in reversed(content_pages)
                    if not (keep_domains and any(k in p.get("url", "") for k in keep_domains))
                ]
                for victim in candidates[:excess]:
                    tid = victim["id"]
                    try:
                        with urllib.request.urlopen(f"{self.cdp_url}/json/close/{tid}", timeout=3.0):
                            closed_ids.append(tid)
                    except Exception:
                        pass

            return {
                "ok": True,
                "closed": len(closed_ids),
                "closed_ids": closed_ids,
                "remaining": max(0, len(pages) - len(closed_ids))
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "closed": len(closed_ids)}

    def get_status(self) -> Dict[str, Any]:
        """取得目前引擎健康狀態與資訊"""
        running = self.is_running()
        version_data = {}
        if running:
            try:
                with urllib.request.urlopen(f"{self.cdp_url}/json/version", timeout=1.5) as resp:
                    version_data = json.loads(resp.read().decode())
            except Exception as e:
                version_data = {"error": str(e)}

        lock_holder = None
        if CDP_LOCK_META_FILE.exists():
            try:
                lock_holder = json.loads(CDP_LOCK_META_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        return {
            "engine": "Chrome-Assistant (Playwright CDP)",
            "running": running,
            "port": self.port,
            "cdp_url": self.cdp_url,
            "profile_dir": self.profile_dir,
            "chrome_bin": CHROME_BIN,
            "lock_holder": lock_holder,
            "version": version_data
        }

    def _pick_page(
        self,
        context: BrowserContext,
        tab_identifier: Optional[str] = None,
        exclude_domains: Tuple[str, ...] = ("facebook.com/groups/", "seller.shopee.tw")
    ) -> Page:
        """
        [INFRA-02] 依據索引、CDP Target ID 或 URL/標題匹配選擇分頁。
        當未指定 tab_identifier 時：
        1. 優先挑選最新開啟的「前景實質內容分頁」（自動跳過保活用的 about:blank 以及背景任務專屬分頁）
        2. 若無其他前景分頁，才回退至非空白分頁或最後一個分頁，徹底防止前景指令誤觸背景保活頁。
        """
        pages = context.pages
        if not pages:
            return context.new_page()

        if tab_identifier is None:
            # 1. 優先尋找非空白且非背景專用域名的最新分頁
            for p in reversed(pages):
                u = (p.url or "").strip()
                if u not in ("about:blank", "chrome://newtab/", "") and not any(d in u for d in exclude_domains):
                    return p
            # 2. 次選任何非空白分頁
            for p in reversed(pages):
                u = (p.url or "").strip()
                if u not in ("about:blank", "chrome://newtab/", ""):
                    return p
            return pages[-1]

        # 1. 嘗試數字索引 (0, 1, 2...)
        if str(tab_identifier).isdigit():
            idx = int(tab_identifier)
            if 0 <= idx < len(pages):
                return pages[idx]

        # 2. 嘗試 URL 或 Title 模糊比對
        target = str(tab_identifier).lower()
        for p in pages:
            try:
                if target in p.url.lower() or target in p.title().lower():
                    return p
            except Exception:
                pass

        # 預設回傳最後一個有效分頁
        return self._pick_page(context, None, exclude_domains=exclude_domains)

    def check_and_bypass_captcha(self, page: Page, max_wait: float = 2.0) -> bool:
        """
        通用防爬蟲驗證盾自動自愈 (Anti-Bot Auto-Bypass)
        自動偵測常見驗證盾（PerimeterX 按住不放、Cloudflare Turnstile），
        若觸發則自動執行物理模擬解鎖，避免上層 AI 卡死。
        """
        try:
            # 1. 偵測 PerimeterX / HUMAN Security (Press & Hold)
            px_frame = page.locator('#px-captcha, iframe[id*="px-captcha"]').first
            is_px = False
            if px_frame.count() > 0 and px_frame.is_visible():
                is_px = True
            else:
                title = page.title()
                if "Access to this page has been denied" in title:
                    is_px = True

            if is_px:
                print("🛡️ [Anti-Bot] 偵測到 PerimeterX 驗證盾 (Press & Hold)，正在自動進行長按解鎖...", file=sys.stderr)
                cx, cy = 640.0, 515.0
                modal_frame = page.frame("px-captcha-modal")
                if modal_frame:
                    try:
                        target = modal_frame.locator("#px-captcha, div[tabindex]").first
                        box = target.bounding_box()
                        if box and box['width'] > 0 and box['height'] > 0:
                            cx = box['x'] + box['width'] / 2
                            cy = box['y'] + box['height'] / 2
                    except Exception:
                        pass
                elif px_frame.count() > 0:
                    box = px_frame.bounding_box()
                    if box and box['width'] > 0 and box['height'] > 0:
                        cx = box['x'] + box['width'] / 2
                        cy = box['y'] + (box['height'] * 0.64)  # button is usually around 64% down

                page.mouse.move(cx, cy, steps=10)
                time.sleep(0.3)
                page.mouse.down()
                for _ in range(12):
                    time.sleep(1)
                page.mouse.up()
                print("🛡️ [Anti-Bot] 滑鼠已釋放，等候頁面重新驗證導航...", file=sys.stderr)
                page.wait_for_timeout(3500)
                return True

            # 2. 偵測 Cloudflare Turnstile
            cf_frame = page.locator('iframe[src*="challenges.cloudflare.com"], .cf-turnstile').first
            if cf_frame.count() > 0 and cf_frame.is_visible():
                print("🛡️ [Anti-Bot] 偵測到 Cloudflare Turnstile 驗證框，嘗試點擊核取...", file=sys.stderr)
                cf_frame.click()
                page.wait_for_timeout(2000)
                return True

        except Exception as e:
            print(f"⚠️ [Anti-Bot] 驗證盾偵測檢查異常 (已略過): {e}", file=sys.stderr)

        return False

    def _smart_locate(
        self,
        page: Page,
        selector: Optional[str] = None,
        text: Optional[str] = None,
        label: Optional[str] = None,
        placeholder: Optional[str] = None,
        role: Optional[str] = None,
        name: Optional[str] = None
    ) -> Locator:
        """
        高階語意多策略定位器 (Smart Locator)
        支援文字、表單 Label、Placeholder、ARIA Role 或標準 CSS/XPath，
        避免依賴易碎且動態變動的 class 名稱。
        """
        # 1. 明確參數優先
        if text:
            return page.get_by_text(text, exact=False).first
        if label:
            return page.get_by_label(label, exact=False).first
        if placeholder:
            return page.get_by_placeholder(placeholder, exact=False).first
        if role:
            if name:
                return page.get_by_role(role, name=name, exact=False).first
            return page.get_by_role(role).first

        if not selector:
            raise ValueError("必須提供 selector, text, label, placeholder 或 role 其一以進行定位。")

        # 2. 語意前綴解析
        s = selector.strip()
        if s.startswith("text="):
            return page.get_by_text(s[5:], exact=False).first
        if s.startswith("label="):
            return page.get_by_label(s[6:], exact=False).first
        if s.startswith("placeholder="):
            return page.get_by_placeholder(s[12:], exact=False).first

        # 3. 嘗試以標準 Playwright Locator 定位
        loc = page.locator(s).first
        if loc.count() > 0:
            return loc

        # 4. 若標準 CSS 找不到，且字串不含 CSS 特殊字元，嘗試回退為純文字比對
        if not any(c in s for c in ["#", ".", "[", "]", ">", ":", "/", "@"]):
            text_loc = page.get_by_text(s, exact=False).first
            if text_loc.count() > 0:
                return text_loc

        return loc

    def open_tab(self, url: str, auto_bypass: bool = True) -> Dict[str, Any]:
        """在 Chrome-Assistant 中開啟新分頁並導航至 URL"""
        self.ensure_chrome()
        with self.session_lock("browser-relay:open", timeout=30.0):
            # 自動清理多餘空白頁與超過上限的殭屍分頁
            self.gc_tabs(max_tabs=10, keep_domains=("facebook.com", "shopee.tw"))
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                page = context.new_page()
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                if auto_bypass:
                    self.check_and_bypass_captcha(page)

                result = {
                    "ok": True,
                    "title": page.title(),
                    "url": page.url,
                    "tab_id": str(len(context.pages) - 1)
                }
                return result

    def get_tabs(self) -> List[Dict[str, Any]]:
        """取得所有當前開啟的分頁"""
        self.ensure_chrome()
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(self.cdp_url)
            context = browser.contexts[0]
            tabs = []
            for idx, page in enumerate(context.pages):
                try:
                    title = page.title()
                except Exception:
                    title = "(載入中...)"
                tabs.append({
                    "target_id": str(idx),
                    "title": title,
                    "url": page.url,
                    "active": (idx == len(context.pages) - 1)
                })
            return tabs

    def read_page(
        self,
        url: Optional[str] = None,
        tab: Optional[str] = None,
        selector: Optional[str] = None,
        auto_bypass: bool = True
    ) -> Dict[str, Any]:
        """讀取頁面結構與乾淨文字，若指定 url 則先導航"""
        self.ensure_chrome()
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(self.cdp_url)
            context = browser.contexts[0]

            if url:
                page = context.new_page()
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
            else:
                page = self._pick_page(context, tab)

            if auto_bypass:
                self.check_and_bypass_captcha(page)

            if selector:
                try:
                    loc = self._smart_locate(page, selector=selector)
                    text = loc.inner_text().strip()
                except Exception as e:
                    text = f"選取器 {selector} 讀取失敗: {e}"
            else:
                text = page.evaluate("""() => {
                    const clone = document.body.cloneNode(true);
                    clone.querySelectorAll("script, style, noscript, svg, iframe, [aria-hidden='true'], [hidden]").forEach(e => e.remove());
                    return clone.innerText.trim();
                }""")

            return {
                "title": page.title(),
                "url": page.url,
                "length": len(text),
                "text": text
            }

    def inspect_page(
        self,
        tab: Optional[str] = None,
        interactive_only: bool = True,
        auto_bypass: bool = True
    ) -> Dict[str, Any]:
        """
        可互動元素語意快照 (Semantic DOM Snapshot)
        一次提取頁面上所有可見的按鈕、表單輸入框、選單與連結，
        並附帶建議的定位指令，讓模型無須盲目撰寫 JS 試探即可直接操作。
        """
        self.ensure_chrome()
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(self.cdp_url)
            context = browser.contexts[0]
            page = self._pick_page(context, tab)

            if auto_bypass:
                self.check_and_bypass_captcha(page)

            js_code = """() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };

                const elements = [];
                const query = 'button, a[href], input, select, textarea, [role="button"], [role="checkbox"], [role="tab"], [role="link"], [data-cy], [data-date]';
                const all = Array.from(document.querySelectorAll(query));

                for (const el of all) {
                    if (!isVisible(el)) continue;
                    
                    const tag = el.tagName.toLowerCase();
                    const role = el.getAttribute('role') || '';
                    const type = el.getAttribute('type') || '';
                    const id = el.id ? '#' + el.id : '';
                    const name = el.getAttribute('name') || '';
                    const placeholder = el.getAttribute('placeholder') || '';
                    const ariaLabel = el.getAttribute('aria-label') || '';
                    const dataCy = el.getAttribute('data-cy') || '';
                    const dataDate = el.getAttribute('data-date') || '';
                    const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true' || el.classList.contains('disabled');
                    
                    let labelText = '';
                    if (el.id) {
                        const l = document.querySelector('label[for="' + el.id + '"]');
                        if (l) labelText = l.innerText.trim();
                    }
                    if (!labelText && el.closest('label')) {
                        labelText = el.closest('label').innerText.trim();
                    }

                    let rawText = (el.innerText || el.value || ariaLabel || placeholder || '').trim();
                    rawText = rawText.replace(/\\s+/g, ' ').slice(0, 50);

                    // 智慧建議最佳定位器
                    let suggested = '';
                    if (dataCy) {
                        suggested = '[data-cy="' + dataCy + '"]';
                    } else if (dataDate) {
                        suggested = '[data-date="' + dataDate + '"]';
                    } else if (id) {
                        suggested = id;
                    } else if (placeholder) {
                        suggested = 'placeholder="' + placeholder + '"';
                    } else if (labelText) {
                        suggested = 'label="' + labelText + '"';
                    } else if (rawText && rawText.length <= 25 && tag !== 'select') {
                        suggested = 'text="' + rawText + '"';
                    } else if (name) {
                        suggested = '[name="' + name + '"]';
                    }

                    elements.push({
                        tag: tag,
                        type: type,
                        role: role,
                        text: rawText,
                        id: id,
                        name: name,
                        placeholder: placeholder,
                        label: labelText,
                        disabled: disabled,
                        suggested: suggested
                    });
                }
                return elements;
            }"""

            items = page.evaluate(js_code)
            return {
                "title": page.title(),
                "url": page.url,
                "count": len(items),
                "elements": items
            }

    def click_element(
        self,
        selector: Optional[str] = None,
        text: Optional[str] = None,
        label: Optional[str] = None,
        placeholder: Optional[str] = None,
        role: Optional[str] = None,
        name: Optional[str] = None,
        tab: Optional[str] = None,
        auto_bypass: bool = True,
        wait_after: int = 1000
    ) -> bool:
        """支援自然語意與文字點擊 DOM 元素"""
        self.ensure_chrome()
        with self.session_lock("browser-relay:click", timeout=30.0):
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                page = self._pick_page(context, tab)

                if auto_bypass:
                    self.check_and_bypass_captcha(page)

                loc = self._smart_locate(
                    page, selector=selector, text=text, label=label,
                    placeholder=placeholder, role=role, name=name
                )
                loc.wait_for(state="visible", timeout=8000)
                loc.click()

                if wait_after > 0:
                    page.wait_for_timeout(wait_after)

                if auto_bypass:
                    self.check_and_bypass_captcha(page)

                return True

    def type_text(
        self,
        text_to_type: str,
        selector: Optional[str] = None,
        label: Optional[str] = None,
        placeholder: Optional[str] = None,
        tab: Optional[str] = None,
        clear: bool = True,
        press_enter: bool = False,
        wait_after: int = 500
    ) -> bool:
        """支援自然語意（Label / Placeholder）填寫文字"""
        self.ensure_chrome()
        with self.session_lock("browser-relay:type", timeout=30.0):
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                page = self._pick_page(context, tab)

                loc = self._smart_locate(
                    page, selector=selector, label=label, placeholder=placeholder
                )
                loc.wait_for(state="visible", timeout=8000)
                if clear:
                    loc.fill(text_to_type)
                else:
                    loc.type(text_to_type)

                if press_enter:
                    loc.press("Enter")

                if wait_after > 0:
                    page.wait_for_timeout(wait_after)

                return True

    def select_option(
        self,
        value: str,
        selector: Optional[str] = None,
        label: Optional[str] = None,
        tab: Optional[str] = None,
        wait_after: int = 500
    ) -> bool:
        """在下拉選單選擇選項"""
        self.ensure_chrome()
        with self.session_lock("browser-relay:select", timeout=30.0):
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                page = self._pick_page(context, tab)

                loc = self._smart_locate(page, selector=selector, label=label)
                loc.wait_for(state="visible", timeout=8000)
                loc.select_option(value)

                if wait_after > 0:
                    page.wait_for_timeout(wait_after)

                return True

    def batch_run(
        self,
        steps: List[Dict[str, Any]],
        tab: Optional[str] = None,
        stop_on_error: bool = True
    ) -> Dict[str, Any]:
        """
        連鎖動作批次執行管線 (Batch Action Pipeline)
        一次性執行一組連續動作，避免模型每按一下就要往返思考一次。
        """
        self.ensure_chrome()
        with self.session_lock("browser-relay:batch", timeout=45.0):
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                page = self._pick_page(context, tab)

                executed_logs = []
                start_all = time.perf_counter()

                for idx, step in enumerate(steps, 1):
                    action = step.get("action", "").lower()
                    step_start = time.perf_counter()
                    log_entry = {"step": idx, "action": action, "params": step, "ok": False}

                    try:
                        if action == "goto":
                            url = step.get("url")
                            page.goto(url, timeout=30000, wait_until="domcontentloaded")
                            page.wait_for_timeout(step.get("wait_after", 1500))
                            self.check_and_bypass_captcha(page)

                        elif action == "click":
                            loc = self._smart_locate(
                                page,
                                selector=step.get("selector"),
                                text=step.get("text"),
                                label=step.get("label"),
                                placeholder=step.get("placeholder"),
                                role=step.get("role"),
                                name=step.get("name")
                            )
                            loc.wait_for(state="visible", timeout=step.get("timeout", 8000))
                            loc.click()
                            page.wait_for_timeout(step.get("wait_after", 1000))
                            self.check_and_bypass_captcha(page)

                        elif action in ("type", "fill"):
                            val = str(step.get("value", step.get("text", "")))
                            loc = self._smart_locate(
                                page,
                                selector=step.get("selector"),
                                label=step.get("label"),
                                placeholder=step.get("placeholder")
                            )
                            loc.wait_for(state="visible", timeout=step.get("timeout", 8000))
                            if step.get("clear", True):
                                loc.fill(val)
                            else:
                                loc.type(val)
                            if step.get("press_enter", False):
                                loc.press("Enter")
                            page.wait_for_timeout(step.get("wait_after", 500))

                        elif action == "select":
                            val = str(step.get("value", ""))
                            loc = self._smart_locate(
                                page,
                                selector=step.get("selector"),
                                label=step.get("label")
                            )
                            loc.wait_for(state="visible", timeout=step.get("timeout", 8000))
                            loc.select_option(val)
                            page.wait_for_timeout(step.get("wait_after", 500))

                        elif action == "press":
                            key = step.get("key", "Enter")
                            page.keyboard.press(key)
                            page.wait_for_timeout(step.get("wait_after", 500))

                        elif action == "wait":
                            ms = int(step.get("ms", step.get("time", 1000)))
                            page.wait_for_timeout(ms)

                        elif action == "bypass":
                            self.check_and_bypass_captcha(page)

                        elif action == "eval":
                            code = step.get("code", step.get("expression", ""))
                            eval_res = page.evaluate(code)
                            log_entry["eval_result"] = eval_res

                        elif action == "screenshot":
                            out_path = step.get("output", "batch_screenshot.png")
                            page.screenshot(path=out_path)
                            log_entry["screenshot"] = out_path

                        else:
                            raise ValueError(f"未知的動作類型: {action}")

                        log_entry["ok"] = True
                        log_entry["elapsed_ms"] = round((time.perf_counter() - step_start) * 1000, 1)
                        executed_logs.append(log_entry)

                    except Exception as e:
                        log_entry["ok"] = False
                        log_entry["error"] = str(e)
                        log_entry["elapsed_ms"] = round((time.perf_counter() - step_start) * 1000, 1)
                        executed_logs.append(log_entry)
                        if stop_on_error:
                            return {
                                "ok": False,
                                "failed_step": idx,
                                "error": str(e),
                                "total_elapsed_s": round(time.perf_counter() - start_all, 2),
                                "current_url": page.url,
                                "current_title": page.title(),
                                "logs": executed_logs
                            }

                return {
                    "ok": True,
                    "total_steps": len(steps),
                    "total_elapsed_s": round(time.perf_counter() - start_all, 2),
                    "current_url": page.url,
                    "current_title": page.title(),
                    "logs": executed_logs
                }

    def eval_js(self, expression: str, tab: Optional[str] = None) -> Any:
        """在頁面執行 JavaScript 並回傳結果"""
        self.ensure_chrome()
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(self.cdp_url)
            context = browser.contexts[0]
            page = self._pick_page(context, tab)
            return page.evaluate(expression)

    def screenshot(self, output_path: str = "screenshot.png", tab: Optional[str] = None) -> str:
        """截取分頁畫面"""
        self.ensure_chrome()
        out_p = Path(output_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(self.cdp_url)
            context = browser.contexts[0]
            page = self._pick_page(context, tab)
            page.screenshot(path=str(out_p), full_page=False)
            return str(out_p)

    def close_tab(self, tab: Optional[str] = None) -> bool:
        """關閉指定分頁"""
        if not self.is_running():
            return False
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(self.cdp_url)
            context = browser.contexts[0]
            page = self._pick_page(context, tab)
            page.close()
            return True

    def quit_browser(self) -> bool:
        """關閉 Chrome-Assistant 瀏覽器"""
        if not self.is_running():
            return True
        try:
            out = subprocess.check_output(["lsof", "-ti", f":{self.port}"], text=True).strip()
            pids = [int(p) for p in out.splitlines() if p.isdigit()]
            for pid in pids:
                try:
                    os.kill(pid, 15)
                except Exception:
                    pass
            time.sleep(0.5)
            return not self.is_running()
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="Antigravity 專屬真實 Chrome 瀏覽器自動化客戶端 (Chrome-Assistant)")
    subparsers = parser.add_subparsers(dest="command")

    # status
    subparsers.add_parser("status", help="檢查 Chrome-Assistant 運行狀態與 CDP 連線")

    # tabs
    subparsers.add_parser("tabs", help="列出所有可控分頁")

    # open
    p_open = subparsers.add_parser("open", help="在 Chrome-Assistant 中開啟新分頁")
    p_open.add_argument("url", help="目標網址")
    p_open.add_argument("--no-bypass", action="store_true", help="停用自動防爬盾自愈")

    # read
    p_read = subparsers.add_parser("read", help="讀取頁面結構與乾淨文字")
    p_read.add_argument("url", nargs="?", default=None, help="目標網址（可選）")
    p_read.add_argument("--tab", "-t", default=None, help="目標分頁索引或關鍵字")
    p_read.add_argument("--selector", "-s", default=None, help="限定選取的 CSS/語意 Selector")

    # inspect (NEW)
    p_inspect = subparsers.add_parser("inspect", help="提取可互動元素快照 (按鈕、輸入框、選單與建議定位器)")
    p_inspect.add_argument("--tab", "-t", default=None, help="目標分頁索引或關鍵字")
    p_inspect.add_argument("--json", action="store_true", help="以完整 JSON 格式輸出")

    # click
    p_click = subparsers.add_parser("click", help="點擊 DOM 元素 (支援文字、標籤或 CSS)")
    p_click.add_argument("selector", nargs="?", default=None, help="CSS 或語意 Selector (如 text=確定)")
    p_click.add_argument("--text", help="以可視文字模糊匹配點擊")
    p_click.add_argument("--label", help="以表單 label 文字定位")
    p_click.add_argument("--placeholder", help="以 placeholder 定位")
    p_click.add_argument("--role", help="以 ARIA role 定位 (如 button, link)")
    p_click.add_argument("--name", help="搭配 role 的可視名稱")
    p_click.add_argument("--tab", "-t", default=None, help="目標分頁索引或關鍵字")
    p_click.add_argument("--wait", type=int, default=1000, help="點擊後等候毫秒數 (預設 1000)")

    # type / fill
    p_type = subparsers.add_parser("type", help="在輸入框填入文字")
    p_type.add_argument("text", help="欲輸入的文字")
    p_type.add_argument("--selector", "-s", default=None, help="CSS Selector")
    p_type.add_argument("--label", help="以表單 label 定位輸入框")
    p_type.add_argument("--placeholder", help="以 placeholder 定位輸入框")
    p_type.add_argument("--no-clear", action="store_true", help="不預先清空現有文字")
    p_type.add_argument("--enter", action="store_true", help="輸入完成後按下 Enter 鍵")
    p_type.add_argument("--tab", "-t", default=None, help="目標分頁索引或關鍵字")

    # select (NEW)
    p_select = subparsers.add_parser("select", help="在下拉選單選取值")
    p_select.add_argument("value", help="選取的 option value 或可視文字")
    p_select.add_argument("--selector", "-s", default=None, help="CSS Selector")
    p_select.add_argument("--label", help="以表單 label 定位下拉選單")
    p_select.add_argument("--tab", "-t", default=None, help="目標分頁索引或關鍵字")

    # batch (NEW)
    p_batch = subparsers.add_parser("batch", help="連鎖動作批次執行管線")
    p_batch.add_argument("pipeline", nargs="?", default=None, help="JSON 格式的動作陣列字串")
    p_batch.add_argument("--file", "-f", default=None, help="包含 JSON 動作陣列的檔案路徑")
    p_batch.add_argument("--tab", "-t", default=None, help="目標分頁索引或關鍵字")

    # bypass (NEW)
    p_bypass = subparsers.add_parser("bypass", help="手動觸發防爬蟲驗證盾解鎖")
    p_bypass.add_argument("--tab", "-t", default=None, help="目標分頁索引或關鍵字")

    # eval
    p_eval = subparsers.add_parser("eval", help="在分頁中執行 JavaScript")
    p_eval.add_argument("expression", help="JavaScript 表達式")
    p_eval.add_argument("--tab", "-t", default=None, help="目標分頁索引或關鍵字")

    # screenshot
    p_ss = subparsers.add_parser("screenshot", help="截取分頁畫面")
    p_ss.add_argument("output", nargs="?", default="screenshot.png", help="輸出圖檔路徑")
    p_ss.add_argument("--tab", "-t", default=None, help="目標分頁索引或關鍵字")

    # close
    p_close = subparsers.add_parser("close", help="關閉指定分頁")
    p_close.add_argument("tab", nargs="?", default=None, help="目標分頁索引或關鍵字")

    # gc (NEW)
    p_gc = subparsers.add_parser("gc", help="回收多餘空白頁與閒置殭屍分頁")
    p_gc.add_argument("--max-tabs", type=int, default=8, help="最大保留分頁數 (預設 8)")

    # quit
    subparsers.add_parser("quit", help="關閉 Chrome-Assistant 瀏覽器進程")

    args = parser.parse_args()
    client = ChromeAssistantClient()

    if args.command == "status" or not args.command:
        st = client.get_status()
        print("🌐 【Chrome-Assistant 瀏覽器引擎狀態】")
        print(f"• 核心狀態: {'🟢 運行中 (Ready)' if st['running'] else '⚪ 待命 (呼叫時自動啟動)'}")
        print(f"• CDP 連線端點: {st['cdp_url']}")
        print(f"• 專屬 Profile 目錄: {st['profile_dir']}")
        print(f"• 依賴外掛: 🟢 零依賴 (完全免外掛)")
        print(f"• 機器人標籤: 🟢 已抹除 (navigator.webdriver = false)")
        print(f"• 防爬盾自愈: 🟢 支援 PerimeterX 長按、Cloudflare Turnstile")
        print(json.dumps(st, ensure_ascii=False))

    elif args.command == "tabs":
        tabs = client.get_tabs()
        print(f"📑 當前 Chrome-Assistant 共偵測到 {len(tabs)} 個可控分頁：\n")
        for i, t in enumerate(tabs, 1):
            print(f"{i}. [{t['title'][:40]}]")
            print(f"   網址: {t['url']}")
            print(f"   ID: {t['target_id']}\n")
        print(json.dumps(tabs, ensure_ascii=False))

    elif args.command == "open":
        res = client.open_tab(args.url, auto_bypass=(not args.no_bypass))
        print(f"🚀 已在 Chrome-Assistant 開啟分頁：{args.url}")
        print(f"📄 標題：{res.get('title')}")
        print(json.dumps(res, ensure_ascii=False))

    elif args.command == "read":
        res = client.read_page(url=args.url, tab=args.tab, selector=args.selector)
        print(f"📖 標題：{res.get('title')}")
        print(f"🔗 網址：{res.get('url')}")
        print(f"📄 內文長度：{res.get('length')} 字元\n")
        print("── 內文摘錄 ──")
        print(res.get("text", "")[:1000] + ("..." if len(res.get("text", "")) > 1000 else ""))

    elif args.command == "inspect":
        res = client.inspect_page(tab=args.tab)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"🔍 【頁面可互動元素快照】(共 {res['count']} 個元素)")
            print(f"📄 標題：{res['title']}")
            print(f"🔗 網址：{res['url']}\n")
            for el in res["elements"]:
                dis = " (🚫 停用/額滿)" if el["disabled"] else ""
                tag_badge = f"[{el['tag'].upper()}]"
                sugg = f" ➔ 操作建議: {el['suggested']}" if el["suggested"] else ""
                print(f"• {tag_badge:<9} \"{el['text']}\"{dis}{sugg}")

    elif args.command == "click":
        ok = client.click_element(
            selector=args.selector,
            text=args.text,
            label=args.label,
            placeholder=args.placeholder,
            role=args.role,
            name=args.name,
            tab=args.tab,
            wait_after=args.wait
        )
        if ok:
            target_desc = args.text or args.label or args.selector or "元素"
            print(f"✅ 成功點擊：{target_desc}")
        else:
            print("❌ 點擊失敗")
            sys.exit(1)

    elif args.command == "type":
        ok = client.type_text(
            text_to_type=args.text,
            selector=args.selector,
            label=args.label,
            placeholder=args.placeholder,
            tab=args.tab,
            clear=(not args.no_clear),
            press_enter=args.enter
        )
        if ok:
            target_desc = args.label or args.placeholder or args.selector or "輸入框"
            print(f"✅ 成功填入「{target_desc}」➔ \"{args.text}\"")
        else:
            print("❌ 填寫失敗")
            sys.exit(1)

    elif args.command == "select":
        ok = client.select_option(
            value=args.value,
            selector=args.selector,
            label=args.label,
            tab=args.tab
        )
        if ok:
            target_desc = args.label or args.selector or "下拉選單"
            print(f"✅ 成功在「{target_desc}」選取 ➔ \"{args.value}\"")
        else:
            print("❌ 選取失敗")
            sys.exit(1)

    elif args.command == "batch":
        pipeline_json = args.pipeline
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                pipeline_json = f.read()

        if not pipeline_json:
            print("❌ 必須提供 pipeline JSON 字串或 --file 檔案路徑", file=sys.stderr)
            sys.exit(1)

        try:
            steps = json.loads(pipeline_json)
            if not isinstance(steps, list):
                raise ValueError("Pipeline 必須為動作物件陣列 (list of dict)")
        except Exception as e:
            print(f"❌ JSON 解析失敗: {e}", file=sys.stderr)
            sys.exit(1)

        res = client.batch_run(steps, tab=args.tab)
        print("⚡ 【批次管線執行結果】")
        print(f"• 狀態: {'🟢 成功完成' if res['ok'] else '❌ 執行失敗'}")
        print(f"• 總耗時: {res['total_elapsed_s']} 秒")
        print(f"• 當前網址: {res['current_url']}")
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if not res["ok"]:
            sys.exit(1)

    elif args.command == "bypass":
        self_chrome = ChromeAssistantClient()
        self_chrome.ensure_chrome()
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(self_chrome.cdp_url)
            page = self_chrome._pick_page(browser.contexts[0], args.tab)
            bypassed = self_chrome.check_and_bypass_captcha(page)
            if bypassed:
                print("✅ 成功觸發並完成驗證盾長按解鎖！")
            else:
                print("ℹ️ 當前頁面未偵測到已知驗證盾或已通過。")

    elif args.command == "eval":
        res = client.eval_js(args.expression, tab=args.tab)
        print("💻 執行結果：")
        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.command == "screenshot":
        path = client.screenshot(args.output, tab=args.tab)
        print(f"📸 截圖已儲存至：{path}")

    elif args.command == "close":
        ok = client.close_tab(args.tab)
        if ok:
            print("🗑️ 已關閉分頁")
        else:
            print("❌ 關閉分頁失敗")
            sys.exit(1)

    elif args.command == "gc":
        res = client.gc_tabs(max_tabs=args.max_tabs)
        print(f"🧹 【分頁回收完成】已關閉 {res.get('closed', 0)} 個多餘/殭屍分頁，目前剩餘 {res.get('remaining', 0)} 個分頁。")
        print(json.dumps(res, ensure_ascii=False))

    elif args.command == "quit":
        ok = client.quit_browser()
        print(f"🛑 Chrome-Assistant 已關閉：{ok}")


if __name__ == "__main__":
    main()
