"""CLI for local in-memory C1 simulation; never connects to AWS."""
import argparse
import json
from pathlib import Path
from c1_plan import load_json, simulate, simulate_recovery, start_simulation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'run', 'recover'))
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--observation', type=Path)
    parser.add_argument('--assume-deletion-grace', action='store_true',
                        help='Model a hypothetical cache/dependency review; never proof of a real review')
    args = parser.parse_args()
    if args.action == 'start':
        if not args.plan or not args.observation:
            parser.error('start requires --plan and --observation')
        journal = start_simulation(load_json(args.plan), load_json(args.observation), args.journal)
    elif args.action == 'run':
        journal = simulate(args.journal, assume_deletion_grace=args.assume_deletion_grace)
    else:
        journal = simulate_recovery(args.journal)
    print(json.dumps({'kind': journal['kind'], 'state': journal['state'],
                      'applyBlocked': True, 'journal': str(args.journal)}))


if __name__ == '__main__':
    main()
