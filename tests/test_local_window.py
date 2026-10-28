"""Persistence and collaboration invariants of the local timing window.

Temporary files stay beneath this checkout's .cache, including when the
suite is run from an unrelated working directory. No simulator is needed.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import model
from local_window.demo import make_demo
from local_window.store import ConflictError, SessionStore
import local_window.store as store_module
from local_window.server import make_server
import local_window.server as server_module


CACHE = Path(__file__).resolve().parents[1] / ".cache" / "local-window-tests"


def fixture():
    """Two separated pulses and an independent reference edge, in fs."""
    project = model.default_project()
    project.update(unit="fs", period_fs=100, rise_fs=1, fall_fs=2, cycles=0)
    project["signals"] = [
        {"id": "sample", "name": "sample", "color": "#bd8d28", "items": [
            {"id": "low0", "type": "segment", "level": 0, "duration_fs": 10},
            {"id": "high1", "type": "segment", "level": 1, "duration_fs": 10},
            {"id": "gap", "type": "segment", "level": 0, "duration_fs": 10},
            {"id": "high2", "type": "segment", "level": 1, "duration_fs": 10},
            {"id": "tail", "type": "segment", "level": 0, "duration_fs": 60},
        ]},
        {"id": "reference", "name": "reference", "items": [
            {"id": "ref0", "type": "segment", "level": 0, "duration_fs": 55},
            {"id": "ref1", "type": "segment", "level": 1, "duration_fs": 45},
        ]},
    ]
    project["view"] = {"row_height": 64, "signal_order": ["sample", "reference"]}
    project["description"] = "测试手动编辑后继续协作"
    project["collaboration"] = {"note": "保留用户描述", "groups": ["a", "b"]}
    return project


def spans(signal):
    result, cursor = [], 0
    for item in signal["items"]:
        end = cursor + item["duration_fs"]
        result.append((cursor, end, item["level"]))
        cursor = end
    return result


class StoreTests(unittest.TestCase):
    def setUp(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="store-", dir=CACHE)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = SessionStore(self.directory, initial=fixture())

    def edit(self, operation):
        return self.store.edit(self.store.state()["revision"], operation)

    def test_launch_cannot_silently_ignore_new_project_for_existing_session(self):
        from local_window.launch import start
        with self.assertRaisesRegex(ValueError, '--project'):
            start(self.directory, self.directory / 'new-project.json')

    def test_failed_va_replace_preserves_previous_export(self):
        exported = self.store.export(self.store.state()['revision'])
        destination = Path(exported['path'])
        original = destination.read_bytes()
        with patch.object(store_module.os, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.store.export(self.store.state()['revision'])
        self.assertEqual(destination.read_bytes(), original)

    def test_initial_snapshot_and_returned_state_are_independent(self):
        state = self.store.state()
        self.assertFalse(state["can_undo"])
        self.assertFalse(state["can_redo"])
        self.assertEqual(json.loads(Path(state["project_path"]).read_text("utf-8")),
                         state["project"])
        self.assertTrue(Path(state["project_path"]).is_relative_to(self.directory))
        state["project"]["signals"][0]["name"] = "outside_mutation"
        self.assertEqual(self.store.state()["project"]["signals"][0]["name"], "sample")

    def test_drag_across_gap_merges_pulses_without_moving_other_edges(self):
        before = self.store.state()
        state = self.edit({"op": "boundary", "signal_id": "sample",
                           "item_id": "high1", "new_end_fs": 35})
        self.assertEqual(spans(state["project"]["signals"][0]),
                         [(0, 10, 0), (10, 40, 1), (40, 100, 0)])
        self.assertEqual(state["project"]["signals"][1], before["project"]["signals"][1])
        self.assertEqual(state["revision"], before["revision"] + 1)
        self.assertEqual(state["project"]["signals"][0]["items"][1]["id"], "high1")

    def test_drag_backwards_merges_pulses(self):
        state = self.edit({"op": "boundary", "signal_id": "sample",
                           "item_id": "gap", "new_end_fs": 20})
        self.assertEqual(spans(state["project"]["signals"][0]),
                         [(0, 10, 0), (10, 40, 1), (40, 100, 0)])

    def test_paint_and_toggle_coalesce_levels(self):
        state = self.edit({"op": "paint", "signal_id": "sample",
                           "start_fs": 18, "end_fs": 32, "level": 1})
        self.assertEqual(spans(state["project"]["signals"][0]),
                         [(0, 10, 0), (10, 40, 1), (40, 100, 0)])
        high_id = state["project"]["signals"][0]["items"][1]["id"]
        state = self.edit({"op": "toggle", "signal_id": "sample", "item_id": high_id})
        self.assertEqual(spans(state["project"]["signals"][0]), [(0, 100, 0)])

    def test_invalid_edit_does_not_mutate_memory_or_disk(self):
        operations = [
            {"op": "settings", "values": {"period": "20f"}},
            {"op": "settings", "values": {"rise": "0.1f"}},
            {"op": "settings", "values": {"rise": "0f"}},
            {"op": "settings", "values": {"module_name": "module"}},
            {"op": "settings", "values": {"vhigh": float("nan")}},
            {"op": "rename", "signal_id": "sample", "name": "reference"},
            {"op": "boundary", "signal_id": "sample", "item_id": "high1", "new_end_fs": -1},
            {"op": "boundary", "signal_id": "sample", "item_id": "high1", "new_end_fs": 1.5},
            {"op": "boundary", "signal_id": "sample", "item_id": "missing", "new_end_fs": 30},
            {"op": "paint", "signal_id": "sample", "start_fs": 0, "end_fs": 20, "level": 2},
            {"op": "view", "row_height": 10},
            {"op": "view", "signal_order": ["sample", "sample"]},
        ]
        before = self.store.state()
        head = (self.directory / "head.json").read_bytes()
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    self.edit(operation)
                self.assertEqual(self.store.state(), before)
                self.assertEqual((self.directory / "head.json").read_bytes(), head)
        self.assertEqual(SessionStore(self.directory).state(), before)

    def test_stale_edit_replace_and_export_are_rejected(self):
        initial = self.store.state()
        latest = self.edit({"op": "rename", "signal_id": "sample", "name": "sample_edited"})
        calls = [
            lambda: self.store.edit(initial["revision"], {"op": "undo"}),
            lambda: self.store.replace(initial["revision"], initial["project"]),
            lambda: self.store.export(initial["revision"]),
        ]
        for call in calls:
            with self.assertRaises(ConflictError) as caught:
                call()
            self.assertEqual(caught.exception.state, latest)
            self.assertEqual(self.store.state(), latest)
        self.assertFalse((self.directory / "exports").exists())

    def test_restart_preserves_revision_and_undo_redo(self):
        initial = self.store.state()
        edited = self.edit({"op": "rename", "signal_id": "sample", "name": "sample_edited"})
        reopened = SessionStore(self.directory, initial=make_demo())
        self.assertEqual(reopened.state(), edited)
        undone = reopened.edit(edited["revision"], {"op": "undo"})
        self.assertEqual(undone["project"], initial["project"])
        self.assertEqual(undone["revision"], edited["revision"] + 1)
        self.assertTrue(undone["can_redo"])
        reopened_again = SessionStore(self.directory)
        redone = reopened_again.edit(undone["revision"], {"op": "redo"})
        self.assertEqual(redone["project"], edited["project"])
        self.assertEqual(redone["revision"], undone["revision"] + 1)
        self.assertFalse(redone["can_redo"])

    def test_new_edit_after_undo_discards_redo_branch(self):
        self.edit({"op": "rename", "signal_id": "sample", "name": "first_name"})
        self.edit({"op": "undo"})
        state = self.edit({"op": "rename", "signal_id": "sample", "name": "second_name"})
        self.assertFalse(state["can_redo"])
        self.assertEqual(self.edit({"op": "redo"}), state)

    def test_failed_head_commit_leaves_last_committed_revision_recoverable(self):
        before = self.store.state()
        real_atomic_json = store_module.atomic_json

        def fail_head(path, value):
            if Path(path).name == "head.json":
                raise OSError("模拟写入失败")
            return real_atomic_json(path, value)

        with patch.object(store_module, "atomic_json", side_effect=fail_head):
            with self.assertRaises(OSError):
                self.edit({"op": "rename", "signal_id": "sample", "name": "lost_update"})
        self.assertEqual(self.store.state(), before)
        self.assertEqual(SessionStore(self.directory).state(), before)
        after = self.edit({"op": "rename", "signal_id": "sample", "name": "saved_update"})
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(SessionStore(self.directory).state(), after)

    def test_simultaneous_writers_cannot_silently_overwrite_each_other(self):
        revision = self.store.state()["revision"]
        barrier = threading.Barrier(2)

        def attempt(name):
            barrier.wait(timeout=5)
            try:
                return self.store.edit(revision, {"op": "rename", "signal_id": "sample", "name": name})
            except ConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ["writer_one", "writer_two"]))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.store.state()["revision"], revision + 1)
        self.assertEqual(SessionStore(self.directory).state(), self.store.state())

    def test_display_reordering_keeps_va_ports_and_waveforms(self):
        before = self.store.state()["project"]
        code = model.compile_va(before)
        state = self.edit({"op": "view", "row_height": 36,
                           "signal_order": ["reference", "sample"]})
        self.assertEqual(state["project"]["signals"], before["signals"])
        self.assertEqual(state["project"]["view"]["signal_order"], ["reference", "sample"])
        self.assertEqual(self.store.va()["code"], code)

    def test_replacement_preserves_metadata_and_supports_undo(self):
        initial = self.store.state()
        replacement = deepcopy(initial["project"])
        replacement["signals"][1]["items"][0]["duration_fs"] = 50
        replacement["signals"][1]["items"][1]["duration_fs"] = 50
        replacement["collaboration"]["manual_exceptions"] = ["sample"]
        state = self.store.replace(initial["revision"], replacement, label="助手推迟参考边沿")
        self.assertEqual(state["project"], replacement)
        self.assertEqual(state["last_change"], "助手推迟参考边沿")
        self.assertEqual(self.edit({"op": "undo"})["project"], initial["project"])

    def test_invalid_replacement_and_non_json_metadata_preserve_project(self):
        before = self.store.state()
        for bad_value in [float("nan"), {"not_json"}]:
            project = deepcopy(before["project"])
            project["collaboration"]["invalid"] = bad_value
            with self.assertRaises((ValueError, TypeError)):
                self.store.replace(before["revision"], project)
            self.assertEqual(self.store.state(), before)

    def test_export_matches_validated_exporter_and_snapshot(self):
        state = self.edit({"op": "settings", "values": {"rise": "2f", "fall": "3f", "cycles": 3}})
        exported = self.store.export(state["revision"])
        self.assertEqual(Path(exported["path"]).read_text("utf-8"), model.compile_va(state["project"]))
        self.assertEqual(json.loads(Path(exported["project_path"]).read_text("utf-8")), state["project"])
        self.assertTrue(Path(exported["path"]).is_relative_to(self.directory / "exports"))
        self.assertEqual(self.store.va()["revision"], state["revision"])
        self.assertEqual(self.store.state(), state)

    def test_period_shortening_only_trims_held_tail(self):
        state = self.edit({"op": "settings", "values": {"period": "70f"}})
        self.assertEqual(spans(state["project"]["signals"][0]),
                         [(0, 10, 0), (10, 20, 1), (20, 30, 0), (30, 40, 1), (40, 70, 0)])
        self.assertEqual(spans(state["project"]["signals"][1]), [(0, 55, 0), (55, 70, 1)])

    def test_add_signal_and_rename_update_view_without_losing_metadata(self):
        state = self.edit({"op": "add_signal", "name": "added_clock", "level": 1})
        signal = state["project"]["signals"][-1]
        self.assertEqual(spans(signal), [(0, 100, 1)])
        self.assertEqual(state["project"]["view"]["signal_order"][-1], signal["id"])
        renamed = self.edit({"op": "rename", "signal_id": signal["id"], "name": "renamed_clock"})
        self.assertEqual(renamed["project"]["signals"][-1]["id"], signal["id"])
        self.assertEqual(renamed["project"]["collaboration"], fixture()["collaboration"])

    def test_fifty_signal_project_survives_save_reopen_and_export(self):
        project = fixture()
        template = deepcopy(project["signals"][0])
        project["signals"] = []
        for index in range(50):
            signal = deepcopy(template)
            signal.update(id=f"s{index}", name=f"clock_{index:02d}")
            for item in signal["items"]:
                item["id"] = f"s{index}_{item['id']}"
            project["signals"].append(signal)
        project["view"]["signal_order"] = [s["id"] for s in reversed(project["signals"])]
        state = self.store.replace(self.store.state()["revision"], project)
        reopened = SessionStore(self.directory)
        self.assertEqual(reopened.state()["project"], project)
        self.assertEqual(reopened.va()["code"], model.compile_va(project))
        self.assertEqual(len(reopened.state()["project"]["signals"]), 50)
        self.assertEqual(state["project"]["view"]["signal_order"][0], "s49")

    def test_demo_and_filtered_relations_use_existing_model_semantics(self):
        demo = make_demo()
        self.assertEqual(model.validate(demo)["errors"], [])
        self.assertEqual(len(demo["signals"]), 6)
        self.assertEqual(demo["period_fs"], model.parse_time("10u"))
        state = self.store.replace(self.store.state()["revision"], demo)
        actual = self.store.relations("200p", signal_id="phi2", kind="near")
        expected = model.analyze_edges(demo, model.parse_time("200p"), limit=200,
                                       signal_id="phi2", relation_kind="near")
        expected["revision"] = state["revision"]
        self.assertEqual(actual, expected)
        self.assertTrue(actual["near"])


class HttpTests(unittest.TestCase):
    def setUp(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="http-", dir=CACHE)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = SessionStore(self.directory / "session", initial=fixture())
        self.token = "test-local-window-token"
        self.server = make_server(self.store, self.token)
        self.server.RequestHandlerClass.log_message = lambda *args: None
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, *, method=None, headers=None, body=None):
        request_headers = {"X-Timing-Token": self.token}
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method or ("POST" if data is not None else "GET"),
                               path, body=body, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            content_type = response.getheader("Content-Type", "")
            result = json.loads(raw) if content_type.startswith("application/json") else raw
            return response.status, result, dict(response.getheaders())
        finally:
            connection.close()

    def test_state_poll_returns_only_unchanged_for_current_revision(self):
        status, state, headers = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state, self.store.state())
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, unchanged, _ = self.request(f"/api/state?since={state['revision']}")
        self.assertEqual(status, 200)
        self.assertEqual(unchanged, {"unchanged": True, "revision": state["revision"]})
        _, stale, _ = self.request(f"/api/state?since={state['revision'] - 1}")
        self.assertEqual(stale, state)

    def test_non_ascii_token_is_rejected_without_closing_connection(self):
        status, error, _ = self.request('/api/state', headers={'X-Timing-Token':'invalid-\u00e9'})
        self.assertEqual(status, 403)
        self.assertIn('error', error)

    def test_edit_and_assistant_replacement_round_trip_with_conflict(self):
        before = self.store.state()
        status, edited, _ = self.request("/api/edit", {
            "revision": before["revision"],
            "operation": {"op": "boundary", "signal_id": "sample", "item_id": "high1", "new_end_fs": 30},
        })
        self.assertEqual(status, 200)
        self.assertEqual(spans(edited["project"]["signals"][0]),
                         [(0, 10, 0), (10, 40, 1), (40, 100, 0)])
        status, conflict, _ = self.request("/api/project", {
            "revision": before["revision"], "project": before["project"],
        })
        self.assertEqual(status, 409)
        self.assertEqual(conflict["state"], edited)
        updated = deepcopy(edited["project"])
        updated["description"] = "保留刚才拖动的结果，再补充说明"
        status, replaced, _ = self.request("/api/project", {
            "revision": edited["revision"], "project": updated, "label": "助手更新说明",
        })
        self.assertEqual(status, 200)
        self.assertEqual(replaced["project"], updated)
        self.assertEqual(replaced["last_change"], "助手更新说明")

    def test_api_export_and_preview_match_same_revision(self):
        state = self.store.state()
        status, preview, _ = self.request("/api/va")
        self.assertEqual(status, 200)
        status, exported, _ = self.request("/api/export", {"revision": state["revision"]})
        self.assertEqual(status, 200)
        self.assertEqual(preview["code"], Path(exported["path"]).read_text("utf-8"))
        self.assertEqual(exported["revision"], preview["revision"])
        self.assertEqual(self.store.state(), state)

    def test_unauthorized_requests_cannot_read_or_edit_project(self):
        before = self.store.state()
        for headers in [{"X-Timing-Token": ""}, {"X-Timing-Token": "wrong"},
                        {"Host": "attacker.invalid"},
                        {"Host": "127.0.0.1"},
                        {"Origin": "https://attacker.invalid"}, {"Origin": "null"}]:
            with self.subTest(headers=headers):
                status, _, _ = self.request("/api/state", headers=headers)
                self.assertEqual(status, 403)
                status, _, _ = self.request("/api/edit", {
                    "revision": before["revision"],
                    "operation": {"op": "rename", "signal_id": "sample", "name": "bad_write"},
                }, headers=headers)
                self.assertEqual(status, 403)
                self.assertEqual(self.store.state(), before)

    def test_same_origin_is_allowed_and_external_bind_is_rejected(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        self.assertEqual(self.request("/api/state", headers={"Origin": origin})[0], 200)
        with self.assertRaises(ValueError):
            make_server(self.store, self.token, host="0.0.0.0")

    def test_bad_json_and_invalid_edits_leave_state_unchanged(self):
        before = self.store.state()
        for body in [b"{", b"[]", b'{"revision":NaN}', b"\xff"]:
            with self.subTest(body=body):
                status, _, _ = self.request("/api/edit", method="POST", body=body,
                    headers={"Content-Type": "application/json"})
                self.assertEqual(status, 400)
                self.assertEqual(self.store.state(), before)
        for operation in [{"op": "unknown"}, {"op": "settings", "values": {"period": "1f"}}]:
            status, error, _ = self.request("/api/edit", {
                "revision": before["revision"], "operation": operation,
            })
            self.assertEqual(status, 400)
            self.assertTrue(error["error"])
            self.assertEqual(self.store.state(), before)

    def test_oversized_and_non_json_requests_are_rejected_before_editing(self):
        status, _, _ = self.request("/api/edit", method="POST", body=b"{}", headers={
            "Content-Type": "application/json", "Content-Length": str(server_module.MAX_BODY + 1),
        })
        self.assertEqual(status, 413)
        status, _, _ = self.request("/api/edit", method="POST", body=b"{}",
                                     headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_static_allowlist_cannot_serve_project_or_parent_files(self):
        static = self.directory / "static"
        static.mkdir()
        (static / "index.html").write_text("<title>Timing test</title>", encoding="utf-8")
        (static / "secret.json").write_text('{"private": true}', encoding="utf-8")
        with patch.object(server_module, "STATIC", static):
            status, body, _ = self.request("/", headers={"X-Timing-Token": ""})
            self.assertEqual(status, 200)
            self.assertEqual(body, b"<title>Timing test</title>")
            for path in ["/secret.json", "/../session/head.json", "/%2e%2e/session/head.json",
                         "/api/../session/head.json", "/session/head.json"]:
                with self.subTest(path=path):
                    self.assertEqual(self.request(path)[0], 404)
            self.assertEqual(self.request("/", headers={"Host": "attacker.invalid"})[0], 403)

    def test_relations_api_validates_input_and_returns_current_revision(self):
        status, result, _ = self.request("/api/relations?threshold=20f&signal_id=sample&kind=near")
        self.assertEqual(status, 200)
        self.assertEqual(result, self.store.relations("20f", "sample", "near"))
        for query in ["threshold=0.1f", "signal_id=missing", "kind=missing"]:
            with self.subTest(query=query):
                self.assertEqual(self.request("/api/relations?" + query)[0], 400)


if __name__ == "__main__":
    unittest.main()
