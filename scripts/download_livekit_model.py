"""
Download LiveKit agent model files at build time.

This script MUST NOT require LiveKit credentials.
It is safe to run in Docker build.
"""

from livekit.agents.cli import download_files

if __name__ == "__main__":
    download_files()
