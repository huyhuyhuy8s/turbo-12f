#!/usr/bin/env python3


def main(argv=None, fetch=None):
    out = argv[argv.index("--out") + 1]
    with open(out, "w") as handle:
        handle.write('{"findings": []}')
    return 0
