#!/usr/bin/env python3
"""Given a change, say what verification it requires — and be able to answer NO.

The doctrine already describes criticality tiers A, B and C, and Tier C already
lists what "proven" means: transactional guarantees, concurrency stress against a
real database, invariant and property tests, threat model, failure injection,
documented recovery, immutable audit, observability. What has never existed is
anything that *applies* it. "release gate" and "definition of done" appear ZERO
times in 2,586 lines of canonical doctrine, so the tiers have been prose that a
tired human or a confident model can skip without anything noticing.

  WHY THIS USES TIERS A/B/C AND NOT A NEW SCHEME
  A four-level 0-3 model was proposed. Adopting it would have left the portfolio
  with two rival risk vocabularies, and `rule-precedence.md` is explicit that a
  rule which quietly loses every time is worse than no rule. The existing scheme
  wins; this is its enforcement, not its replacement.

  WHAT IT REFUSES TO DO
  It never converts "not run" into "pass". A required check with no evidence is
  INSUFFICIENT_EVIDENCE, which is a distinct verdict from PASS and from BLOCK,
  because "we did not look" and "we looked and it was fine" are different claims
  and only one of them is a release decision.

    release_gate.py classify --changed a.py b.md      # tier + required checks
    release_gate.py verify --changed a.py --evidence e.json
    release_gate.py --selftest

Exit: 0 PASS · 1 BLOCK · 2 INSUFFICIENT_EVIDENCE · 3 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

PASS, BLOCK, INSUFFICIENT = "PASS", "BLOCK", "INSUFFICIENT_EVIDENCE"
EXIT = {PASS: 0, BLOCK: 1, INSUFFICIENT: 2}

# Tier C is defined by CONSEQUENCE, not by directory. These patterns name the
# things doctrine already calls critical invariants: money, authority, identity,
# tenant isolation and evidence. A path matching any of them is Tier C whatever
# else it looks like.
TIER_C = (
    r"budget", r"mandate", r"\bpolic(y|ies)\b", r"approval", r"authoris|authoriz",
    r"identity", r"tenant", r"evidence", r"audit", r"ledger", r"settle",
    r"payment", r"credential", r"secret", r"\bauth\b", r"reconcil", r"idempot",
    # 2026-11-03: the Control Plane's action gateway — the boundary where a clearance
    # becomes an irreversible effect — classified as Tier B because its path named
    # none of the above. The place authority is *spent* is as critical as where it
    # is decided.
    r"gateway", r"clearance", r"connector",
)
# Tier A carries no runtime consequence on its own.
TIER_A = (r"\.md$", r"^docs/", r"^README", r"\.txt$", r"LICENSE")

# What each tier must show. Tier C inherits B, which inherits A: a critical
# change does not escape the cheaper checks by being important.
REQUIRED = {
    "A": ["lint"],
    "B": ["lint", "unit", "integration"],
    "C": ["lint", "unit", "integration", "concurrency", "invariant",
          "failure_injection", "recovery", "audit_evidence", "observability"],
}


def classify(paths):
    """Return (tier, reasons). Highest matching tier wins; ties never downgrade."""
    if not paths:
        raise Usage("no changed paths given — an empty change set is not a Tier A change")
    reasons, tier = [], "A"
    for path in paths:
        low = path.lower()
        hit = next((p for p in TIER_C if re.search(p, low)), None)
        if hit:
            reasons.append(f"{path}: Tier C, matches /{hit}/ (money, authority, identity, "
                           "tenant isolation or evidence)")
            tier = "C"
            continue
        if any(re.search(p, path, re.I) for p in TIER_A):
            continue
        if tier != "C":
            tier = "B"
        reasons.append(f"{path}: Tier B, runtime code with no critical-invariant signal")
    if tier == "A" and not reasons:
        reasons.append("all changed paths are documentation or text: Tier A")
    return tier, reasons


class Usage(Exception):
    """Caller error, kept distinct from a release verdict."""


def approvals(package, repo, approvers, tier, gh_json=None):
    """Return (ok, lines). Every Tier C NOT APPLICABLE in `package` must have been approved by an
    allowed GitHub login on a pull request that contains the package's commit. The reviewer NAME
    written in the contract is prose; this is the authenticated version of it (founder 2026-11-03).

    `gh_json(path)` runs `gh api <path>` and returns parsed JSON; injected for the selftest.
    """
    import json as _json
    if gh_json is None:
        def gh_json(path):
            r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
            return _json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
    exempt = [c for c, v in package.get("evidence", {}).items() if str(v).lower().startswith("not_applicable:")]
    if tier != "C" or not exempt:
        return True, [f"  approvals          not required (tier {tier}, {len(exempt)} exemption(s))"]
    sha = package.get("sha", "")
    pulls = gh_json(f"repos/{repo}/commits/{sha}/pulls") or []
    approved_by = set()
    for pr in pulls:
        latest = {}  # a reviewer's LATEST review is the one that counts (Codex 2026-11-03:
                     # an early APPROVED followed by DISMISSED or CHANGES_REQUESTED must not pass)
        for rv in gh_json(f"repos/{repo}/pulls/{pr['number']}/reviews") or []:
            latest[rv.get("user", {}).get("login")] = rv
        for login, rv in latest.items():
            # and it must approve THIS commit when GitHub says which commit it reviewed
            if rv.get("state") == "APPROVED" and (not rv.get("commit_id") or rv["commit_id"] == sha):
                approved_by.add(login)
    ok_logins = sorted(approved_by & set(a for a in approvers if a))
    if ok_logins:
        return True, [f"  approvals          {', '.join(exempt)} exempted; approved on PR by {', '.join(ok_logins)}"]
    return False, [f"  approvals          FAIL: tier C exemption(s) {', '.join(exempt)} have no APPROVED review "
                   f"from {', '.join(approvers)} on a pull request containing {sha[:12]} "
                   f"(found: {', '.join(sorted(a for a in approved_by if a)) or 'none'})"]


def exemption(result):
    """Parse `not_applicable: <reason>[; reviewed_by: <who>]` strictly.

    Returns (reason, reviewer). The reviewer counts only in the structured trailing form
    with a non-empty name; `reviewed_by:` with nothing after it, or the words appearing
    inside the prose of the reason, is no reviewer (Codex finding 2026-11-03).
    """
    body = result.split(":", 1)[1] if ":" in result else ""
    reason, reviewer = body, ""
    if "; reviewed_by:" in body:
        reason, reviewer = body.rsplit("; reviewed_by:", 1)
    reason = reason.strip()
    if reason.lower().startswith("reviewed_by:"):
        reason = ""
    return reason, reviewer.strip()


def provenance(package, head, dirty_now, evidence_only_on_top=False):
    """Return a list of provenance failures for an evidence package, empty if it binds.

    Evidence is evidence for exactly one commit. A package whose `sha` is not the commit
    being judged is stale or mismatched; one produced from a dirty tree describes code that
    no commit holds. Either is a BLOCK, whatever the checks said. The one allowed gap: HEAD
    may be the package's commit plus commits that touch nothing but docs/testing/evidence/
    (the evidence has to be committed after it is produced) — the CLI computes that flag
    from `git diff --name-only`, never from the package.
    """
    problems = []
    if package.get("sha") != head and not evidence_only_on_top:
        problems.append(f"COMMIT_MISMATCH: evidence is for {str(package.get('sha'))[:12]}, HEAD is {str(head)[:12]}")
    if package.get("dirty"):
        problems.append("DIRTY_TREE: evidence was produced from an uncommitted working tree")
    if dirty_now:
        problems.append("DIRTY_TREE: the tree being judged has uncommitted changes")
    return problems


def verify(paths, evidence, package=None, head=None, dirty_now=False, evidence_only_on_top=False):
    """Return (verdict, tier, required, lines).

    `evidence` maps a check name to a result. Anything absent is NOT RUN, and a
    required check that was not run can never be a PASS. With `package`, the evidence
    must also bind to `head` (see provenance()).
    """
    tier, reasons = classify(paths)
    required = REQUIRED[tier]
    lines = [f"tier {tier}"] + [f"  {r}" for r in reasons] + [""]

    missing, failed = [], []
    prov = provenance(package, head, dirty_now, evidence_only_on_top) if package is not None else []
    if package is not None and "evidence" in package:
        # The package's own results are what is judged. A flat evidence file that disagrees
        # with it is a BLOCK, not a second opinion (Codex finding 2026-11-03: the sha check
        # bound the package to HEAD but not the results to the package).
        if evidence and evidence != package["evidence"]:
            prov.append("EVIDENCE_MISMATCH: evidence.json differs from the package's recorded results")
        evidence = package["evidence"]
    for pr in prov:
        lines.append(f"  provenance         {pr}")
    for check in required:
        result = evidence.get(check)
        if result is None:
            missing.append(check)
            lines.append(f"  {check:<18} NOT RUN")
        elif str(result).lower() in ("pass", "passed", "ok", "true"):
            lines.append(f"  {check:<18} pass")
        elif str(result).lower().startswith("not_applicable:") and str(result).split(":", 1)[1].strip():
            # An explicit, reasoned exception (founder directive 2026-11-03: a requirement
            # that does not apply is marked NOT APPLICABLE with a reason, never left unrun).
            # "not_applicable:" with no reason is a failure — an exception needs an argument.
            # At Tier C the reason is not enough: the same contributor must not both exempt a
            # critical requirement and approve the release, so the exemption names who else
            # checked it (`reviewed_by: <person or review record>`), or it fails.
            reason, reviewer = exemption(str(result))
            if not reason:
                failed.append(check)
                lines.append(f"  {check:<18} FAIL (N/A without a reason)")
            elif tier == "C" and not reviewer:
                failed.append(check)
                lines.append(f"  {check:<18} FAIL (N/A at tier C needs '; reviewed_by: <who>' — {reason})")
            else:
                lines.append(f"  {check:<18} N/A — {reason}" + (f" (reviewed by {reviewer})" if reviewer else ""))
        else:
            failed.append(check)
            lines.append(f"  {check:<18} FAIL ({result})")

    extra = sorted(set(evidence) - set(required))
    for check in extra:
        lines.append(f"  {check:<18} (not required at tier {tier}, recorded)")

    if prov:
        verdict = BLOCK
        lines += ["", f"BLOCK: evidence does not bind to this commit ({len(prov)} provenance failure(s))"]
    elif failed:
        verdict = BLOCK
        lines += ["", f"BLOCK: {len(failed)} required check(s) failed: {', '.join(failed)}"]
    elif missing:
        verdict = INSUFFICIENT
        lines += ["", f"INSUFFICIENT_EVIDENCE: {len(missing)} required check(s) never ran: "
                      f"{', '.join(missing)}",
                  "  This is not a failure and it is not a pass. Run them, or record an",
                  "  explicit exception — 'not tested' must never become 'tested'."]
    else:
        verdict = PASS
        lines += ["", f"PASS: all {len(required)} required checks at tier {tier} passed"]
    return verdict, tier, required, lines


def selftest():
    checks = 0

    def ok(cond, msg):
        nonlocal checks
        assert cond, msg
        checks += 1

    # --- classification
    ok(classify(["README.md"])[0] == "A", "docs were not Tier A")
    ok(classify(["src/app/format.py"])[0] == "B", "ordinary code was not Tier B")
    ok(classify(["src/budgets.py"])[0] == "C", "a budget change was not Tier C")
    ok(classify(["src/tenant_scope.py"])[0] == "C", "tenant isolation was not Tier C")
    ok(classify(["src/evidence.py"])[0] == "C", "evidence was not Tier C")

    # The escalation must be one-way: one critical file in a big docs change is
    # still critical.
    ok(classify(["README.md", "docs/x.md", "src/mandates.py"])[0] == "C",
       "a critical file was diluted by documentation changes")
    # ...and Tier B is not downgraded by a doc alongside it.
    ok(classify(["README.md", "src/util.py"])[0] == "B", "code was downgraded by a doc")

    # An empty change set must not silently be the cheapest tier.
    try:
        classify([])
        ok(False, "an empty change set was classified")
    except Usage:
        ok(True, "")

    # --- verdicts
    full_c = {c: "pass" for c in REQUIRED["C"]}
    ok(verify(["src/budgets.py"], full_c)[0] == PASS, "a complete Tier C run did not pass")

    v, _, _, lines = verify(["src/budgets.py"], {"lint": "pass"})
    ok(v == INSUFFICIENT, f"a mostly-unrun Tier C change returned {v}")
    ok(any("never ran" in l for l in lines), "the missing checks were not named")

    broken = dict(full_c, concurrency="fail")
    ok(verify(["src/budgets.py"], broken)[0] == BLOCK, "a failed required check did not block")

    # THE central refusal: absence is never a pass, even when nothing failed.
    v, _, _, _ = verify(["src/budgets.py"], {})
    ok(v == INSUFFICIENT, f"an empty evidence pack returned {v}, not INSUFFICIENT_EVIDENCE")
    ok(v != PASS, "silence was read as success")

    # A failure outranks absence: BLOCK is worse news than INSUFFICIENT and must win.
    v, _, _, _ = verify(["src/budgets.py"], {"lint": "fail"})
    ok(v == BLOCK, "a failure was masked by other checks being unrun")

    # Tier A is genuinely cheap, so the gate stays usable.
    ok(verify(["README.md"], {"lint": "pass"})[0] == PASS, "a doc change needed more than lint")
    # ...but not free.
    ok(verify(["README.md"], {})[0] == INSUFFICIENT, "a doc change needed nothing at all")

    # The execution boundary is Tier C even though its path names no money word
    # (found 2026-11-03: app/services/action_gateway.py classified B).
    ok(classify(["app/services/action_gateway.py"])[0] == "C",
       "the action gateway escaped Tier C")

    # A reasoned NOT APPLICABLE satisfies a check at Tier B; at Tier C it also needs a reviewer.
    nb = {c: "pass" for c in REQUIRED["B"]}
    nb["integration"] = "not_applicable: pure library, no integration surface"
    ok(verify(["src/util.py"], nb)[0] == PASS, "a reasoned N/A was not accepted at tier B")
    na = {c: "pass" for c in REQUIRED["C"]}
    na["recovery"] = "not_applicable: stateless, nothing to restore"
    ok(verify(["src/budgets.py"], na)[0] == BLOCK, "a tier C N/A with no reviewer was accepted")
    na["recovery"] = "not_applicable: stateless, nothing to restore; reviewed_by: founder 2026-11-03"
    ok(verify(["src/budgets.py"], na)[0] == PASS, "a reviewed tier C N/A was not accepted")
    na["recovery"] = "not_applicable:"
    ok(verify(["src/budgets.py"], na)[0] == BLOCK, "an N/A without a reason was accepted")

    # Exemption parsing is strict: an empty reviewer or prose 'reviewed_by' is no reviewer.
    na["recovery"] = "not_applicable: reviewed_by:"
    ok(verify(["src/budgets.py"], na)[0] == BLOCK, "an empty reviewer was accepted")
    na["recovery"] = "not_applicable: nothing here, reviewed_by: nobody really"
    ok(verify(["src/budgets.py"], na)[0] == BLOCK, "prose reviewed_by was accepted as a reviewer")
    na["recovery"] = "not_applicable: ; reviewed_by: founder"
    ok(verify(["src/budgets.py"], na)[0] == BLOCK, "a reviewer with no reason was accepted")

    # The package's results are what is judged; a flat file cannot overrule them.
    good = {c: "pass" for c in REQUIRED["C"]}
    pk = {"sha": "abc", "dirty": False, "evidence": dict(good, recovery="exit 1")}
    ok(verify(["src/budgets.py"], good, pk, "abc")[0] == BLOCK, "flat pass overrode a package failure")
    ok(verify(["src/budgets.py"], {}, {"sha": "abc", "dirty": False, "evidence": good}, "abc")[0] == PASS,
       "a package alone was not judged")

    # Authenticated exemptions: a reviewer name in the contract is not an approval.
    pkg = {"sha": "a" * 40, "evidence": {**{c: "pass" for c in REQUIRED["C"]}, "recovery": "not_applicable: stateless; reviewed_by: founder"}}
    fake = lambda approved: (lambda path: [{"number": 7}] if path.endswith("/pulls") else [{"state": "APPROVED", "user": {"login": approved}}] if approved else [])
    ok(approvals(pkg, "o/r", ["Thepluscode"], "C", fake("Thepluscode"))[0] is True, "an approved tier C exemption was refused")
    ok(approvals(pkg, "o/r", ["Thepluscode"], "C", fake("someone-else"))[0] is False, "an exemption approved by a non-allowed login passed")
    ok(approvals(pkg, "o/r", ["Thepluscode"], "C", fake(None))[0] is False, "an exemption with no PR approval passed")
    ok(approvals(pkg, "o/r", ["Thepluscode"], "B", fake(None))[0] is True, "a tier B exemption demanded a PR approval")
    seq = lambda *states: (lambda path: [{"number": 7}] if path.endswith("/pulls") else
                           [{"state": st, "user": {"login": "Thepluscode"}} for st in states])
    ok(approvals(pkg, "o/r", ["Thepluscode"], "C", seq("APPROVED", "DISMISSED"))[0] is False, "a dismissed approval still counted")
    ok(approvals(pkg, "o/r", ["Thepluscode"], "C", seq("APPROVED", "CHANGES_REQUESTED"))[0] is False, "a withdrawn approval still counted")
    ok(approvals(pkg, "o/r", ["Thepluscode"], "C", seq("CHANGES_REQUESTED", "APPROVED"))[0] is True, "a final approval was refused")
    other = lambda path: [{"number": 7}] if path.endswith("/pulls") else [{"state": "APPROVED", "user": {"login": "Thepluscode"}, "commit_id": "b" * 40}]
    ok(approvals(pkg, "o/r", ["Thepluscode"], "C", other)[0] is False, "an approval for a different commit counted")
    ok(approvals(pkg, "o/r", ["", "Thepluscode"], "C", seq("APPROVED"))[0] is True, "an empty approver element broke a valid list")

    # Evidence binds to one commit: a stale, mismatched or dirty package blocks.
    good = {c: "pass" for c in REQUIRED["C"]}
    ok(verify(["src/budgets.py"], good, {"sha": "abc", "dirty": False}, "abc")[0] == PASS,
       "matching provenance was rejected")
    ok(verify(["src/budgets.py"], good, {"sha": "abc", "dirty": False}, "def")[0] == BLOCK,
       "a commit mismatch was not a BLOCK")
    ok(verify(["src/budgets.py"], good, {"sha": "abc", "dirty": False}, "def", evidence_only_on_top=True)[0] == PASS,
       "an evidence-only commit on top was treated as a mismatch")
    ok(verify(["src/budgets.py"], good, {"sha": "abc", "dirty": True}, "abc")[0] == BLOCK,
       "a dirty-tree package was not a BLOCK")
    ok(verify(["src/budgets.py"], good, {"sha": "abc", "dirty": False}, "abc", dirty_now=True)[0] == BLOCK,
       "a dirty tree under judgement was not a BLOCK")

    # Tier C inherits the cheap checks rather than escaping them.
    ok(set(REQUIRED["B"]).issubset(REQUIRED["C"]), "Tier C skips Tier B checks")
    ok(set(REQUIRED["A"]).issubset(REQUIRED["B"]), "Tier B skips Tier A checks")

    # Extra evidence is recorded, never a substitute for a required check.
    v, _, _, lines = verify(["src/budgets.py"], {"lint": "pass", "chaos": "pass"})
    ok(v == INSUFFICIENT, "unrequired evidence satisfied a required check")
    ok(any("not required at tier C" in l for l in lines), "extra evidence was not recorded")

    print(f"release_gate.py selftest: {checks} checks passed")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", nargs="?", choices=("classify", "verify", "approvals"))
    ap.add_argument("--repo", help="approvals: owner/name on GitHub")
    ap.add_argument("--approvers", default="", help="approvals: comma-separated GitHub logins allowed to approve a tier C exemption")
    ap.add_argument("--changed", nargs="*", default=[])
    ap.add_argument("--evidence", help="JSON file mapping check name to result")
    ap.add_argument("--package", help="evidence-package.json; binds the evidence to HEAD "
                                      "(commit mismatch or dirty tree is a BLOCK)")
    ap.add_argument("--allow-unbound", action="store_true",
                    help="judge a flat evidence file without a package (local triage only; the "
                         "output carries an UNBOUND marker and is never a release verdict)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.command:
        ap.error("give a command (classify, verify) or --selftest")

    try:
        if args.command == "approvals":
            with open(args.package) as fh:
                package = json.load(fh)
            tier, _ = classify(args.changed)
            good, lines = approvals(package, args.repo, [a for a in args.approvers.split(",") if a], tier)
            print("\n".join(lines))
            return 0 if good else 1
        if args.command == "classify":
            tier, reasons = classify(args.changed)
            print(f"tier {tier}")
            for r in reasons:
                print(f"  {r}")
            print(f"  required: {', '.join(REQUIRED[tier])}")
            return 0

        if args.command == "verify" and not args.package and not args.allow_unbound:
            raise Usage("verify needs --package (the runner's evidence-package.json); a flat evidence "
                        "file is a claim without provenance — --allow-unbound for local triage only")
        evidence = {}
        if args.evidence:
            if not os.path.exists(args.evidence):
                raise Usage(f"no evidence file at {args.evidence}")
            with open(args.evidence, encoding="utf-8") as fh:
                evidence = json.load(fh)
        package = head = None
        dirty_now = False
        if args.package:
            with open(args.package) as fh:
                package = json.load(fh)
            head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            # "Dirty" under judgement means a TRACKED file differs from HEAD. Untracked build
            # output (node_modules, target/, caches) is what a CI runner produces while
            # checking, and must not turn every CI verdict into a BLOCK.
            dirty_now = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                            capture_output=True, text=True).stdout.strip())
        on_top = False
        if package and package.get("sha") and package["sha"] != head:
            anc = subprocess.run(["git", "merge-base", "--is-ancestor", package["sha"], head]).returncode == 0
            changed = subprocess.run(["git", "diff", "--name-only", package["sha"], head],
                                     capture_output=True, text=True).stdout.split()
            on_top = anc and bool(changed) and all(c.startswith("docs/testing/evidence/") for c in changed)
        verdict, _tier, _required, lines = verify(args.changed, evidence, package, head, dirty_now, on_top)
        if package is None:
            lines.append("UNBOUND: no evidence package — local triage, not a release verdict")
        print("\n".join(lines))
        return EXIT[verdict]
    except Usage as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
