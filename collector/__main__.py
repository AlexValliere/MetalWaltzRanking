"""CLI credentials are read only from the two documented environment variables."""

import argparse
import os

from .collect import error_code, log, run_collection
from .configuration import load_masks, load_servers
from .timing import DEFAULT_MAX_DURATION_SECONDS, positive_duration


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Metal Waltz ranking collector")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="Refresh complete server-exposed Grandeur rankings")
    collect.add_argument("--output", default="site/data", help="Public data directory (default: site/data)")
    collect.add_argument("--server", default="all", help="One reviewed server key/alias, or all (default)")
    collect.add_argument("--max-duration-seconds", type=positive_duration, default=DEFAULT_MAX_DURATION_SECONDS,
                         help="Whole-run collection budget in seconds (default: 9000 / 150 minutes)")
    args = parser.parse_args(argv)
    try:
        servers, masks = load_servers(), load_masks()
        aliases = {alias: item["key"] for item in servers for alias in item["aliases"]}
        if args.server != "all" and args.server not in aliases:
            parser.error("Unknown reviewed server key/alias")
        selected = {item["key"] for item in servers} if args.server == "all" else {aliases[args.server]}
        username = os.environ.get("MW_COLLECTOR_USERNAME")
        password = os.environ.get("MW_COLLECTOR_PASSWORD")
        credentials = (username, password) if username and password else None
        try:
            return run_collection(args.output, servers, selected, credentials, masks,
                                  max_duration_seconds=args.max_duration_seconds)
        finally:
            credentials = username = password = None
    except Exception as error:
        log(phase="failed", errorCode=error_code(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
