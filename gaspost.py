#!/usr/bin/env python3
"""Submit the gas meter reading (ООО «НОВАТЭК-Челябинск») to mgn-city.ru.

The meter has no number on the site, so -meter is just the value; it goes into
the first "Новое показание" field.

    ./gaspost.py -account 12345678 -meter 15
    ./gaspost.py -account 12345678 -meter 15 --dry-run

The work is done in mgncity.py, which must sit next to this file.
"""

import sys

from mgncity import GAS, main

if __name__ == "__main__":
    sys.exit(main(GAS))
