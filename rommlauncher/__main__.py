"""`python -m rommlauncher` does the same as `romm_launcher.py`."""

import sys

from .cli import main

sys.exit(main())
