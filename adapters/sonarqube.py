import json


def main(argv=None, fetch=None):
    with open(argv[argv.index("--out") + 1], "w") as handle:
        json.dump({"findings": []}, handle)
    return 0
