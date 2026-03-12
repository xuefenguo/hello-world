#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

HOST = "0.0.0.0"
PORT = 8765
BASE_DIR = Path(__file__).resolve().parents[2]
WEB_PAGE = BASE_DIR / "apps" / "desktop-ui" / "status.html"
CONFIG_DIR = Path.home() / ".openclaw-desktop"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = CONFIG_DIR / "data"
SESSIONS_FILE = DATA_DIR / "sessions.json"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DIAG_DIR = DATA_DIR / "diagnostics"
LOG_FILE = DATA_DIR / "app.log"
ACTIONS_FILE = DATA_DIR / "actions.json"

ALLOWED_ACTIONS = {
    "pwd": ["pwd"],
    "date": ["date"],
    "ls_home": ["ls", "-la", str(Path.home())],
    "whoami": ["whoami"],
    "uname": ["uname", "-a"],
}

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {"name": "openclaw-desktop", "version": "0.3.0"},
    "chat": {"cloud_fallback_to_local": True, "max_history_messages": 40},
    "computer_control": {"enabled": True, "require_confirm": True},
    "providers": {
        "local": {"enabled": True, "model": "local-echo", "endpoint": ""},
        "cloud": {
            "enabled": False,
            "base_url": "https://api.openai.com/v1/chat/completions",
            "api_key": "",
            "model": "gpt-4o-mini",
            "timeout_s": 45,
        },
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(event: str, payload: dict[str, Any] | None = None) -> None:
    payload = payload or {}
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{now_iso()} | {event} | {json.dumps(payload, ensure_ascii=False)}\n")


def ensure_paths() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    if not SESSIONS_FILE.exists():
        SESSIONS_FILE.write_text("[]", encoding="utf-8")
    if not ACTIONS_FILE.exists():
        ACTIONS_FILE.write_text("[]", encoding="utf-8")
    if not LOG_FILE.exists():
        LOG_FILE.write_text("", encoding="utf-8")


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict[str, Any]:
    data = load_json(CONFIG_FILE, DEFAULT_CONFIG)
    return data if isinstance(data, dict) else DEFAULT_CONFIG


def save_config(cfg: dict[str, Any]) -> None:
    save_json(CONFIG_FILE, cfg)


def load_sessions() -> list[dict[str, Any]]:
    data = load_json(SESSIONS_FILE, [])
    return data if isinstance(data, list) else []


def save_sessions(items: list[dict[str, Any]]) -> None:
    save_json(SESSIONS_FILE, items)


def load_actions() -> list[dict[str, Any]]:
    data = load_json(ACTIONS_FILE, [])
    return data if isinstance(data, list) else []


def save_actions(items: list[dict[str, Any]]) -> None:
    save_json(ACTIONS_FILE, items)


def find_or_create_session(session_id: str | None, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    if session_id:
        for s in sessions:
            if s.get("id") == session_id:
                return s
    s = {"id": str(uuid4()), "title": "新会话", "created_at": now_iso(), "updated_at": now_iso(), "messages": []}
    sessions.append(s)
    return s


def get_by_id(items: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    for i in items:
        if i.get("id") == item_id:
            return i
    return None


def sanitize_name(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    return clean or f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def list_snapshots() -> list[dict[str, Any]]:
    out = []
    for p in sorted(SNAPSHOT_DIR.glob("*.json"), reverse=True):
        out.append({"name": p.stem, "path": str(p), "updated_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat()})
    return out


def local_generate(messages: list[dict[str, str]], model: str) -> str:
    user_messages = [m.get("content", "") for m in messages if m.get("role") == "user"]
    latest = user_messages[-1] if user_messages else ""
    return f"[本地模型:{model}] 已收到：{latest}"


def cloud_generate(messages: list[dict[str, str]], cfg: dict[str, Any], model: str) -> str:
    cloud = cfg.get("providers", {}).get("cloud", {})
    base_url = str(cloud.get("base_url", "")).strip()
    api_key = str(cloud.get("api_key", "")).strip()
    timeout_s = int(cloud.get("timeout_s", 45))
    if not base_url or not api_key:
        raise ValueError("云端模型未配置 base_url 或 api_key")
    req = urllib.request.Request(
        base_url,
        method="POST",
        data=json.dumps({"model": model, "messages": messages, "temperature": 0.7}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return str(body.get("choices", [{}])[0].get("message", {}).get("content", ""))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"云端调用失败: {exc}") from exc


def validate_provider(provider: str) -> bool:
    return provider in {"local", "cloud"}


def chat_generate(provider: str, model: str, messages: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[str, str]:
    if provider == "local":
        return local_generate(messages, model), "local"
    try:
        return cloud_generate(messages, cfg, model), "cloud"
    except Exception:
        if not bool(cfg.get("chat", {}).get("cloud_fallback_to_local", True)):
            raise
        local_model = str(cfg.get("providers", {}).get("local", {}).get("model", "local-echo"))
        return local_generate(messages, local_model), "local-fallback"


def detect_action_from_text(text: str) -> dict[str, Any] | None:
    t = text.lower().strip()
    if any(k in t for k in ["当前目录", "pwd", "目录在哪"]):
        return {"action_type": "pwd", "command": ALLOWED_ACTIONS["pwd"], "reason": "查询当前目录"}
    if any(k in t for k in ["日期", "时间", "date"]):
        return {"action_type": "date", "command": ALLOWED_ACTIONS["date"], "reason": "查询系统时间"}
    if any(k in t for k in ["列出", "文件", "home", "家目录"]):
        return {"action_type": "ls_home", "command": ALLOWED_ACTIONS["ls_home"], "reason": "查看主目录内容"}
    if any(k in t for k in ["我是谁", "whoami", "当前用户"]):
        return {"action_type": "whoami", "command": ALLOWED_ACTIONS["whoami"], "reason": "查询当前用户"}
    if any(k in t for k in ["系统信息", "uname", "内核"]):
        return {"action_type": "uname", "command": ALLOWED_ACTIONS["uname"], "reason": "查询系统信息"}
    return None


def execute_allowed_action(action_type: str) -> dict[str, Any]:
    if action_type not in ALLOWED_ACTIONS:
        raise ValueError("action not allowed")
    cmd = ALLOWED_ACTIONS[action_type]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    return {
        "command": shlex.join(cmd),
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenClawOrchestrator/0.4"

    def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: bytes, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json({
                "status": "ok", "runtime": "running", "version": "0.4.0", "timestamp": now_iso(),
                "config_path": str(CONFIG_FILE), "sessions_path": str(SESSIONS_FILE), "actions_path": str(ACTIONS_FILE), "log_path": str(LOG_FILE)
            })
            return
        if self.path == "/api/config":
            self._send_json({"config": load_config()})
            return
        if self.path == "/api/sessions":
            self._send_json({"sessions": load_sessions()})
            return
        if self.path == "/api/actions":
            self._send_json({"actions": load_actions()})
            return
        if self.path.startswith("/api/sessions/") and self.path.endswith("/export"):
            sid = self.path.split("/")[3]
            s = get_by_id(load_sessions(), sid)
            if not s:
                self._send_json({"error": "session not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"session": s})
            return
        if self.path == "/api/snapshots":
            self._send_json({"snapshots": list_snapshots()})
            return
        if self.path == "/api/diagnostics/export":
            sessions = load_sessions()
            actions = load_actions()
            diag = {
                "generated_at": now_iso(),
                "health": {"status": "ok", "version": "0.4.0"},
                "config": load_config(),
                "session_count": len(sessions),
                "action_count": len(actions),
                "latest_sessions": [{"id": s.get("id"), "title": s.get("title")} for s in sessions[-5:]],
                "latest_actions": actions[-10:],
            }
            p = DIAG_DIR / f"diag_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            save_json(p, diag)
            self._send_json({"ok": True, "diagnostic_path": str(p), "diagnostic": diag})
            return
        if self.path in {"/", "/index.html", "/status"}:
            self._send_html(WEB_PAGE.read_bytes() if WEB_PAGE.exists() else b"<h1>UI page not found</h1>", status=HTTPStatus.OK if WEB_PAGE.exists() else HTTPStatus.NOT_FOUND)
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/config":
            body = self._read_json()
            cfg = body.get("config")
            if not isinstance(cfg, dict):
                self._send_json({"error": "invalid config"}, status=HTTPStatus.BAD_REQUEST)
                return
            save_config(cfg)
            log_event("config_saved")
            self._send_json({"ok": True, "config": cfg})
            return
        if self.path == "/api/sessions":
            sessions = load_sessions()
            s = find_or_create_session(None, sessions)
            save_sessions(sessions)
            self._send_json({"ok": True, "session": s})
            return
        if self.path.startswith("/api/sessions/") and self.path.endswith("/rename"):
            sid = self.path.split("/")[3]
            title = str(self._read_json().get("title", "")).strip()
            if not title:
                self._send_json({"error": "title is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            sessions = load_sessions()
            s = get_by_id(sessions, sid)
            if not s:
                self._send_json({"error": "session not found"}, status=HTTPStatus.NOT_FOUND)
                return
            s["title"] = title
            s["updated_at"] = now_iso()
            save_sessions(sessions)
            self._send_json({"ok": True, "session": s})
            return
        if self.path == "/api/snapshots":
            name = sanitize_name(str(self._read_json().get("name", "")))
            target = SNAPSHOT_DIR / f"{name}.json"
            save_json(target, load_config())
            self._send_json({"ok": True, "snapshot": {"name": name, "path": str(target)}})
            return
        if self.path.startswith("/api/snapshots/") and self.path.endswith("/restore"):
            name = self.path.split("/")[3]
            target = SNAPSHOT_DIR / f"{sanitize_name(name)}.json"
            if not target.exists():
                self._send_json({"error": "snapshot not found"}, status=HTTPStatus.NOT_FOUND)
                return
            cfg = load_json(target, DEFAULT_CONFIG)
            if not isinstance(cfg, dict):
                self._send_json({"error": "invalid snapshot"}, status=HTTPStatus.BAD_REQUEST)
                return
            save_config(cfg)
            self._send_json({"ok": True, "config": cfg})
            return
        if self.path == "/v1/config/test":
            provider = str(self._read_json().get("provider", "local"))
            cfg = load_config()
            try:
                if provider == "local":
                    local_generate([{"role": "user", "content": "ping"}], str(cfg.get("providers", {}).get("local", {}).get("model", "local-echo")))
                elif provider == "cloud":
                    cloud_generate([{"role": "user", "content": "ping"}], cfg, str(cfg.get("providers", {}).get("cloud", {}).get("model", "gpt-4o-mini")))
                else:
                    raise ValueError("unsupported provider")
            except Exception as exc:
                self._send_json({"ok": False, "error_message": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True})
            return
        if self.path == "/v1/chat/completions":
            body = self._read_json()
            provider = str(body.get("provider", "local"))
            model = str(body.get("model", "local-echo"))
            sid = body.get("session_id")
            messages = body.get("messages", [])
            if not validate_provider(provider):
                self._send_json({"error": "invalid provider"}, status=HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(messages, list):
                self._send_json({"error": "messages must be list"}, status=HTTPStatus.BAD_REQUEST)
                return
            cfg = load_config()
            sessions = load_sessions()
            s = find_or_create_session(str(sid) if sid else None, sessions)
            try:
                content, actual = chat_generate(provider, model, messages, cfg)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            s["messages"] = (messages + [{"role": "assistant", "content": content}])[-int(cfg.get("chat", {}).get("max_history_messages", 40)):]
            s["updated_at"] = now_iso()
            if s.get("title") == "新会话":
                first_user = next((m.get("content") for m in messages if m.get("role") == "user"), "新会话")
                s["title"] = str(first_user)[:24]
            save_sessions(sessions)
            self._send_json({
                "session_id": s["id"], "provider": provider, "actual_provider": actual, "model": model, "content": content,
                "usage": {"prompt_tokens": sum(len(str(m.get("content", ""))) for m in messages), "completion_tokens": len(content)}
            })
            return
        if self.path == "/v1/chat/operate":
            body = self._read_json()
            text = str(body.get("text", "")).strip()
            sid = str(body.get("session_id", "") or "")
            cfg = load_config()
            if not bool(cfg.get("computer_control", {}).get("enabled", True)):
                self._send_json({"error": "computer control disabled"}, status=HTTPStatus.BAD_REQUEST)
                return
            plan = detect_action_from_text(text)
            if not plan:
                self._send_json({"ok": False, "message": "未识别可执行的电脑操作，请尝试：查询当前目录/系统时间/主目录文件"})
                return
            actions = load_actions()
            action = {
                "id": str(uuid4()), "created_at": now_iso(), "session_id": sid or None, "status": "pending",
                "text": text, "action_type": plan["action_type"], "reason": plan["reason"], "command": plan["command"],
            }
            actions.append(action)
            save_actions(actions)
            log_event("action_planned", {"action_id": action["id"], "action_type": action["action_type"]})
            require_confirm = bool(cfg.get("computer_control", {}).get("require_confirm", True))
            if require_confirm:
                self._send_json({"ok": True, "requires_confirm": True, "action": action})
                return
            result = execute_allowed_action(action["action_type"])
            action["status"] = "done"
            action["executed_at"] = now_iso()
            action["result"] = result
            save_actions(actions)
            log_event("action_executed", {"action_id": action["id"], "action_type": action["action_type"], "returncode": result.get("returncode")})
            self._send_json({"ok": True, "requires_confirm": False, "action": action})
            return
        if self.path.startswith("/api/actions/") and self.path.endswith("/execute"):
            action_id = self.path.split("/")[3]
            actions = load_actions()
            action = get_by_id(actions, action_id)
            if not action:
                self._send_json({"error": "action not found"}, status=HTTPStatus.NOT_FOUND)
                return
            if action.get("status") == "done":
                self._send_json({"ok": True, "action": action})
                return
            try:
                result = execute_allowed_action(str(action.get("action_type", "")))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            action["status"] = "done"
            action["executed_at"] = now_iso()
            action["result"] = result
            save_actions(actions)
            log_event("action_executed", {"action_id": action_id, "action_type": action.get("action_type"), "returncode": result.get("returncode")})
            self._send_json({"ok": True, "action": action})
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.startswith("/api/sessions/"):
            sid = self.path.split("/")[3]
            sessions = load_sessions()
            new_sessions = [s for s in sessions if s.get("id") != sid]
            if len(new_sessions) == len(sessions):
                self._send_json({"error": "session not found"}, status=HTTPStatus.NOT_FOUND)
                return
            save_sessions(new_sessions)
            self._send_json({"ok": True})
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    ensure_paths()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"OpenClaw Orchestrator listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
