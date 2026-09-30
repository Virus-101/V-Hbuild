"""Assemble the static website into site-dist/ (what Netlify publishes).

    python scripts/build_site.py

The site is the landing page (site/), the example builds (site/samples/)
and the same 3D viewer the app uses (vhbuild/static/), which runs entirely in
the browser. Standard library only, so any host's Python can run it.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site-dist"


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "site", OUT)
    static = ROOT / "vhbuild" / "static"
    # The viewer references /static/...; the app's own pages stay out of the site.
    shutil.copytree(static, OUT / "static", ignore=shutil.ignore_patterns("index.html", "viewer.html"))
    shutil.copy2(static / "viewer.html", OUT / "viewer.html")
    files = sum(1 for p in OUT.rglob("*") if p.is_file())
    print(f"site-dist/: {files} files")


if __name__ == "__main__":
    main()
