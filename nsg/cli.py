"""Command-line interface for the Neural Semantic Graph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nsg.config import NSGConfig
from nsg.graph import NeuralSemanticGraph
from nsg import persistence


def cmd_index(args: argparse.Namespace) -> None:
    """Read a JSONL file of documents and build a persisted graph."""
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    nsg = NeuralSemanticGraph(config=NSGConfig())

    with open(input_path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"Warning: skipping line {lineno} (invalid JSON): {exc}", file=sys.stderr)
                continue
            doc_id = doc.get("doc_id", f"doc_{lineno}")
            text = doc.get("text", "")
            if not text:
                continue
            print(f"  Indexing {doc_id} …")
            nsg.add_document(doc_id, text)

    nsg.build_or_update_index()

    output_path = Path(args.output)
    persistence.save(nsg, output_path)
    print(
        f"Graph saved to {output_path}/ ({nsg.graph.number_of_nodes()} nodes, {nsg.graph.number_of_edges()} edges)"
    )


def cmd_query(args: argparse.Namespace) -> None:
    """Load a persisted graph and query it."""
    graph_path = Path(args.graph)
    if not graph_path.exists():
        print(f"Error: graph directory not found: {graph_path}", file=sys.stderr)
        sys.exit(1)

    nsg = persistence.load(graph_path)
    result = nsg.query_subgraph(args.q, top_k=args.top_k, hops=args.hops)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nsg", description="Neural Semantic Graph CLI")
    sub = parser.add_subparsers(dest="command")

    # --- index ---
    p_index = sub.add_parser("index", help="Index documents into a graph")
    p_index.add_argument("--input", required=True, help="Path to a docs.jsonl file")
    p_index.add_argument(
        "--output", default="nsg_output", help="Output directory (default: nsg_output)"
    )

    # --- query ---
    p_query = sub.add_parser("query", help="Query the graph")
    p_query.add_argument("--graph", required=True, help="Path to the saved graph directory")
    p_query.add_argument("--q", required=True, help="Query string")
    p_query.add_argument("--top-k", type=int, default=12, help="Number of seed concepts")
    p_query.add_argument("--hops", type=int, default=2, help="Expansion hops")

    args = parser.parse_args(argv)

    if args.command == "index":
        cmd_index(args)
    elif args.command == "query":
        cmd_query(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
