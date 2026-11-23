"""python -B -m runner --campaign PATH --results PATH [--validate-only]."""
import argparse
import json
from .campaign import execute, load


def main():
    parser = argparse.ArgumentParser(description='Run a bounded synthetic offline campaign (no inference)')
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--results', required=True, help='Directory within the authorized sibling results root')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    try:
        if args.validate_only:
            from pathlib import Path
            from .paths import RESULTS, contained
            load(args.campaign)
            contained(Path(args.results), RESULTS)
            print('VALID: contract and result-root boundary; fixture availability is checked at execution')
            return 0
        run, result = execute(args.campaign, args.results)
        print(json.dumps({'status': result['overall']['status'], 'directory': str(run)}))
        return {'PASS': 0, 'FAIL': 1, 'BLOCKED': 3, 'NOT_RUN': 4, 'SKIP': 4}[result['overall']['status']]
    except (ValueError, OSError, UnicodeError) as error:
        print('ERROR: ' + str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
