"""Fetch, validate, merge and render without publishing false success."""

from datetime import datetime
import logging
from pathlib import Path

from .models import FetchResult, JST, SOURCES
from .render import render, write_page
from .scoring import load_config
from .storage import load_state, merge_results, save_state

LOG = logging.getLogger(__name__)


def run(state_path, output_dir, status_path, *, legacy_path=None, config_path=None,
        fetchers=None, now=None, render_only=False):
    now = now or datetime.now(JST)
    config = load_config(config_path)
    existing = Path(state_path)
    source_path = existing if existing.exists() else Path(legacy_path) if legacy_path else existing
    state = load_state(source_path)
    if render_only:
        write_page(output_dir, render(state, config, now, preview=True))
        return 0
    if fetchers is None:
        from .sources import FETCHERS
        fetchers = FETCHERS
    results = []
    for key, fetch in fetchers.items():
        try:
            result = fetch(now)
            if result.source != key:
                raise ValueError("取得結果の媒体IDが一致しません")
        except Exception as exc:
            LOG.exception("%s 取得失敗", SOURCES[key])
            result = FetchResult(key, error=str(exc))
        results.append(result)
        LOG.info("%s: %s (%d件)", SOURCES[key], "成功" if result.ok else "失敗", len(result.articles))
    merged = merge_results(state, results, now, config["retain_days"])
    report = {"attempted_at": now.isoformat(), "ok": any(r.ok for r in results),
              "sources": merged["sources"]}
    save_state(status_path, report)
    if not report["ok"]:
        LOG.error("全媒体で取得失敗。記事状態と公開ページを更新しません")
        return 1
    # Complete rendering before replacing any previous successful output.
    page = render(merged, config, now)
    write_page(output_dir, page)
    save_state(state_path, merged)
    return 0
