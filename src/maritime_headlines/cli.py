import argparse
import logging

from .pipeline import run


def main():
    parser = argparse.ArgumentParser(description="海事ニュース取得とHTML生成")
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--legacy-state", default="data/legacy_seen_articles.json")
    parser.add_argument("--output", default="dist")
    parser.add_argument("--status", default="data/run-status.json")
    parser.add_argument("--config")
    parser.add_argument("--render-only", action="store_true", help="保存記事だけでプレビュー（通信・履歴更新なし）")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return run(args.state, args.output, args.status, legacy_path=args.legacy_state,
                   config_path=args.config, render_only=args.render_only)
    except Exception:
        logging.exception("処理を停止しました。前回データとActionsログを確認してください")
        return 1
