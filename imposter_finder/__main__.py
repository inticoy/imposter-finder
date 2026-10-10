from __future__ import annotations

import argparse

from imposter_finder.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="범인찾기: 친구전 리포트")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect", help="PUBG·FC·롤을 한 번 수집·리포트하고 끝낸다 (GitHub Actions)")
    subparsers.add_parser("serve", help="/user 슬래시 명령 봇 + 15분 수집 루프 (상주)")
    subparsers.add_parser("bot", help="/user 슬래시 명령 봇만")

    args = parser.parse_args()
    if args.command == "collect":
        from imposter_finder.once import run_once

        return run_once(load_settings())

    from imposter_finder.bot import run_discord_bot

    run_discord_bot(load_settings(), serve=args.command == "serve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
