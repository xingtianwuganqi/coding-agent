#!/usr/bin/env python
"""Simple test runner script."""

import sys
import subprocess

def main():
    """Run tests and report results."""
    print("Running tests for mini-coding-agent...")
    print("=" * 60)
    
    # Add src to path
    sys.path.insert(0, "src")
    
    # Run pytest
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=".",
        capture_output=False
    )
    
    print("=" * 60)
    if result.returncode == 0:
        print("✓ All tests passed!")
    else:
        print(f"✗ Tests failed with exit code: {result.returncode}")
    
    return result.returncode

if __name__ == "__main__":
    sys.exit(main())