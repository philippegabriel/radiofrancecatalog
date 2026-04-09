#!/usr/bin/env python3
# Python program to convert 
# CSV to HTML Table

import pandas as pd
import sys

a = pd.read_csv(sys.stdin)
a.to_html(sys.stdout)

