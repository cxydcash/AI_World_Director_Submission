"""Zero-dependency HTTP visualizer for the AI World Director replay."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo_replay import ReplayEngine
from simulation_engine import SimulationEngine
from interactive_simulation import InteractiveSimulationEngine
from open_world import OpenWorldEngine
try:  # 作为包导入（visualization.app）
    from .narrative import DailyNarrativeWriter
    from .player_view import PlayerObserver, build_player_view
except ImportError:  # 作为脚本运行（python visualization/app.py）
    from narrative import DailyNarrativeWriter
    from player_view import PlayerObserver, build_player_view
from world import EXPLORE_LOCATIONS


STATIC_DIR = Path(__file__).resolve().parent
ENGINE = ReplayEngine()
SIMULATION_ENGINE = SimulationEngine()
INTERACTIVE_ENGINE = InteractiveSimulationEngine()
OPEN_WORLD_ENGINE = OpenWorldEngine()

# 只读观察器：把每一 tick 的真实快照整理成"NPC 动态 / 轨迹"。
# 它不修改世界状态，只补充世界本身没有保存的展示历史。
OPEN_WORLD_OBSERVER = PlayerObserver()
# 每日叙事编写器：一天结束时把真实事件整理成一幕一幕（按天缓存）。
OPEN_WORLD_NARRATOR = DailyNarrativeWriter()
# 地图「关注人物」列表：只影响展示层，绝不写进 WorldState。
# None 表示自动（默认关注主要人物）。
MAP_FOCUS = {'npc_ids': None}


def install_observer(engine, observer):
    """在每次真实 tick 之后记录一次只读快照（含后台自动运行）。"""
    original_tick = engine.tick

    def observed_tick(*args, **kwargs):
        result = original_tick(*args, **kwargs)
        try:
            observer.observe(engine.snapshot())
        except Exception:
            pass
        return result

    engine.tick = observed_tick
    return engine


install_observer(OPEN_WORLD_ENGINE, OPEN_WORLD_OBSERVER)


def advance_world(engine, observer, hours):
    """按真实 tick 机制推进若干小时（1 小时 = 1 tick），不改变模拟规则。"""
    engine.pause()
    original_tick_seconds = engine.tick_seconds
    engine.tick_seconds = 3600.0
    try:
        steps = max(1, int(round(float(hours))))
        for _ in range(steps):
            engine.tick()
            observer.observe(engine.snapshot())
    finally:
        engine.tick_seconds = original_tick_seconds
    return engine.current()


class VisualizerHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object.")
        return payload

    def _send_file(self, path):
        if not path.exists():
            self.send_error(404)
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(path.suffix, "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            self._send_json(ENGINE.current())
            return
        if path == "/api/simulation/state":
            self._send_json(SIMULATION_ENGINE.current())
            return
        if path == "/api/interactive/state":
            self._send_json(INTERACTIVE_ENGINE.current())
            return
        if path == "/api/open-world/state":
            self._send_json(OPEN_WORLD_ENGINE.current())
            return
        if path == "/api/open-world/player-view":
            self._send_json(build_player_view(
                OPEN_WORLD_ENGINE, OPEN_WORLD_OBSERVER, EXPLORE_LOCATIONS,
                OPEN_WORLD_NARRATOR, focus_ids=MAP_FOCUS['npc_ids'],
            ))
            return
        if path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            self._send_file(STATIC_DIR / path[len("/static/"):])
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/open-world/start":
            self._send_json(OPEN_WORLD_ENGINE.start())
            return
        if path == "/api/open-world/pause":
            self._send_json(OPEN_WORLD_ENGINE.pause())
            return
        if path == "/api/open-world/pause-input":
            self._send_json(OPEN_WORLD_ENGINE.pause_for_input())
            return
        if path == "/api/open-world/resume":
            self._send_json(OPEN_WORLD_ENGINE.resume_after_input())
            return
        if path == "/api/open-world/reset":
            OPEN_WORLD_OBSERVER.reset()
            OPEN_WORLD_NARRATOR.reset()
            MAP_FOCUS['npc_ids'] = None
            self._send_json(OPEN_WORLD_ENGINE.reset())
            return
        if path == "/api/open-world/map-focus":
            try:
                payload = self._read_json_body()
                ids = payload.get("npc_ids")
                if ids is None:
                    MAP_FOCUS["npc_ids"] = None          # 恢复自动（主要人物）
                elif isinstance(ids, list):
                    MAP_FOCUS["npc_ids"] = [str(x) for x in ids]
                else:
                    raise ValueError("npc_ids must be a list or null")
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(build_player_view(
                OPEN_WORLD_ENGINE, OPEN_WORLD_OBSERVER, EXPLORE_LOCATIONS,
                OPEN_WORLD_NARRATOR, focus_ids=MAP_FOCUS["npc_ids"],
            ))
            return
        if path == "/api/open-world/advance":
            try:
                payload = self._read_json_body()
                hours = float(payload.get("hours", 1))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            state = advance_world(OPEN_WORLD_ENGINE, OPEN_WORLD_OBSERVER, hours)
            self._send_json(build_player_view(
                OPEN_WORLD_ENGINE, OPEN_WORLD_OBSERVER, EXPLORE_LOCATIONS,
                OPEN_WORLD_NARRATOR, focus_ids=MAP_FOCUS['npc_ids'],
            ))
            return
        if path == "/api/open-world/move":
            try:
                payload = self._read_json_body()
                result = OPEN_WORLD_ENGINE.move(payload.get("direction", ""))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200 if result.get("success") else 400)
            return
        if path == "/api/open-world/analyze":
            try:
                payload = self._read_json_body()
                result = OPEN_WORLD_ENGINE.analyze_thought(payload.get("input", ""))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200 if result.get("success") else 400)
            return
        if path == "/api/open-world/execute":
            try:
                payload = self._read_json_body()
                result = OPEN_WORLD_ENGINE.execute_candidate(payload.get("candidate_id", ""))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200 if result.get("success") else 400)
            return
        if path == "/api/open-world/action":
            try:
                payload = self._read_json_body()
                result = OPEN_WORLD_ENGINE.execute_action(payload.get("action"))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200 if result.get("success") else 400)
            return
        if path == "/api/open-world/talk":
            try:
                payload = self._read_json_body()
                result = OPEN_WORLD_ENGINE.talk(payload.get("npc_id", ""), payload.get("message", ""))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200 if result.get("success") else 400)
            return
        if path == "/api/open-world/dialogue/end":
            try:
                result = OPEN_WORLD_ENGINE.end_dialogue_session()
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200)
            return
        if path == "/api/interactive/start":
            self._send_json(INTERACTIVE_ENGINE.start())
            return
        if path == "/api/interactive/pause":
            self._send_json(INTERACTIVE_ENGINE.pause())
            return
        if path == "/api/interactive/pause-input":
            self._send_json(INTERACTIVE_ENGINE.pause_for_input())
            return
        if path == "/api/interactive/resume":
            self._send_json(INTERACTIVE_ENGINE.resume_after_input())
            return
        if path == "/api/interactive/reset":
            self._send_json(INTERACTIVE_ENGINE.reset())
            return
        if path == "/api/interactive/move":
            try:
                payload = self._read_json_body()
                result = INTERACTIVE_ENGINE.move(payload.get("direction", ""))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200 if result.get("success") else 400)
            return
        if path == "/api/interactive/analyze":
            try:
                payload = self._read_json_body()
                result = INTERACTIVE_ENGINE.analyze_thought(payload.get("input", ""))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200 if result.get("success") else 400)
            return
        if path == "/api/interactive/execute":
            try:
                payload = self._read_json_body()
                result = INTERACTIVE_ENGINE.execute_candidate(payload.get("candidate_id", ""))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200 if result.get("success") else 400)
            return
        if path == "/api/interactive/action":
            try:
                payload = self._read_json_body()
                result = INTERACTIVE_ENGINE.execute_action(payload.get("action"))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200 if result.get("success") else 400)
            return
        if path == "/api/interactive/talk":
            try:
                payload = self._read_json_body()
                result = INTERACTIVE_ENGINE.talk(payload.get("npc_id", ""), payload.get("message", ""))
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            self._send_json(result, status=200 if result.get("success") else 400)
            return
        if path == "/api/simulation/start":
            self._send_json(SIMULATION_ENGINE.start())
            return
        if path == "/api/simulation/pause":
            self._send_json(SIMULATION_ENGINE.pause())
            return
        if path == "/api/simulation/reset":
            self._send_json(SIMULATION_ENGINE.reset())
            return
        if path == "/api/simulation/tick":
            self._send_json(SIMULATION_ENGINE.tick())
            return
        if path == "/api/action":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                action_text = payload.get("input", "")
                result = ENGINE.apply_input(action_text)
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return
            if not result.get("success"):
                self._send_json(result, status=400)
                return
            self._send_json(result)
            return
        if path == "/api/reset":
            ENGINE.reset()
            self._send_json(ENGINE.current())
            return
        if path == "/api/next":
            self._send_json(ENGINE.next_event())
            return
        if path == "/api/replay":
            self._send_json({"events": ENGINE.run_all()})
            return
        self.send_error(404)

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


def run_server(host="127.0.0.1", port=8000):
    server = ThreadingHTTPServer((host, port), VisualizerHandler)
    print(f"AI World Director visualizer: http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_server(args.host, args.port)


