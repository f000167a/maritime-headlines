"""関心度で選別し、鮮度は順位だけに使う。"""

import json
from pathlib import Path
import re

from .models import normalized_title, timestamp


def load_config(path=None):
    path = Path(path) if path else Path(__file__).parent / "config/scoring.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    if config["retain_days"] <= 0 or config["threshold"] < 0:
        raise ValueError("保持日数と閾値を確認してください")
    return config


def matches(title, keyword):
    term = normalized_title(keyword)
    if term.isascii():
        # Avoid MOL in demolition, FFA in offal, etc.; allow bulker/bulkers.
        suffix = "s?" if term in {"bulker", "newbuilding"} else ""
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + suffix + r"(?![a-z0-9])", title) is not None
    return term in title


def score_article(article, config, now):
    result = dict(article)
    title = normalized_title(article["title"])
    base = config["category_priority"].get(article["cat"], config["default_category_score"])
    matched = []
    for group in config["keyword_groups"]:
        points = [pts for keyword, pts in group["keywords"].items() if matches(title, keyword)]
        if points:
            matched.append((group["name"], max(points)))
    content = min(base + sum(pts for _, pts in matched), config["max_content_score"])
    # Old articles first discovered today are not current news.
    anchor = (timestamp(article["content_updated_at"]).date() if not article.get("published_date")
              else timestamp(article["published_date"] + "T00:00:00+09:00").date())
    days = (now.date() - anchor).days
    recency = config["recency_bonus"].get(str(days), 0)
    result.update(content_score=content, recency_bonus=recency, rank_score=content + recency,
                  is_hot=content >= config["threshold"], matched_groups=matched)
    return result
