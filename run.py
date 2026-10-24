# run.py
import subprocess
import sys

if __name__ == "__main__":
    print("🚀 Launching Athletic Workload & Recovery Engine Dashboard...")
    subprocess.run([sys.executable, "-m", "streamlit", "run", "app/ui.py"])