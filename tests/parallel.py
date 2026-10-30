#!/usr/bin/env python3
"""Run the test suite with one worker process per CPU.

Each test class runs whole in one worker, so setUpClass and class-level
fixtures behave as under "python3 -m unittest discover -s tests". The
classes go out largest first. Usage, from the repository root:

    python3 tests/parallel.py [-j N] [name ...]

Names, if given, select test modules or classes by prefix (for example
test_ge_install or test_audi_connect.FairMode). Exit status 0 when every
test passed.
"""
import argparse
import io
import multiprocessing
import os
from pathlib import Path
import sys
import time
import unittest


TESTS = Path(__file__).resolve().parent


def classes(names):
    """(id, suite) per test class, in discovery order."""
    found = {}

    def walk(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                walk(item)
            else:
                key = '%s.%s' % (type(item).__module__, type(item).__qualname__)
                found.setdefault(key, unittest.TestSuite()).addTest(item)

    walk(unittest.defaultTestLoader.discover(str(TESTS), top_level_dir=str(TESTS)))
    return [(k, s) for k, s in found.items()
            if not names or any(k.startswith(n) for n in names)]


def run(job):
    key, suite = job
    out = io.StringIO()
    start = time.time()
    result = unittest.TextTestRunner(stream=out, verbosity=0).run(suite)
    report = ''.join('%s: %s\n%s\n' % (kind, test.id(), text)
                     for kind, items in (('FAIL', result.failures), ('ERROR', result.errors),
                                         ('UNEXPECTED SUCCESS',
                                          [(t, '') for t in result.unexpectedSuccesses]))
                     for test, text in items)
    return (key, result.testsRun, len(result.failures) + len(result.errors)
            + len(result.unexpectedSuccesses), len(result.skipped), time.time() - start, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('-j', type=int, default=os.cpu_count() or 1, help='worker processes')
    parser.add_argument('names', nargs='*')
    args = parser.parse_args()
    os.chdir(TESTS.parent)
    sys.path.insert(0, str(TESTS))
    jobs = sorted(classes(args.names), key=lambda j: -j[1].countTestCases())
    if not jobs:
        sys.exit('no tests match')
    start = time.time()
    total = failed = skipped = 0
    slow = []
    with multiprocessing.get_context('fork').Pool(args.j) as pool:
        for key, ran, bad, skip, took, report in pool.imap_unordered(run, jobs):
            total, failed, skipped = total + ran, failed + bad, skipped + skip
            slow.append((took, key))
            if report:
                print(report, end='')
    slow.sort(reverse=True)
    print('slowest classes: ' + ', '.join('%s %.1fs' % (k, t) for t, k in slow[:3]))
    print('Ran %d tests in %.1fs with %d workers: %s%s' % (
        total, time.time() - start, args.j, 'FAILED (%d)' % failed if failed else 'OK',
        ', %d skipped' % skipped if skipped else ''))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
