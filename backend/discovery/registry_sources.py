"""Free researcher registries as website-candidate sources for the miner.

* ORCID: a researcher's OpenAlex author record names their ORCID iD; the ORCID
  record's self-maintained "Websites & social links" list may hold a homepage.
  ``pub.orcid.org`` (the Public API) disallows every robot in its robots.txt,
  so records are read from ``orcid.org/<iD>/public-record.json``, which
  orcid.org's robots.txt permits (Crawl-delay: 1).
* RePEc/IDEAS: the author index (``ideas.repec.org/i/e<letter>.html``) lists
  every registered RePEc author; each author page carries the homepage the
  author registered with the RePEc Author Service.

These functions only parse. They never decide identity: every website goes
into the mining queue and must pass ``mining._verify`` (full-name personal
page plus a matching publication title) and ``MiningStore.approve_bundle``.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from backend.database.identity import same_person
from backend.discovery.mining_policy import norm_name, normalized_url

OPENALEX_AUTHORS_URL = "https://api.openalex.org/authors"
OPENALEX_BATCH = 50
IDEAS_HOST = "ideas.repec.org"
IDEAS_INDEX_URLS = [f"https://{IDEAS_HOST}/i/e{letter}.html" for letter in "abcdefghijklmnopqrstuvwxyz"]
_ORCID = re.compile(r"^(?:https?://(?:www\.)?orcid\.org/)?(\d{4}-\d{4}-\d{4}-\d{3}[\dX])/?$", re.I)
_OPENALEX_AUTHOR = re.compile(r"^(?:https?://openalex\.org/)?(A\d{4,12})$", re.I)
_IDEAS_AUTHOR_PATH = re.compile(r"^/[ef]/(p[a-z]{2,3}\d+)\.html$")


# ---------------------------------------------------------------- ORCID

def normalize_orcid(value) -> str | None:
    """Return the bare iD ``0000-0002-1825-0097`` if the ISO 7064 checksum holds."""
    match = _ORCID.match(str(value or "").strip())
    if not match:
        return None
    orcid = match.group(1).upper()
    digits = orcid.replace("-", "")
    total = 0
    for char in digits[:-1]:
        total = (total + int(char)) * 2
    check = (12 - total % 11) % 11
    return orcid if digits[-1] == ("X" if check == 10 else str(check)) else None


def orcid_profile_url(orcid: str) -> str:
    return f"https://orcid.org/{orcid}"


def orcid_record_url(orcid: str) -> str:
    return f"https://orcid.org/{orcid}/public-record.json"


def openalex_authors_url(author_ids: list[str], mailto: str | None = None) -> str:
    """One OpenAlex request for up to 50 authors, returning only id and ORCID."""
    ids = [a for a in author_ids if _OPENALEX_AUTHOR.match(a or "")][:OPENALEX_BATCH]
    params = {"filter": "openalex:" + "|".join(ids), "select": "id,orcid,display_name",
              "per_page": OPENALEX_BATCH}
    if mailto:
        params["mailto"] = mailto
    return OPENALEX_AUTHORS_URL + "?" + urlencode(params)


def openalex_headers() -> dict:
    key = os.environ.get("OPENALEX_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def parse_openalex_authors(data) -> dict[str, dict]:
    """``{"A123": {"orcid": "0000-…", "display_name": …}}`` for authors with a valid iD."""
    found = {}
    for author in (data or {}).get("results") or []:
        match = _OPENALEX_AUTHOR.match(str(author.get("id") or ""))
        orcid = normalize_orcid(author.get("orcid"))
        if match and orcid:
            found[match.group(1).upper()] = {"orcid": orcid, "display_name": author.get("display_name") or ""}
    return found


def _value(node):
    return (node or {}).get("value") if isinstance(node, dict) else None


def parse_orcid_record(data) -> dict:
    """Names and public websites of an orcid.org ``public-record.json`` body."""
    data = data if isinstance(data, dict) else {}
    names = data.get("names") or {}
    websites = []
    for item in ((data.get("website") or {}).get("websites") or []):
        visibility = ((item.get("visibility") or {}).get("visibility") or "PUBLIC").upper()
        url = _value(item.get("url"))
        if url and visibility == "PUBLIC":
            websites.append({"url": url.strip(), "label": (item.get("urlName") or "").strip(),
                             "source": item.get("source") or ""})
    return {"given": (_value(names.get("givenNames")) or "").strip(),
            "family": (_value(names.get("familyName")) or "").strip(),
            "credit": (_value(names.get("creditName")) or "").strip(),
            "websites": websites}


def orcid_websites(data, orcid: str, first_name: str, last_name: str) -> tuple[list[dict], str | None]:
    """Website candidates from an ORCID record, or ``([], reason)``.

    The record must name the same person as the researcher (``same_person``,
    default strength). A link the iD holder added themselves is marked
    ``self_asserted``; links added by an integration are kept but marked.
    """
    record = parse_orcid_record(data)
    if not record["family"] or not same_person(first_name, last_name, record["given"], record["family"]):
        return [], "orcid_name_mismatch"
    found, seen = [], set()
    for site in record["websites"]:
        url = normalized_url(site["url"])
        if not url or url in seen:
            continue
        seen.add(url)
        found.append({"url": url, "label": site["label"][:200],
                      "self_asserted": site["source"] == orcid,
                      "orcid_name": f"{record['given']} {record['family']}".strip()})
    return found, None if found else "orcid_no_websites"


# ---------------------------------------------------------------- RePEc / IDEAS

def parse_ideas_index(html: str, url: str) -> list[dict]:
    """Author entries ``Surname, Given`` of one IDEAS author-index page."""
    if (urlparse(url).hostname or "").lower() != IDEAS_HOST:
        return []
    soup = BeautifulSoup(html, "html.parser")
    entries = {}
    for anchor in soup.select('a[href^="/e/"], a[href^="/f/"]'):
        match = _IDEAS_AUTHOR_PATH.match(anchor.get("href", ""))
        label = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        if not match or label.count(",") < 1:
            continue
        last, _, first = (part.strip() for part in label.partition(","))
        if not first or not last:
            continue
        target = urljoin(f"https://{IDEAS_HOST}/", anchor["href"])
        entries[target] = {"first_name": first, "last_name": last, "url": target,
                           "short_id": match.group(1)}
    return list(entries.values())


def ideas_name_key(first: str, last: str) -> str:
    return norm_name(f"{first} {last}")


def parse_ideas_author(html: str, url: str) -> dict | None:
    """Name, registered homepage and research-output text of an IDEAS author page."""
    if (urlparse(url).hostname or "").lower() != IDEAS_HOST:
        return None
    soup = BeautifulSoup(html, "html.parser")
    person = soup.select_one("#person")
    if not person:
        return None
    fields = {}
    for row in person.select("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = cells[0].get_text(" ", strip=True).rstrip(":").lower()
            if label:
                fields[label] = cells[1].get_text(" ", strip=True)
    homepage = None
    label_cell = person.select_one("td.homelabel")
    if label_cell:
        value = label_cell.find_next_sibling("td")
        anchor = value.select_one("a[href]") if value else None
        if anchor:
            homepage = normalized_url(anchor["href"].strip())
    first = " ".join(x for x in (fields.get("first name", ""), fields.get("middle name", "")) if x)
    research = soup.select_one("#research")
    return {"first_name": first.strip(), "last_name": fields.get("last name", "").strip(),
            "short_id": fields.get("repec short-id", "").strip(), "homepage": homepage,
            "research_text": research.get_text(" ", strip=True) if research else ""}
