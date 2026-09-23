"""python -m forge "a plant monitor that waters itself" [-o out/] [--provider local|claude|offline]

Writes <name>.vbuild into the output folder, and the same files unpacked
next to it so they can be opened directly.
"""
import argparse
import sys
from pathlib import Path

from . import pipeline, vbuild


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="forge", description="Turn a device idea into a .vbuild project.")
    ap.add_argument("idea", help="what the device should do, in plain words")
    ap.add_argument("-o", "--out", default="forge-out", help="output directory (default: forge-out)")
    ap.add_argument("--provider", default="auto", choices=["auto", "local", "claude", "offline"],
                    help="which model plans the device (default: the local model when Ollama serves it)")
    ap.add_argument("--offline", action="store_true", help="same as --provider offline")
    ap.add_argument("--no-compile", action="store_true", help="skip compiling the firmware")
    args = ap.parse_args(argv)

    provider = "offline" if args.offline else args.provider
    result = pipeline.run(args.idea, provider=None if provider == "offline" else provider,
                          compile_firmware=False if args.no_compile else None,
                          log=lambda m: print(m, file=sys.stderr))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = pipeline.bundle(result)
    target = out / pipeline.filename(result)
    target.write_bytes(data)
    _, files = vbuild.read(data)
    for rel, content in files.items():
        path = out / "files" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    d = result["design"]
    print(f"\n{d['name']} on {d['board']['name']} - {len(d['instances'])} parts, "
          f"${result['bom_total']:.2f}, planned by {result['planner']}")
    print(f"Firmware image: {result['firmware_binary'] or 'not compiled'}")
    for issue in d["issues"]:
        print(f"  {issue['severity']:7} {issue['message']}")
    print(f"\nWrote {target} ({len(data) // 1024} KB) and {len(files)} files under {out / 'files'}/")
    return 0 if d["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
