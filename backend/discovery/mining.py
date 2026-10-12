"""Incremental website discovery from cached links and department directories.

No search or LLM calls. Network candidates are durable before a source checkpoint
advances, and the store atomically imports each independently verified bundle.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import logging
import os
import time
from urllib.parse import urlsplit

from backend.discovery.mining_policy import (
    extract_links, norm_name, normalized_url, page_type, paper_overlap,
    permitted_page, same_owner, scope_for, section_links, url_key,
    validate_identity,
)
from backend.discovery.mining_sources import (
    BudgetExceeded, DIRECTORY_SEEDS, MiningFetcher, directory_links,
    profile_websites,
)
from backend.discovery.mining_store import MiningStore

logger = logging.getLogger(__name__)
MINER_VERSION = "1"


def _name(person):
    return f"{person['first_name']} {person['last_name']}".strip()


def _hash(html):
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _enqueue(store, candidate, names, known):
    """Match exact full names only; uncertainty remains explicit in the queue."""
    url = normalized_url(candidate.get("url"))
    kind = (candidate.get("evidence") or {}).get("page_type")
    if (not url or len(url) > 2048 or len(candidate.get("source_url", "")) > 2048
            or len(candidate.get("name", "")) > 511 or not permitted_page(url, kind)
            or (url_key(url) in known and candidate.get("source_kind") != "directory_profile")):
        return False
    candidate = dict(candidate, url=url)
    if not candidate.get("researcher_id"):
        owners = names.get(norm_name(candidate["name"]), [])
        candidate["researcher_id"] = owners[0]["id"] if len(owners) == 1 else None
        candidate.setdefault("evidence", {})["matching_researcher_ids"] = [p["id"] for p in owners]
    candidate.setdefault("evidence", {})["miner_version"] = MINER_VERSION
    return store.enqueue(candidate)


def _mine_cached(store, people, names, known, limit, stats, deadline):
    for source in store.cached_sources(limit):
        if time.monotonic() >= deadline:
            break
        rid = source["researcher_id"]
        person = people.get(rid)
        if not person:
            continue
        html, base = source["raw_html"], source["url"]
        evidence = {"source_researcher_id": rid, "source_url_id": source["url_id"],
                    "source_content_hash": source["content_hash"]}
        # Sections of an existing site retain that person's ownership boundary.
        for link in section_links(html, base):
            candidate = {"researcher_id": rid, "name": _name(person),
                         "url": link["url"], "source_url": base,
                         "source_kind": "known_site", "evidence": dict(evidence, page_type=link["page_type"])}
            stats["queued"] += bool(_enqueue(store, candidate, names, known))
        # Name anchors are discovery evidence, never sufficient identity proof.
        for link in extract_links(html, base):
            matches = names.get(norm_name(link["label"]), [])
            if not matches or (len(matches) == 1 and matches[0]["id"] == rid):
                continue
            candidate = {"name": link["label"], "url": link["url"],
                         "source_url": base, "source_kind": "cached_link",
                         "evidence": dict(evidence, anchor=link["label"],
                                          context=link.get("context", "")[:2000])}
            stats["queued"] += bool(_enqueue(store, candidate, names, known))
        # A crash before this point repeats idempotent enqueues, never loses links.
        store.checkpoint("cached", base, source["content_hash"], retry_hours=0)
        stats["cached_pages"] += 1


def _mine_directories(store, fetcher, names, known, stats):
    for seed in DIRECTORY_SEEDS:
        if not store.source_due("directory", seed["url"]):
            continue
        response = fetcher.fetch(seed["url"])
        stats["directories"] += 1
        if response["status"] != 200 or not response.get("html"):
            store.checkpoint("directory", seed["url"], "", error=response.get("error") or str(response["status"]), retry_hours=24)
            continue
        links = directory_links(seed, response["html"], response["url"])
        for link in links:
            candidate = {"name": link["name"], "url": link["url"],
                         "source_url": response["url"],
                         "source_kind": "directory_profile" if link["kind"] == "profile" else "directory",
                         "evidence": {"directory_url": seed["url"], "department": seed["name"],
                                      "directory_generation": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                      "source_content_hash": _hash(response["html"])}}
            stats["queued"] += bool(_enqueue(store, candidate, names, known))
        # Empty/shell/error directories are retried rather than silently exhausted.
        store.checkpoint("directory", seed["url"], _hash(response["html"]),
                         error=None if links else "No person website/profile links found",
                         retry_hours=168 if links else 24)


def _retry(store, candidate, reason, stats):
    exhausted = candidate.get("attempts", 0) >= 2
    status = "review" if exhausted else "retry"
    store.save_result(candidate, status, reason, retry_hours=24 * (2 ** min(candidate.get("attempts", 0), 4)))
    stats[status] += 1


def _review(store, candidate, reason, stats, urls=None):
    store.save_result(candidate, "review", reason, urls=urls)
    stats["review"] += 1


def _verify(store, fetcher, candidate, people, names, known, stats):
    response = fetcher.fetch(candidate["url"])
    if response["status"] != 200 or not response.get("html"):
        _retry(store, candidate, "Destination unavailable: " + str(response.get("error") or response["status"]), stats)
        return
    name = candidate["name"]
    if candidate["source_kind"] == "directory_profile":
        # Profiles are durable tasks, so a large department resumes next batch.
        # Only the directory's named profile can supply personal-website links.
        if validate_identity(response["html"], name)["confirmed"]:
            for child in profile_websites(name, response["html"], response["url"]):
                child["evidence"] = dict(candidate.get("evidence") or {}, **child.get("evidence", {}))
                stats["queued"] += bool(_enqueue(store, child, names, known))
    rid = candidate.get("researcher_id")
    if not rid or rid not in people:
        _review(store, candidate, "Researcher identity is new or ambiguous; manual matching required", stats)
        return
    person = people[rid]
    # Even one DB name row may conflate homonyms. Require a publication match,
    # using shared papers when the source is a different known researcher.
    evidence = candidate.get("evidence") or {}
    source_rid = evidence.get("source_researcher_id")
    titles = store.paper_titles(rid, source_rid if source_rid and source_rid != rid else None)
    if not titles:
        _review(store, candidate, "No corroborating publication titles for automatic identity matching", stats)
        return

    first_url = normalized_url(response["url"])
    expected_kind = evidence.get("page_type")
    if not first_url or not permitted_page(first_url, expected_kind):
        _review(store, candidate, "Destination redirects to a non-researcher page", stats)
        return
    source_is_owner = candidate["source_kind"] == "known_site" and source_rid == rid
    scope = scope_for(candidate["source_url"] if source_is_owner else first_url)
    if not same_owner(scope, first_url, explicit=True):
        _review(store, candidate, "Known-site section redirected outside the person's ownership boundary", stats)
        return
    pending = section_links(response["html"], first_url)
    if source_is_owner and expected_kind:
        for link in pending:
            if url_key(link["url"]) == url_key(first_url):
                link["page_type"] = expected_kind
        home_url = scope.home_url or candidate["source_url"]
        pending.append({"url": home_url, "page_type": "HOME"})
    if not any(url_key(x["url"]) == url_key(first_url) for x in pending):
        pending.insert(0, {"url": first_url, "page_type": expected_kind or page_type(first_url) or "HOME"})
    pending = [dict(link, depth=0 if link["page_type"] == "HOME" or url_key(link["url"]) == url_key(first_url) else 1)
               for link in pending]
    # Home coverage is checked before any database insertion.
    pending.sort(key=lambda x: x["page_type"] != "HOME")
    responses = {url_key(first_url): response}
    checked, accepted, seen = [], {}, set()
    partial = False
    unverified_sections = []
    while pending:
        if len(seen) >= 32:
            _review(store, candidate, "More than 32 section URLs require manual scope review", stats, list(accepted.values()))
            return
        link = pending.pop(0)
        key = url_key(link["url"])
        if key in seen:
            continue
        seen.add(key)
        if not same_owner(scope, link["url"], explicit=True):
            continue
        page = responses.get(key)
        if page is None:
            page = fetcher.fetch(link["url"])
            responses[key] = page
        if page["status"] != 200 or not page.get("html"):
            partial = True
            continue
        final = normalized_url(page["url"])
        if not final or not same_owner(scope, final, explicit=True) or not permitted_page(final, link["page_type"]):
            partial = True
            continue
        proof = validate_identity(page["html"], _name(person))
        overlap = paper_overlap(proof["text"], titles)
        kind = link["page_type"]
        # A new-papers listing may have no already-known title. Its distinctive
        # identity can inherit publication corroboration from the verified home.
        if kind == "HOME" and not proof["confirmed"]:
            continue
        same_hostname = (urlsplit(final).hostname or "").removeprefix("www.") == scope.host
        inherited = same_hostname and overlap and proof.get("inheritable_section", False)
        if kind != "HOME" and not (proof["confirmed"] or inherited):
            unverified_sections.append(final)
            continue
        final_key = url_key(final)
        previous = accepted.get(final_key)
        if not previous or kind == "HOME":
            accepted[final_key] = {"url": final, "page_type": kind}
        checked.append({"url": final, "identity": proof["confirmed"], "paper_match": overlap, "page_type": kind})
        # Two navigation levels cover Home -> Research -> Working papers.
        if link["depth"] < 2:
            for extra in section_links(page["html"], final):
                if url_key(extra["url"]) not in seen and same_owner(scope, extra["url"], explicit=True):
                    pending.append(dict(extra, depth=link["depth"] + 1))
    bundle = list(accepted.values())
    if not any(x["page_type"] == "HOME" for x in bundle):
        _review(store, candidate, "No verified personal homepage/profile coverage", stats, bundle)
        return
    if not any(x["paper_match"] for x in checked):
        _review(store, candidate, "Full name found but no corroborating publication title on site", stats, bundle)
        return
    if unverified_sections:
        _review(store, candidate, "Linked sections need identity/content review: " + ", ".join(unverified_sections), stats, bundle)
        return
    if partial:
        # Keep the task retryable; do not permanently discard unavailable sections.
        _retry(store, candidate, "Some linked pages are unavailable or redirect outside the person's site", stats)
        return
    candidate["evidence"] = dict(evidence, checks=checked, verified_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    outcome = store.approve_bundle(candidate, bundle, (person["first_name"], person["last_name"]))
    if outcome.get("held"):
        _review(store, candidate, outcome["reason"], stats, bundle)
        return
    inserted = outcome.get("inserted", [])
    stats["added_urls"] += len(inserted)
    stats["approved_sites"] += 1
    for row in bundle:
        known.add(url_key(row["url"]))


def run_site_mining_batch(*, apply=False, mode="all", source_limit=None,
                          candidate_limit=None, max_requests=None, max_seconds=None):
    """Run a bounded batch; default is a database-read-only network dry run."""
    if mode not in {"all", "cached", "directories"}:
        raise ValueError("mode must be all, cached, or directories")
    source_limit = int(source_limit if source_limit is not None else os.getenv("SITE_MINING_SOURCE_LIMIT", "500"))
    candidate_limit = int(candidate_limit if candidate_limit is not None else os.getenv("SITE_MINING_CANDIDATE_LIMIT", "30"))
    max_requests = int(max_requests if max_requests is not None else os.getenv("SITE_MINING_MAX_REQUESTS", "200"))
    max_seconds = int(max_seconds if max_seconds is not None else os.getenv("SITE_MINING_MAX_SECONDS", "600"))
    if min(source_limit, candidate_limit, max_requests, max_seconds) < 0 or max_seconds == 0:
        raise ValueError("Limits must be nonnegative and max_seconds must be positive")
    stats = dict(apply=apply, mode=mode, cached_pages=0, directories=0, queued=0,
                 verified=0, approved_sites=0, added_urls=0, review=0, retry=0,
                 requests=0, budget_exhausted=False)
    started = time.monotonic()
    fetcher = MiningFetcher(max_requests=max_requests, max_seconds=max_seconds)
    with MiningStore(apply=apply) as store:
        if not store.acquired:
            return dict(stats, skipped="another_mining_job_is_running")
        run_id = store.start_run()
        try:
            researchers = store.researchers()
            people = {p["id"]: p for p in researchers}
            names = defaultdict(list)
            for person in researchers:
                normalized = norm_name(_name(person))
                if len(normalized.split()) >= 2:
                    names[normalized].append(person)
            known = {url_key(row["url"]) for row in store.known_urls()}
            if mode in {"all", "cached"} and source_limit:
                _mine_cached(store, people, names, known, source_limit, stats, started + max_seconds)
            if mode in {"all", "directories"}:
                _mine_directories(store, fetcher, names, known, stats)
            for candidate in store.due_candidates(candidate_limit) if candidate_limit else []:
                try:
                    _verify(store, fetcher, candidate, people, names, known, stats)
                except BudgetExceeded:
                    store.save_result(candidate, "retry", "Deferred at this batch's network/time budget",
                                      retry_hours=1, count_attempt=False)
                    stats["retry"] += 1
                    raise
                stats["verified"] += 1
        except BudgetExceeded:
            stats["budget_exhausted"] = True
        except Exception as exc:
            stats.update(requests=fetcher.requests, elapsed_seconds=round(time.monotonic() - started, 2))
            store.finish_run(run_id, stats, error=f"{type(exc).__name__}: {exc}"[:1000])
            raise
        stats.update(requests=fetcher.requests, elapsed_seconds=round(time.monotonic() - started, 2))
        store.finish_run(run_id, stats)
    logger.info("Site mining completed: %s", json.dumps(stats))
    return stats
