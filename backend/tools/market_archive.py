"""Read-only local archive status/reconstruction; never starts a worker."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--root', required=True, help='Explicit TOPSIGNAL_DATABENTO_CACHE_DIR')
    parser.add_argument('--decision', help='Exact committed database decision ID')
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()
    from app.services.market_archive import Archive, status
    result = Archive(args.owner, args.root).reconstruct(args.decision) if args.decision else status(args.owner, root=args.root, days=args.days)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
