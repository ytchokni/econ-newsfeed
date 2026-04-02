"""Match DB researchers against RePEC person records."""
from __future__ import annotations

import argparse
import csv
import os
import sys
from urllib.parse import urlparse


def parse_rdf_file(path: str) -> dict | None:
    """Parse a single ReDIF .rdf file into a dict.

    Returns None if the record has no Homepage field.
    Extracts: name_first, name_last, name_full, workplace, homepage, handle.
    """
    fields: dict[str, str] = {}
    current_key: str | None = None

    with open(path, encoding="latin-1") as f:
        for line in f:
            line = line.rstrip("\n\r")
            # Continuation line for Workplace-Name (starts with "/ ")
            if line.startswith("/ ") and current_key == "workplace":
                continue  # we only keep the first line
            # Field line: "Key: Value"
            if ": " in line and not line.startswith(" "):
                key, _, value = line.partition(": ")
                key = key.strip()
                value = value.strip()
                if key == "Name-First":
                    fields["name_first"] = value
                    current_key = "name_first"
                elif key == "Name-Last":
                    fields["name_last"] = value
                    current_key = "name_last"
                elif key == "Name-Full":
                    fields["name_full"] = value
                    current_key = "name_full"
                elif key == "Workplace-Name":
                    fields["workplace"] = value
                    current_key = "workplace"
                elif key == "Homepage":
                    fields["homepage"] = value
                    current_key = "homepage"
                elif key == "Handle":
                    fields["handle"] = value
                    current_key = "handle"
                else:
                    current_key = None
            else:
                current_key = None

    if "homepage" not in fields:
        return None
    if "name_first" not in fields or "name_last" not in fields:
        return None

    fields.setdefault("name_full", f"{fields['name_first']} {fields['name_last']}")
    fields.setdefault("workplace", "")
    fields.setdefault("handle", "")

    return fields
