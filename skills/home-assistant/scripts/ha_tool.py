#!/usr/bin/env python3
import sys
import os
import json
import urllib.request
import urllib.error
from pathlib import Path

BASE_DIR = next((p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists() or (p / ".git").exists()), Path(__file__).resolve().parents[2])
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from common.venv_guard import ensure_venv
    ensure_venv()
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv(BASE_DIR / os.getenv("ENV_FILE", ".env"))

HASS_URL = os.getenv("HASS_URL", "").rstrip("/")
HASS_TOKEN = os.getenv("HASS_TOKEN", "")

def request_ha(endpoint: str, method: str = "GET", data: dict = None):
    if not HASS_URL or not HASS_TOKEN:
        print("Error: HASS_URL 或 HASS_TOKEN 未設定", file=sys.stderr)
        sys.exit(1)
    url = f"{HASS_URL}/api/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json"
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode('utf-8')}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)

def list_entities(domain: str = None):
    states = request_ha("states")
    results = []
    for s in states:
        entity_id = s.get("entity_id", "")
        if not domain or entity_id.startswith(f"{domain}."):
            results.append({
                "entity_id": entity_id,
                "name": s.get("attributes", {}).get("friendly_name", entity_id),
                "state": s.get("state")
            })
    print(json.dumps(results, ensure_ascii=False, indent=2))

def get_state(entity_id: str):
    state = request_ha(f"states/{entity_id}")
    print(json.dumps(state, ensure_ascii=False, indent=2))

def call_service(domain: str, service: str, service_data: dict):
    res = request_ha(f"services/{domain}/{service}", method="POST", data=service_data)
    print(json.dumps({"success": True, "result": res}, ensure_ascii=False))

def main():
    if len(sys.argv) < 2:
        print("Usage: ha_tool.py [list <domain> | get <entity_id> | on <entity_id> | off <entity_id> | temp <climate_id> <degrees> | service <domain> <service> <json_data>]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "list":
        domain = sys.argv[2] if len(sys.argv) > 2 else None
        list_entities(domain)
    elif cmd == "get":
        if len(sys.argv) < 3:
            print("Specify entity_id")
            sys.exit(1)
        get_state(sys.argv[2])
    elif cmd == "on":
        entity_id = sys.argv[2]
        domain = entity_id.split(".")[0]
        call_service(domain, "turn_on", {"entity_id": entity_id})
    elif cmd == "off":
        entity_id = sys.argv[2]
        domain = entity_id.split(".")[0]
        call_service(domain, "turn_off", {"entity_id": entity_id})
    elif cmd == "temp":
        climate_id = sys.argv[2]
        temp = float(sys.argv[3])
        call_service("climate", "set_temperature", {"entity_id": climate_id, "temperature": temp})
    elif cmd == "service":
        domain = sys.argv[2]
        service = sys.argv[3]
        data = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
        call_service(domain, service, data)
    else:
        print(f"Unknown command {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
