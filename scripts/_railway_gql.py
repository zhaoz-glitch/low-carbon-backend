#!/usr/bin/env python
"""Helper: run a Railway GraphQL v2 query/mutation.

Usage: python railway_gql.py <payload.json>
Auth: reads accessToken from ~/.railway/config.json
"""
import json
import os
import sys
import urllib.request

CFG = os.path.join(os.path.expanduser("~"), ".railway", "config.json")
TOKEN = json.load(open(CFG, encoding="utf-8"))["user"]["accessToken"]

payload = open(sys.argv[1], encoding="utf-8").read()
req = urllib.request.Request(
    "https://backboard.railway.com/graphql/v2",
    data=payload.encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + TOKEN,
        "User-Agent": "railway-cli/5.45.5",
        "Accept": "*/*",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    out = {"http_error": e.code, "body": e.read().decode("utf-8", "replace")[:500]}

print(json.dumps(out, indent=1, ensure_ascii=False))
