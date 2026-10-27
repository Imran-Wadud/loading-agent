"""Launch the Loading Agent Reflex dashboard."""

from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> int:
    print("🚀 Launching Loading Agent Reflex dashboard...")

    try:
        subprocess.run(
            [sys.executable, "-m", "reflex", "run"],
            cwd=PROJECT_ROOT,
            check=True,
        )
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"Reflex exited with status {exc.returncode}.")
        return exc.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())