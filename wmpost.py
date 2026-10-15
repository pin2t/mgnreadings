#!/usr/bin/env python3
"""Submit water meter readings to mgn-city.ru.

    ./wmpost.py -account 123456789 -meter "25-1111222=42" -meter "25-2222333=55"
    ./wmpost.py -account 123456789 -meter "25-1111222=42" --dry-run

The work is done in mgncity.py, which must sit next to this file.
"""

import sys

from mgncity import WATER, main

if __name__ == "__main__":
    sys.exit(main(WATER))
