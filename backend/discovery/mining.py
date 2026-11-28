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
from backend.discovery.registry_sources import (
    IDEAS_INDEX_URLS, ideas_name_key, openalex_authors_url, openalex_headers,
    orcid_profile_url, orcid_record_url, orcid_websites, parse_ideas_author,
    parse_ideas_index, parse_openalex_authors,
)
from backend.database.identity import same_person

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


def _registry_settings(overrides=None):
    """Per-batch registry budget; it is a share of SITE_MINING_MAX_REQUESTS."""
    values = {
        "max_requests": int(os.getenv("SITE_MINING_REGISTRY_MAX_REQUESTS", "60")),
        # Off by default: OpenAlex lookups spend the shared paid
        # OPENALEX_DAILY_BUDGET, and the ORCID record endpoint needs sign-off.
        "orcid_authors": int(os.getenv("SITE_MINING_ORCID_AUTHORS", "0")),
        "repec_index_pages": int(os.getenv("SITE_MINING_REPEC_INDEX_PAGES", "1")),
        "delay_seconds": float(os.getenv("SITE_MINING_REGISTRY_DELAY_SECONDS", "2")),
        "openalex_daily_budget": int(os.getenv("OPENALEX_DAILY_BUDGET", "1000")),
        "openalex_mailto": os.getenv("OPENALEX_MAILTO", ""),
    }
    values.update({k: v for k, v in (overrides or {}).items() if v is not None})
    if min(values["max_requests"], values["orcid_authors"], values["repec_index_pages"],
           values["delay_seconds"]) < 0:
        raise ValueError("Registry limits must be nonnegative")
    return values


def registry_host_delays(settings):
    delay = settings["delay_seconds"]
    return {"orcid.org": delay, "ideas.repec.org": delay}


def _openalex_request_allowed(store, settings):
    """OpenAlex requests share the enrichment worker's per-UTC-day budget.

    Apply runs reserve a request; dry runs only check, so they stay read-only.
    """
    from backend.database.enrichment_budget import has_budget_remaining, reserve_request
    limit = settings["openalex_daily_budget"]
    return reserve_request("openalex", limit) if store.apply else has_budget_remaining("openalex", limit)


class _RegistryRoom:
    """The registry share of this batch's request budget."""

    def __init__(self, fetcher, limit):
        self.fetcher, self.stop_at = fetcher, fetcher.requests + limit

    def __call__(self, needed=1):
        return not self.fetcher.budget_exhausted and self.fetcher.requests + needed <= self.stop_at


def _registry_candidate(store, person, url, source_url, kind, evidence, names, known, stats):
    candidate = {"researcher_id": person["id"], "name": _name(person), "url": url,
                 "source_url": source_url, "source_kind": kind, "evidence": evidence}
    queued = bool(_enqueue(store, candidate, names, known))
    stats["queued"] += queued
    stats[f"{kind}_queued"] += queued
    return queued


def _mine_orcid(store, fetcher, room, names, known, stats, settings):
    """OpenAlex author -> ORCID iD -> websites on the ORCID record."""
    targets = store.orcid_lookup_targets(settings["orcid_authors"])
    for offset in range(0, len(targets), 50):
        chunk = targets[offset:offset + 50]
        # Robots + the batch request; then at least one ORCID record.
        if not room(3) or not _openalex_request_allowed(store, settings):
            return
        response = fetcher.fetch_json(openalex_authors_url([t["openalex_author_id"] for t in chunk],
                                                           settings["openalex_mailto"]),
                                      headers=openalex_headers())
        stats["openalex_requests"] += 1
        if response["status"] != 200 or "json" not in response:
            # Nothing is checkpointed, so the same authors are retried next batch.
            stats["registry_errors"] += 1
            return
        found = parse_openalex_authors(response["json"])
        for person in chunk:
            author = person["openalex_author_id"]
            key = "openalex:" + author
            info = found.get(author.upper())
            if not info:
                store.checkpoint("openalex_orcid", key, "", retry_hours=24 * 30)
                continue
            if not room(2):
                return
            orcid = info["orcid"]
            record = fetcher.fetch_json(orcid_record_url(orcid))
            stats["orcid_records"] += 1
            if record["status"] != 200 or "json" not in record:
                store.checkpoint("openalex_orcid", key, "", error=str(record.get("error") or record["status"]),
                                 retry_hours=24)
                stats["registry_errors"] += 1
                continue
            sites, reason = orcid_websites(record["json"], orcid, person["first_name"], person["last_name"])
            if reason:
                stats[reason] += 1
            # Links the iD holder added themselves come first.
            for site in sorted(sites, key=lambda site: not site["self_asserted"])[:3]:
                _registry_candidate(store, person, site["url"], orcid_profile_url(orcid), "orcid", {
                    "orcid": orcid, "openalex_author_id": author, "orcid_label": site["label"],
                    "self_asserted": site["self_asserted"], "orcid_name": site["orcid_name"],
                }, names, known, stats)
            store.checkpoint("openalex_orcid", key, _hash(orcid + "\n" + "\n".join(s["url"] for s in sites)),
                             error=reason if reason == "orcid_name_mismatch" else None, retry_hours=24 * 90)


def _mine_repec(store, fetcher, room, unlinked, known, stats, settings):
    """IDEAS author index -> unique exact-name match -> registered homepage."""
    by_name = defaultdict(list)
    for person in unlinked:
        key = norm_name(_name(person))
        if len(key.split()) >= 2:
            by_name[key].append(person)
    pages = 0
    for index_url in IDEAS_INDEX_URLS:
        if pages >= settings["repec_index_pages"] or not room(3):
            return
        if not store.source_due("repec_index", index_url):
            continue
        pages += 1
        response = fetcher.fetch(index_url)
        stats["repec_index_pages"] += 1
        if response["status"] != 200 or not response.get("html"):
            store.checkpoint("repec_index", index_url, "", error=response.get("error") or str(response["status"]),
                             retry_hours=24)
            stats["registry_errors"] += 1
            continue
        entries = parse_ideas_index(response["html"], response["url"])
        entries_by_name = defaultdict(list)
        for entry in entries:
            entries_by_name[ideas_name_key(entry["first_name"], entry["last_name"])].append(entry)
        finished = True
        for key, matches in entries_by_name.items():
            people = by_name.get(key, [])
            if not people:
                continue
            # Homonyms on either side stay with the other discovery sources.
            if len(matches) != 1 or len(people) != 1:
                stats["repec_ambiguous"] += 1
                continue
            entry, person = matches[0], people[0]
            if not store.source_due("repec_author", entry["url"]):
                continue
            if not room(1):
                finished = False
                break
            page = fetcher.fetch(entry["url"])
            stats["repec_author_pages"] += 1
            if page["status"] != 200 or not page.get("html"):
                store.checkpoint("repec_author", entry["url"], "", error=page.get("error") or str(page["status"]),
                                 retry_hours=24)
                stats["registry_errors"] += 1
                continue
            author = parse_ideas_author(page["html"], page["url"]) or {}
            reason = None
            if not author.get("last_name") or not same_person(
                    person["first_name"], person["last_name"], author["first_name"], author["last_name"]):
                reason = "repec_name_mismatch"
            elif not author.get("homepage"):
                reason = "repec_no_homepage"
            elif not paper_overlap(author["research_text"], store.paper_titles(person["id"])):
                # IDEAS lists the author's RePEc works: requiring one of our
                # titles there keeps homonyms out of the verification queue.
                reason = "repec_no_title_match"
            if reason:
                stats[reason] += 1
            else:
                _registry_candidate(store, person, author["homepage"], page["url"], "repec", {
                    "repec_short_id": author.get("short_id") or entry["short_id"],
                    "ideas_index": index_url, "repec_title_match": True,
                }, {}, known, stats)
            store.checkpoint("repec_author", entry["url"], _hash(page["html"]),
                             error=reason if reason == "repec_name_mismatch" else None, retry_hours=24 * 90)
        if finished:
            # A letter with more matches than one batch can fetch is read again
            # next batch; author pages already checkpointed are skipped.
            store.checkpoint("repec_index", index_url, _hash(response["html"]), retry_hours=168)


def _mine_registries(store, fetcher, names, known, stats, settings):
    if not settings["max_requests"]:
        return
    unlinked = store.unlinked_researchers()
    stats["unlinked_researchers"] = len(unlinked)
    start = fetcher.requests
    # ORCID may use half the registry share; RePEc gets whatever is left.
    _mine_orcid(store, fetcher, _RegistryRoom(fetcher, settings["max_requests"] // 2), names, known, stats, settings)
    remaining = settings["max_requests"] - (fetcher.requests - start)
    _mine_repec(store, fetcher, _RegistryRoom(fetcher, remaining), unlinked, known, stats, settings)


def _retry(store, candidate, reason, stats):
    exhausted = candidate.get("attempts", 0) >= 2
    status = "review" if exhausted else "retry"
    store.save_result(candidate, status, reason, retry_hours=24 * (2 ** min(candidate.get("attempts", 0), 4)))
    stats[status] += 1


def _review(store, candidate, reason, stats, urls=None):
    store.save_result(candidate, "review", reason, urls=urls)
    stats["review"] += 1


REGISTRY_KINDS = {"orcid", "repec"}


def _verify(store, fetcher, candidate, people, names, known, stats):
    # Registry sources look for a first homepage. Once one of a researcher's
    # registry links is approved (or the crawl gains a URL some other way),
    # the rest are left for review instead of adding more HOME pages.
    if (candidate["source_kind"] in REGISTRY_KINDS and candidate.get("researcher_id")
            and store.researcher_has_urls(candidate["researcher_id"])):
        _review(store, candidate, "Researcher already has a tracked URL; extra registry website not added", stats)
        return
    response = fetcher.fetch(candidate["url"])
    if response["status"] != 200 or not response.get("html"):
        _retry(store, candidate, "Destination unavailable: " + str(response.get("error") or response["status"]), stats)
        return
    name = candidate["name"]
    if candidate["source_kind"] == "directory_profile":
        # Profiles are durable tasks, so a large department resumes next batch.
        # Only the directory's named profile can supply personal-website links.
        # A business-card profile (Bonn) names the person but is too short for
        # the academic-content rule; error, multi-person and directory pages
        # are refused before that rule. Each linked website is then verified
        # in full on its own, so the profile only supplies candidates.
        profile = validate_identity(response["html"], name)
        if profile["confirmed"] or profile["reason"] == "insufficient_academic_content":
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
                          candidate_limit=None, max_requests=None, max_seconds=None,
                          registry=None):
    """Run a bounded batch; default is a database-read-only network dry run.

    ``registry`` overrides ``_registry_settings`` (SITE_MINING_REGISTRY_*).
    """
    if mode not in {"all", "cached", "directories", "registries"}:
        raise ValueError("mode must be all, cached, directories, or registries")
    registry = _registry_settings(registry)
    source_limit = int(source_limit if source_limit is not None else os.getenv("SITE_MINING_SOURCE_LIMIT", "500"))
    candidate_limit = int(candidate_limit if candidate_limit is not None else os.getenv("SITE_MINING_CANDIDATE_LIMIT", "30"))
    max_requests = int(max_requests if max_requests is not None else os.getenv("SITE_MINING_MAX_REQUESTS", "200"))
    max_seconds = int(max_seconds if max_seconds is not None else os.getenv("SITE_MINING_MAX_SECONDS", "600"))
    if min(source_limit, candidate_limit, max_requests, max_seconds) < 0 or max_seconds == 0:
        raise ValueError("Limits must be nonnegative and max_seconds must be positive")
    stats = defaultdict(int, apply=apply, mode=mode, cached_pages=0, directories=0, queued=0,
                        verified=0, approved_sites=0, added_urls=0, review=0, retry=0,
                        requests=0, budget_exhausted=False)
    started = time.monotonic()
    fetcher = MiningFetcher(max_requests=max_requests, max_seconds=max_seconds,
                            host_delays=registry_host_delays(registry))
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
            if mode in {"all", "registries"}:
                _mine_registries(store, fetcher, names, known, stats, registry)
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
    stats = dict(stats)
    logger.info("Site mining completed: %s", json.dumps(stats))
    return stats
