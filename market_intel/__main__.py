"""Entry point: delegates to market_intel.main()."""
from __future__ import annotations

import sys

from market_intel.main import main

if __name__ == "__main__":
    sys.exit(main())
