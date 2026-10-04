"""BI-01/RES-006 元数据链诊断工具测试：结局分类口径、事件归因、链级计时。

全部使用假 provider/假链路注入，不触网络、不依赖后端导入。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import diagnose_metadata_chain as dmc  # noqa: E402


@dataclass
class FakeMeta:
    title: str | None = "翦商"
    source: str = "nlc"


@dataclass
class FakeProvider:
    meta: object = None                     # 返回值（None=无结果）
    events: list = field(default_factory=list)  # 探测期间伪造的底层请求事件
    error: Exception | None = None          # 抛出的异常

    def fetch_by_isbn(self, isbn: str):
        return self._call()

    def search(self, title: str, author: str | None = None):
        return self._call()

    def _call(self):
        # 事件由 listener 收集：诊断在 provider 调用前注册 collector，
        # 这里模拟底层 http 请求结束时的通知路径——直接走注入的 listener。
        dmc_events.fire(self.events)
        if self.error:
            raise self.error
        return self.meta


class _Events:
    """把假 provider 的事件送进当前注册的 listener（模拟 http.py 通知路径）。"""

    def __init__(self):
        self.current: list | None = None

    def install(self, fn):
        self.current = fn

    def clear(self):
        self.current = None

    def fire(self, events):
        if self.current is not None:
            for e in events:
                self.current(e)


dmc_events = _Events()


def make_deps(providers: dict) -> dict:
    return {
        "providers": providers,
        "chain_fn": lambda **kw: None,
        "add_listener": dmc_events.install,
        "clear_listeners": dmc_events.clear,
    }


PROBE_ISBN = {"label": "t", "kind": "isbn", "value": "9787536692930", "expect_title": "三体"}


# ── 结局分类口径 ──

def test_classify_ok_and_suspect():
    assert dmc.classify_provider_outcome(FakeMeta(title="三体"), [], None, PROBE_ISBN) == "ok"
    # 期望中文书名，返回无中文字符的书名 → 错误元数据（英文错题）
    assert dmc.classify_provider_outcome(
        FakeMeta(title="The Three-Body Problem"), [], None, PROBE_ISBN) == "suspect_metadata"
    # 字符集完全无交集
    assert dmc.classify_provider_outcome(FakeMeta(title="ZZZ"), [], None, PROBE_ISBN) == "suspect_metadata"
    # 无 expect_title 时不做怀疑判断
    assert dmc.classify_provider_outcome(
        FakeMeta(title="Anything"), [], None, {"kind": "isbn", "value": "x"}) == "ok"


def test_classify_no_result_vs_network_vs_timeout_vs_ratelimit():
    p = {"kind": "isbn", "value": "x"}
    assert dmc.classify_provider_outcome(None, [], None, p) == "no_result"
    assert dmc.classify_provider_outcome(
        None, [{"status": 200, "error": None}], None, p) == "no_result"
    assert dmc.classify_provider_outcome(
        None, [{"status": None, "error": "URLError: connection refused"}], None, p) == "network_error"
    assert dmc.classify_provider_outcome(
        None, [{"status": None, "error": "TimeoutError: timed out"}], None, p) == "timeout"
    # 429 优先于网络错误归类
    assert dmc.classify_provider_outcome(
        None, [{"status": 429, "error": "HTTPError: Too Many Requests"}], None, p) == "rate_limited"
    assert dmc.classify_provider_outcome(None, [], "ValueError: boom", p) == "provider_error"


# ── 诊断主流程（注入假对象）──

def test_run_diagnosis_classifies_each_provider():
    providers = {
        "nlc": FakeProvider(meta=FakeMeta(title="三体", source="nlc")),
        "google_books": FakeProvider(meta=None, events=[{"kind": "json", "url": "u",
                                                         "status": None, "elapsed_ms": 1.0,
                                                         "error": "URLError: refused"}]),
        "openlibrary": FakeProvider(meta=FakeMeta(title="Revelation", source="openlibrary")),
    }
    report = dmc.run_diagnosis([PROBE_ISBN], **make_deps(providers))

    row = report["probes"][0]
    assert row["providers"]["nlc"]["outcome"] == "ok"
    assert row["providers"]["google_books"]["outcome"] == "network_error"
    assert row["providers"]["google_books"]["requests"][0]["status"] is None
    assert row["providers"]["openlibrary"]["outcome"] == "suspect_metadata"
    # 汇总按 provider × outcome 聚合
    agg = report["summary"]["by_provider_outcome"]
    assert agg["nlc"] == {"ok": 1}
    assert agg["google_books"] == {"network_error": 1}
    assert report["summary"]["total_probes"] == 1


def test_run_diagnosis_title_probe_uses_search_with_author():
    calls: list[tuple] = []

    class Recording(FakeProvider):
        def search(self, title, author=None):
            calls.append((title, author))
            return FakeMeta(title="翦商", source="nlc")

    report = dmc.run_diagnosis(
        [{"label": "翦商", "kind": "title", "value": "翦商", "author": "李硕",
          "expect_title": "翦商"}],
        **make_deps({"nlc": Recording()}))
    assert calls == [("翦商", "李硕")]
    assert report["probes"][0]["providers"]["nlc"]["outcome"] == "ok"


def test_run_diagnosis_chain_records_source_and_deadline_hit():
    def chain(**kw):
        return FakeMeta(title="三体", source="google_books")

    deps = make_deps({"nlc": FakeProvider()})
    deps["chain_fn"] = chain
    deps["chain_deadline_sec"] = 12.0
    report = dmc.run_diagnosis([PROBE_ISBN], **deps)
    chain_row = report["probes"][0]["chain"]
    assert chain_row["outcome"] == "ok" and chain_row["source"] == "google_books"
    assert chain_row["deadline_hit"] is False

    # 用 0 秒期限模拟触达总时限
    deps["chain_deadline_sec"] = 0.0
    report2 = dmc.run_diagnosis([PROBE_ISBN], **deps)
    assert report2["probes"][0]["chain"]["deadline_hit"] is True


def test_run_diagnosis_chain_exception_recorded_not_raised():
    def boom(**kw):
        raise RuntimeError("炸了")

    deps = make_deps({"nlc": FakeProvider()})
    deps["chain_fn"] = boom
    report = dmc.run_diagnosis([PROBE_ISBN], **deps)
    chain_row = report["probes"][0]["chain"]
    assert chain_row["outcome"] == "provider_error"
    assert "炸了" in chain_row["error"]


def test_provider_exception_recorded_as_provider_error():
    providers = {"nlc": FakeProvider(error=ValueError("解析失败"))}
    report = dmc.run_diagnosis([PROBE_ISBN], **make_deps(providers))
    assert report["probes"][0]["providers"]["nlc"]["outcome"] == "provider_error"
    assert "解析失败" in report["probes"][0]["providers"]["nlc"]["error"]


# ── 探针文件校验 ──

def test_default_probes_shape():
    for probe in dmc.DEFAULT_PROBES:
        assert probe["kind"] in ("isbn", "title")
        assert probe["value"]
        assert "label" in probe
