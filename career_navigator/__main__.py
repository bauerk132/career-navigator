"""python -m career_navigator             -> print the synthetic demo report
python -m career_navigator export-state OUT [--state-dir DIR]
                                       -> write the saved logs to OUT as JSONL
"""
import argparse
import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        from .demo import render_report

        print(render_report())
        return 0
    parser = argparse.ArgumentParser(prog="python -m career_navigator")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export-state", help="write the saved logs out as JSONL")
    export.add_argument("out_dir")
    export.add_argument("--state-dir", default=None)
    args = parser.parse_args(argv)

    from .state_store import StateError, resolve_state_dir
    from .state_tools import export_state

    state_dir = resolve_state_dir(args.state_dir, live=True)
    try:
        counts = export_state(state_dir, args.out_dir)
    except StateError as exc:
        print(f"export-state failed: {exc}", file=sys.stderr)
        return 1
    for name, count in counts.items():
        print(f"{name}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
