#!/usr/bin/env python3
"""Decide whether an automated change is safe to land.

Usage: gate.py <before.json> <after.json> <report.md>
Exit 0 = safe to auto-merge, 1 = needs human review. Writes a markdown report either way.
"""
import json
import subprocess
import sys

REQUIRED_PAGES = ["index.html", "about/index.html", "portfolio/index.html", "books/index.html"]
PROTECTED = (".github/", "_data/books.yml", "images/books/", "CNAME")
MAX_MODIFIED_LINES = 400   # edits to existing text files (deletions of whole files don't count)
TOLERANCE = {"performance": 5, "accessibility": 2, "best-practices": 5, "seo": 2}


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def main():
    before, after = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
    problems, notes = [], []

    for p in REQUIRED_PAGES:
        if p not in after["pages"]:
            problems.append(f"Required page missing: `{p}`")

    new_broken = sorted(set(after["broken_links"]) - set(before["broken_links"]))
    if new_broken:
        problems.append("New broken internal links:\n" + "\n".join(f"  - `{b}`" for b in new_broken[:20]))
    fixed = len(set(before["broken_links"]) - set(after["broken_links"]))
    if fixed:
        notes.append(f"Fixed {fixed} broken internal link(s).")

    changed = git("diff", "--cached", "--name-only").split()
    touched = [f for f in changed if f.startswith(PROTECTED) or f in PROTECTED]
    if touched:
        problems.append("Touched protected files: " + ", ".join(f"`{f}`" for f in touched))

    modified = 0
    for line in git("diff", "--cached", "--numstat", "--diff-filter=M").splitlines():
        a, d, _ = line.split("\t", 2)
        if a != "-":
            modified += int(a) + int(d)
    if modified > MAX_MODIFIED_LINES:
        problems.append(f"{modified} lines edited in existing files (limit {MAX_MODIFIED_LINES} for auto-merge)")

    rows = ["| Page | Category | Before | After |", "|---|---|---|---|"]
    lb, la = before.get("lighthouse", {}), after.get("lighthouse", {})
    if not lb or not la:
        problems.append("Lighthouse results missing")
    for page in sorted(set(lb) | set(la)):
        for cat, tol in TOLERANCE.items():
            b, a = lb.get(page, {}).get(cat), la.get(page, {}).get(cat)
            rows.append(f"| {page} | {cat} | {b} | {a} |")
            if a is None or b is None:
                problems.append(f"Lighthouse {cat} could not be measured on {page} (before={b}, after={a})")
            elif a is not None and b is not None and a < b - tol:
                problems.append(f"Lighthouse {cat} on {page} dropped {b} -> {a} (tolerance {tol})")

    kb = lambda d: sum(d.get("bytes_by_ext", {}).values()) / 1024
    notes.append(f"Built site size: {kb(before):.0f} KB -> {kb(after):.0f} KB; "
                 f"pages: {len(before['pages'])} -> {len(after['pages'])}.")

    ok = not problems
    report = ["### Automated checks: " + ("passed, auto-merging" if ok else "FAILED, needs review")]
    report += [f"- {p}" for p in problems] + [f"- {n}" for n in notes] + ["", *rows]
    open(sys.argv[3], "w").write("\n".join(report) + "\n")
    print("\n".join(report))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
