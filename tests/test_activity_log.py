"""The web app's activity log: filtering, paging, stats, export, the logging bridge and its routes."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dreame_mcp.activity_log import ActivityLog, ActivityLogHandler, create_log_router, install_log_handler


@pytest.fixture
def log() -> ActivityLog:
    log = ActivityLog()
    log.add("info", "connected to the cloud", kind="server")
    log.add("warning", "Map is slow", kind="map")
    log.add("", "nothing special", kind="server")
    return log


def test_a_level_is_kept_in_capitals_and_defaults_to_info(log):
    assert [e["level"] for e in log.query(sort="asc")["entries"]] == ["INFO", "WARNING", "INFO"]


def test_the_oldest_entry_drops_out_when_the_log_is_full():
    small = ActivityLog(max_entries=2)
    for n in range(3):
        small.add("INFO", f"entry {n}")
    assert [e["detail"] for e in small.query(sort="asc")["entries"]] == ["entry 1", "entry 2"]


def test_newest_first_by_default(log):
    assert log.query()["entries"][0]["detail"] == "nothing special"


def test_filters_by_level_kind_and_words_in_any_case(log):
    assert log.query(level="INFO")["total"] == 2
    assert log.query(kind="map")["total"] == 1
    assert [e["detail"] for e in log.query(search="MAP")["entries"]] == ["Map is slow"]


def test_after_an_id_gives_only_later_entries(log):
    first = log.query(sort="asc")["entries"][0]["id"]
    assert [e["detail"] for e in log.query(after_id=first, sort="asc")["entries"]] == [
        "Map is slow",
        "nothing special",
    ]


def test_an_unknown_after_id_gives_everything(log):
    assert log.query(after_id="nosuchid")["total"] == 3


def test_paging_keeps_the_total(log):
    page = log.query(limit=1, offset=1, sort="asc")
    assert page["total"] == 3
    assert [e["detail"] for e in page["entries"]] == ["Map is slow"]


def test_stats_count_by_level_and_kind(log):
    stats = log.stats()
    assert stats["total"] == 3
    assert stats["max_entries"] == 5000
    assert stats["by_level"] == {"INFO": 2, "WARNING": 1}
    assert stats["by_kind"] == {"server": 2, "map": 1}
    assert stats["oldest"] <= stats["newest"]


def test_stats_of_an_empty_log():
    stats = ActivityLog().stats()
    assert (stats["total"], stats["oldest"], stats["newest"]) == (0, None, None)


def test_clear_empties_the_log(log):
    log.clear()
    assert log.query()["total"] == 0


def test_csv_export_is_filtered(log):
    rows = log.export_csv(level="INFO", kind="server", search="connected").splitlines()
    assert rows[0] == "id,timestamp,level,kind,detail"
    assert len(rows) == 2 and rows[1].endswith(",INFO,server,connected to the cloud")


def test_json_export_is_filtered(log):
    items = json.loads(log.export_json(level="WARNING", kind="map", search="slow"))
    assert [e["detail"] for e in items] == ["Map is slow"]


# -- the bridge from Python logging --------------------------------------------


def _record(name: str, level: int, msg: str) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


def test_the_handler_copies_a_log_record_into_the_log():
    log = ActivityLog()
    ActivityLogHandler(log).emit(_record("dreame-mcp", logging.WARNING, "cloud slow"))
    (entry,) = log.query()["entries"]
    assert (entry["level"], entry["kind"], entry["detail"]) == ("WARNING", "server", "cloud slow")
    assert entry["meta"]["logger"] == "dreame-mcp"


def test_warn_is_said_as_warning():
    log = ActivityLog()
    record = _record("x", logging.WARNING, "old style")
    record.levelname = "WARN"
    ActivityLogHandler(log).emit(record)
    assert log.query()["entries"][0]["level"] == "WARNING"


def test_ordinary_web_requests_are_left_out():
    log = ActivityLog()
    handler = ActivityLogHandler(log)
    handler.emit(_record("uvicorn.access", logging.INFO, "GET /api/status 200"))
    handler.emit(_record("uvicorn.access", logging.WARNING, "GET /api/status 500"))
    assert [e["detail"] for e in log.query()["entries"]] == ["GET /api/status 500"]


def test_a_record_that_cannot_be_read_is_handed_to_logging(monkeypatch):
    handler = ActivityLogHandler(ActivityLog())
    failed = []
    monkeypatch.setattr(handler, "handleError", failed.append)
    bad = _record("x", logging.INFO, "%d")
    bad.args = ("not a number",)
    handler.emit(bad)
    assert failed == [bad]


def test_installing_the_handler_twice_adds_it_once():
    log = ActivityLog()
    root = logging.getLogger()
    try:
        first = install_log_handler(log)
        assert install_log_handler(log) is first
        assert sum(h is first for h in root.handlers) == 1
    finally:
        root.removeHandler(first)


def test_a_handler_already_on_the_package_logger_is_reused():
    log = ActivityLog()
    package = logging.getLogger("dreame-mcp")
    mine = ActivityLogHandler(log)
    package.addHandler(mine)
    try:
        assert install_log_handler(log) is mine
    finally:
        package.removeHandler(mine)


# -- the web routes ------------------------------------------------------------


@pytest.fixture
def web(log) -> TestClient:
    app = FastAPI()
    app.include_router(create_log_router(log), prefix="/api")
    return TestClient(app)


def test_the_logs_route_filters(web):
    assert web.get("/api/logs", params={"kind": "map"}).json()["total"] == 1


def test_the_stats_route(web):
    assert web.get("/api/logs/stats").json()["total"] == 3


def test_clearing_by_the_route_leaves_a_note_of_it(web):
    assert web.delete("/api/logs").json() == {"success": True}
    (entry,) = web.get("/api/logs").json()["entries"]
    assert (entry["level"], entry["detail"]) == ("WARNING", "Log buffer cleared")


def test_export_as_csv_or_json(web):
    csv = web.get("/api/logs/export", params={"format": "csv"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert "logs.csv" in csv.headers["content-disposition"]
    js = web.get("/api/logs/export")
    assert js.headers["content-type"] == "application/json"
    assert len(js.json()) == 3
