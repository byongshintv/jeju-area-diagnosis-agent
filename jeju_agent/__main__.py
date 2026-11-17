"""`python -m jeju_agent` 진입점."""
import argparse
import sys

from .config import build_config
from .server import serve_forever


def main(argv=None):
    parser = argparse.ArgumentParser(description="제주 동네 상권 진단 Agent (로컬 웹앱)")
    parser.add_argument("--host", default=None, help="기본 127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="기본 8765")
    parser.add_argument("--no-browser", action="store_true", help="브라우저를 열지 않는다")
    args = parser.parse_args(argv)

    overrides = {}
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    if args.no_browser:
        overrides["open_browser"] = False
    cfg = build_config(overrides or None)
    return serve_forever(cfg)


if __name__ == "__main__":
    sys.exit(main())
