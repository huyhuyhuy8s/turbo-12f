import json
import os
import sys


def main(argv=None, run=None):
    def option(flag):
        return argv[argv.index(flag) + 1]

    job = json.loads(option("--job"))
    commit = option("--commit")
    last = os.path.join(option("--home"), "jobs", job["name"], "cubic-last.json")
    if os.path.exists(last):
        with open(last) as handle:
            recorded = json.load(handle).get("commit")
        if recorded == commit:
            with open(option("--out"), "w") as handle:
                json.dump({"findings": []}, handle)
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
