#!/usr/bin/env python3
"""Submit electricity meter readings (ООО «МЭК») to mgn-city.ru.

    ./elpost.py -account 123456789 -meter 011111111111110000=17650
    ./elpost.py -account 123456789 -meter 011111111111110000=17650 --dry-run

The work is done in mgncity.py, which must sit next to this file.
"""

import sys

from mgncity import ELECTRICITY, main

if __name__ == "__main__":
    sys.exit(main(ELECTRICITY))
