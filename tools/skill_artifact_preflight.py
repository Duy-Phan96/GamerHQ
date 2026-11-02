"""Verify a locally staged, reviewed Skill wheel without executing it."""
import argparse
import json
from pathlib import Path

from hosts.gamerhq.skill_artifacts import verify_wheel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    result = verify_wheel(args.wheel, distribution=args.distribution, version=args.version, sha256=args.sha256)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
