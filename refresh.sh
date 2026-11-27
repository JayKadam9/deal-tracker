#!/bin/bash
cd /Users/jaykadam/Desktop/buildwithjay/deal-tracker || exit 1
git pull --rebase -q
/usr/bin/python3 scripts/fetch_deals.py --out data/deals.json || exit 1
git add data/deals.json
git commit -qm "Daily deals refresh $(date '+%d %b %Y')" && git push -q
