"""CLI entry point for sal."""
import argparse
import json
import sys
from pathlib import Path

import anthropic

import sal.core as core


def _cmd_init():
    core.WS = Path.cwd()
    client = anthropic.Anthropic(api_key=core.get_api_key(), max_retries=5)
    cards = core.ensure_indexed(client)
    print(f"\n{len(cards)} document(s) indexed in {core.WS}")


def _cmd_ls():
    index_dir = Path.cwd() / ".sal" / "index"
    if not index_dir.exists():
        sys.exit("Not indexed. Run: sal init")
    cards = [json.loads(p.read_text()) for p in sorted(index_dir.glob("*.json"))]
    if not cards:
        sys.exit("No documents indexed. Run: sal init")
    print(f"\n{len(cards)} document(s) in {Path.cwd()}\n")
    for c in cards:
        print(f"  {c.get('title', c.get('path', '?'))}  ({c.get('type', '?')})")
        print(f"  path: {c.get('path', '?')}")
        print(f"  topics: {', '.join(c.get('topics', []))}")
        if c.get("key_terms"):
            print(f"  key terms: {', '.join(c['key_terms'])}")
        if c.get("prerequisites"):
            print(f"  prerequisites: {', '.join(c['prerequisites'])}")
        if c.get("key_results"):
            print(f"  key results: {', '.join(c['key_results'])}")
        if c.get("summary"):
            print(f"  {c['summary']}")
        print()


def _cmd_serve(args):
    core.WS = Path(args.resources).resolve() if args.resources else Path.cwd()
    client = anthropic.Anthropic(api_key=core.get_api_key(), max_retries=5)
    core.CARDS[:] = core.ensure_indexed(client)
    from sal.web import app
    print(f"\nServing {len(core.CARDS)} document(s) at http://localhost:{args.port}")
    app.run(host="localhost", port=args.port)


def main():
    parser = argparse.ArgumentParser(prog="sal")
    parser.add_argument("--resources", help="Resources directory (MCP server mode)")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init", help="Index documents in current directory")
    subparsers.add_parser("ls", help="List indexed documents in current directory")
    serve_parser = subparsers.add_parser("serve", help="Start web UI")
    serve_parser.add_argument("--port", type=int, default=8888, help="Port (default: 8888)")
    serve_parser.add_argument("--resources", dest="serve_resources",
                              help="Resources directory (default: current dir)")
    args = parser.parse_args()

    if args.command == "init":
        _cmd_init()
    elif args.command == "ls":
        _cmd_ls()
    elif args.command == "serve":
        # let serve use its own --resources flag, falling back to top-level
        if args.serve_resources:
            args.resources = args.serve_resources
        _cmd_serve(args)
    else:
        if not args.resources:
            parser.print_help()
            sys.exit(1)
        core.WS = Path(args.resources).resolve()
        client = anthropic.Anthropic(api_key=core.get_api_key(), max_retries=5)
        core.CARDS[:] = core.ensure_indexed(client)
        from sal.mcp import mcp
        mcp.run()
