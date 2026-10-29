import argparse
import json
import os
from typing import List

from app import run_scan


def load_values(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk scan many values with Entity Intelligence")
    parser.add_argument("--input", required=True, help="Path to a text file with one value per line")
    parser.add_argument("--output", required=True, help="Path to save JSON output")
    parser.add_argument("--category", default="all", choices=["all", "phone", "email", "domain", "url", "number"], help="Category override")
    parser.add_argument("--aggressive", action="store_true", help="Use more aggressive search patterns")
    args = parser.parse_args()

    entries = load_values(args.input)
    results = []
    for value in entries:
        try:
            result = run_scan(value, category=args.category, aggressive=args.aggressive)
            results.append({
                "input": value,
                "type": result.get("type"),
                "risk": result.get("risk"),
                "likely_owner": result.get("likely_owner", []),
                "likely_usage": result.get("likely_usage", []),
                "search_results": result.get("search_results", [])[:5],
            })
        except Exception as exc:  # pragma: no cover
            results.append({"input": value, "error": str(exc)})

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(results)} results to {args.output}")


if __name__ == "__main__":
    main()
