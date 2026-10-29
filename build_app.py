import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path


def build_app(platform_name: str = "auto") -> None:
    root = Path(__file__).resolve().parent
    dist_dir = root / "dist"
    build_dir = root / "build"
    dist_dir.mkdir(exist_ok=True)
    build_dir.mkdir(exist_ok=True)

    target = platform_name
    if target == "auto":
        target = platform.system().lower()

    if target.startswith("win"):
        cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--noconsole", "--name", "EntityIntelligence", "desktop_app.py"]
    else:
        cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--name", "EntityIntelligence", "desktop_app.py"]

    subprocess.run(cmd, cwd=root, check=True)
    print(f"Packaging complete. Output in: {dist_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Package the Entity Intelligence desktop app")
    parser.add_argument("--platform", choices=["auto", "windows", "linux", "darwin"], default="auto", help="Target platform")
    args = parser.parse_args()
    build_app(args.platform)


if __name__ == "__main__":
    main()
