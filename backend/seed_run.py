#!/usr/bin/env python
"""Simple wrapper to execute seed and verify results."""

import asyncio
import sys
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent
sys.path.insert(0, str(backend_path))

from scripts.seed.run_seed import main  # noqa: E402


async def run():
    try:
        print("🌱 Starting seed execution...")
        await main()
        print("\n✅ SEED COMPLETED SUCCESSFULLY")
        return 0
    except Exception as e:
        print(f"\n❌ SEED FAILED: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run())
    sys.exit(exit_code)
