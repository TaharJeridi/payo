"""Targeted mutation testing for the evaluation scorer (TAH-145) and the claim checks (TAH-143).

Each mutant replaces the first occurrence of one code fragment in one source file; the given tests must fail.
The source file is restored after every mutant and at the end, also on error. Needs the test database (as pytest).
From ``backend/``:

    python ../evaluation/mutation/mutate.py app/rag/claims.py tests/test_verified_claims.py \\
        ../evaluation/mutation/claims.json

Spec files hold ``[name, original fragment, mutated fragment]`` triples. Mutants run in 2026-11-19: scoring 22,
runner 6, metrics 1 (TAH-145); claims 17, service 3, scope 3, structured 2, parser 1, documents 1 (TAH-143);
all killed.
"""

import json
import subprocess
import sys
from pathlib import Path


def main(target: Path, tests: list[str], spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    original = target.read_text(encoding="utf-8")
    survivors = []
    try:
        for name, old, new in spec:
            if old not in original:
                raise SystemExit(f"{name}: fragment not found in {target}")
            target.write_text(original.replace(old, new, 1), encoding="utf-8")
            command = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests]
            result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
            if result.returncode == 0:
                survivors.append(name)
            print(("KILLED   " if result.returncode else "SURVIVED ") + name, flush=True)
    finally:
        target.write_text(original, encoding="utf-8")
    print(f"{len(spec) - len(survivors)}/{len(spec)} killed; survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), sys.argv[2:-1], Path(sys.argv[-1])))
