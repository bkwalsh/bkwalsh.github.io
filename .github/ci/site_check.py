#!/usr/bin/env python3
"""Measure a built Jekyll site: pages, broken internal links, Lighthouse scores.

Usage: site_check.py <_site dir> <out.json> [--no-lighthouse]
The site must be built with .github/ci/ci-config.yml (url: http://localhost:4000).
"""
import html.parser
import http.server
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import functools

PORT = 4000
ORIGIN = f"http://localhost:{PORT}"
LH_PAGES = ["/about/", "/portfolio/", "/books/"]
LH_RUNS = 3
CATS = ["performance", "accessibility", "best-practices", "seo"]


class Refs(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if not v:
                continue
            if k in ("href", "src"):
                self.refs.append(v)
            elif k == "srcset":
                self.refs += [p.strip().split(" ")[0] for p in v.split(",") if p.strip()]


def resolve(site, page_rel, ref):
    if ref.startswith(ORIGIN):
        ref = ref[len(ORIGIN):] or "/"
    u = urllib.parse.urlparse(ref)
    if u.scheme or u.netloc or not u.path:  # external, mailto:, data:, pure #fragment
        return None
    path = urllib.parse.unquote(u.path)
    if not path.startswith("/"):
        path = os.path.normpath(os.path.join("/" + os.path.dirname(page_rel), path))
    full = os.path.join(site, path.lstrip("/"))
    for cand in (full, os.path.join(full, "index.html"), full + ".html"):
        if os.path.isfile(cand):
            return None
    return path


def scan(site):
    pages, broken = [], set()
    for root, _, files in os.walk(site):
        for f in files:
            if not f.endswith(".html"):
                continue
            rel = os.path.relpath(os.path.join(root, f), site)
            pages.append(rel)
            p = Refs()
            try:
                p.feed(open(os.path.join(root, f), encoding="utf-8", errors="replace").read())
            except Exception:
                continue
            for r in p.refs:
                miss = resolve(site, rel, r)
                if miss:
                    broken.add(f"{rel} -> {miss}")
    return sorted(pages), sorted(broken)


def weight(site):
    tot = {}
    for root, _, files in os.walk(site):
        for f in files:
            ext = os.path.splitext(f)[1].lower() or "other"
            tot[ext] = tot.get(ext, 0) + os.path.getsize(os.path.join(root, f))
    return tot


def lighthouse(site):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = {}
    try:
        for page in LH_PAGES:
            runs = {c: [] for c in CATS}
            metrics = []
            for _ in range(LH_RUNS):
                with tempfile.NamedTemporaryFile(suffix=".json") as tf:
                    r = subprocess.run(
                        ["lighthouse", ORIGIN + page, "--quiet", "--output=json", f"--output-path={tf.name}",
                         f"--only-categories={','.join(CATS)}",
                         "--chrome-flags=--headless=new --no-sandbox --disable-gpu"],
                        capture_output=True, text=True, timeout=180)
                    if r.returncode != 0:
                        print(r.stderr[-2000:], file=sys.stderr)
                        continue
                    d = json.load(open(tf.name))
                for c in CATS:
                    s = d["categories"][c]["score"]
                    if s is not None:
                        runs[c].append(round(s * 100))
                a = d["audits"]
                metrics.append({k: a[k].get("numericValue") for k in
                                ("first-contentful-paint", "largest-contentful-paint",
                                 "total-blocking-time", "cumulative-layout-shift", "total-byte-weight")
                                if k in a})
            out[page] = {c: (statistics.median(v) if v else None) for c, v in runs.items()}
            if metrics:
                out[page]["metrics"] = {k: statistics.median([m[k] for m in metrics if m.get(k) is not None])
                                        for k in metrics[0]}
    finally:
        srv.shutdown()
    return out


def main():
    site, out_path = sys.argv[1], sys.argv[2]
    pages, broken = scan(site)
    result = {"pages": pages, "broken_links": broken, "bytes_by_ext": weight(site)}
    if "--no-lighthouse" not in sys.argv:
        result["lighthouse"] = lighthouse(site)
    json.dump(result, open(out_path, "w"), indent=2)
    print(json.dumps({"pages": len(pages), "broken_links": len(broken),
                      "lighthouse": result.get("lighthouse")}, indent=2))


if __name__ == "__main__":
    main()
