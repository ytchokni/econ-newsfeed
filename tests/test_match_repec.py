import pytest
import csv
from scripts.match_repec import parse_rdf_file

SAMPLE_RDF = """Template-Type: ReDIF-Person 1.0
Name-First: Arild
Name-Last: Aakvik
Name-Full: Arild Aakvik
Workplace-Name: Universitetet i Bergen
/ Institutt for Økonomi
Workplace-Institution: RePEc:edi:iouibno
Homepage: https://sites.google.com/site/aakvikarilduib/
Short-Id: paa1
Handle: REPEC:per:1966-08-13:arild_aakvik
"""

def test_parse_rdf_file(tmp_path):
    rdf = tmp_path / "paa1.rdf"
    rdf.write_text(SAMPLE_RDF)
    record = parse_rdf_file(str(rdf))
    assert record is not None
    assert record["name_first"] == "Arild"
    assert record["name_last"] == "Aakvik"
    assert record["name_full"] == "Arild Aakvik"
    assert record["workplace"] == "Universitetet i Bergen"
    assert record["homepage"] == "https://sites.google.com/site/aakvikarilduib/"
    assert record["handle"] == "REPEC:per:1966-08-13:arild_aakvik"


SAMPLE_RDF_NO_HOMEPAGE = """Template-Type: ReDIF-Person 1.0
Name-First: Rana
Name-Last: Abou El Azm
Workplace-Name: American University
Short-Id: pab100
Handle: REPEC:per:1988-01-26:rana_aly_abou_el_azm
"""

def test_parse_rdf_file_no_homepage(tmp_path):
    rdf = tmp_path / "pab100.rdf"
    rdf.write_text(SAMPLE_RDF_NO_HOMEPAGE)
    record = parse_rdf_file(str(rdf))
    assert record is None


from scripts.match_repec import build_repec_index

def test_build_repec_index(tmp_path):
    # Create two records under a subdirectory (mimics per/pers/a/)
    subdir = tmp_path / "a"
    subdir.mkdir()
    (subdir / "p1.rdf").write_text(SAMPLE_RDF)
    (subdir / "p2.rdf").write_text(SAMPLE_RDF_NO_HOMEPAGE)

    # Record with same name but different person
    dup_rdf = """Template-Type: ReDIF-Person 1.0
Name-First: Arild
Name-Last: Aakvik
Name-Full: Arild Aakvik
Workplace-Name: University of Oslo
Homepage: https://oslo.no/~aakvik
Handle: REPEC:per:1970-01-01:arild_aakvik2
"""
    (subdir / "p3.rdf").write_text(dup_rdf)

    by_name, by_domain = build_repec_index(str(tmp_path))

    # Name index: two records for ("arild", "aakvik"), none for no-homepage record
    assert len(by_name[("arild", "aakvik")]) == 2
    assert ("rana", "abou el azm") not in by_name

    # Domain index: both homepage domains present
    assert "sites.google.com" in by_domain
    assert "oslo.no" in by_domain


from scripts.match_repec import match_by_url, SHARED_HOSTING_DOMAINS

def test_match_by_url_custom_domain():
    """Non-shared domain: domain-only match + last name confirmation."""
    repec_records = [
        {"name_first": "Jaap", "name_last": "Abbring", "name_full": "Jaap Abbring",
         "workplace": "Tilburg", "homepage": "http://jaap.abbring.org/",
         "handle": "REPEC:per:abc"}
    ]
    by_domain = {"jaap.abbring.org": repec_records}
    researcher = {"id": 1, "first_name": "Jaap", "last_name": "Abbring",
                  "affiliation": None, "urls": ["https://jaap.abbring.org/research"]}
    matches = match_by_url(researcher, by_domain)
    assert len(matches) == 1
    assert matches[0]["match_type"] == "url_match"
    assert matches[0]["confidence"] == "unique"

def test_match_by_url_shared_domain_full_url():
    """Shared domain (sites.google.com): must compare full URL, not just domain."""
    rec_a = {"name_first": "Arild", "name_last": "Aakvik", "name_full": "Arild Aakvik",
             "workplace": "Bergen", "homepage": "https://sites.google.com/site/aakvikarilduib/",
             "handle": "REPEC:per:aakvik"}
    rec_b = {"name_first": "Other", "name_last": "Person", "name_full": "Other Person",
             "workplace": "MIT", "homepage": "https://sites.google.com/site/otherperson/",
             "handle": "REPEC:per:other"}
    by_domain = {"sites.google.com": [rec_a, rec_b]}
    researcher = {"id": 2, "first_name": "Arild", "last_name": "Aakvik",
                  "affiliation": None,
                  "urls": ["https://sites.google.com/site/aakvikarilduib/"]}
    matches = match_by_url(researcher, by_domain)
    assert len(matches) == 1
    assert matches[0]["repec_handle"] == "REPEC:per:aakvik"

def test_match_by_url_last_name_mismatch():
    """Domain matches but last name doesn't — no match."""
    repec_records = [
        {"name_first": "Jane", "name_last": "Doe", "name_full": "Jane Doe",
         "workplace": "MIT", "homepage": "http://example.com/",
         "handle": "REPEC:per:doe"}
    ]
    by_domain = {"example.com": repec_records}
    researcher = {"id": 3, "first_name": "John", "last_name": "Smith",
                  "affiliation": None, "urls": ["http://example.com/page"]}
    matches = match_by_url(researcher, by_domain)
    assert len(matches) == 0


from scripts.match_repec import match_by_name

def test_match_by_name_unique():
    """Single RePEC record for name → confidence: unique."""
    by_name = {
        ("arild", "aakvik"): [
            {"name_first": "Arild", "name_last": "Aakvik", "name_full": "Arild Aakvik",
             "workplace": "Bergen", "homepage": "https://example.com",
             "handle": "REPEC:per:aakvik"}
        ]
    }
    researcher = {"id": 1, "first_name": "Arild", "last_name": "Aakvik",
                  "affiliation": "Bergen", "urls": []}
    matches = match_by_name(researcher, by_name)
    assert len(matches) == 1
    assert matches[0]["confidence"] == "unique"

def test_match_by_name_affiliation_tiebreak():
    """Multiple records, DB affiliation matches one → confidence: affiliation_match."""
    by_name = {
        ("john", "smith"): [
            {"name_first": "John", "name_last": "Smith", "name_full": "John Smith",
             "workplace": "Massachusetts Institute of Technology",
             "homepage": "https://mit.edu/~jsmith", "handle": "REPEC:per:smith1"},
            {"name_first": "John", "name_last": "Smith", "name_full": "John Smith",
             "workplace": "University of Oxford",
             "homepage": "https://oxford.ac.uk/~smith", "handle": "REPEC:per:smith2"},
        ]
    }
    researcher = {"id": 2, "first_name": "John", "last_name": "Smith",
                  "affiliation": "MIT", "urls": []}
    matches = match_by_name(researcher, by_name)
    assert len(matches) == 1
    assert matches[0]["confidence"] == "affiliation_match"
    assert matches[0]["repec_handle"] == "REPEC:per:smith1"

def test_match_by_name_ambiguous():
    """Multiple records, no affiliation tiebreak → all written as ambiguous."""
    by_name = {
        ("wei", "zhang"): [
            {"name_first": "Wei", "name_last": "Zhang", "name_full": "Wei Zhang",
             "workplace": "Peking University",
             "homepage": "https://a.edu/zhang", "handle": "REPEC:per:zhang1"},
            {"name_first": "Wei", "name_last": "Zhang", "name_full": "Wei Zhang",
             "workplace": "Fudan University",
             "homepage": "https://b.edu/zhang", "handle": "REPEC:per:zhang2"},
        ]
    }
    researcher = {"id": 3, "first_name": "Wei", "last_name": "Zhang",
                  "affiliation": None, "urls": []}
    matches = match_by_name(researcher, by_name)
    assert len(matches) == 2
    assert all(m["confidence"] == "ambiguous" for m in matches)

def test_match_by_name_no_match():
    """Name not in index → no matches."""
    by_name = {}
    researcher = {"id": 4, "first_name": "Nobody", "last_name": "Here",
                  "affiliation": None, "urls": []}
    matches = match_by_name(researcher, by_name)
    assert len(matches) == 0


from scripts.match_repec import write_csv, CSV_COLUMNS

def test_write_csv(tmp_path):
    output = str(tmp_path / "out.csv")
    rows = [
        {"researcher_id": 1, "first_name": "Arild", "last_name": "Aakvik",
         "db_affiliation": "Bergen", "repec_name": "Arild Aakvik",
         "repec_workplace": "Universitetet i Bergen",
         "repec_homepage": "https://example.com", "repec_handle": "REPEC:per:aakvik",
         "match_type": "exact_name", "confidence": "unique"},
    ]
    write_csv(rows, output)

    import csv
    with open(output) as f:
        reader = csv.DictReader(f)
        result = list(reader)
    assert len(result) == 1
    assert result[0]["researcher_id"] == "1"
    assert result[0]["confidence"] == "unique"


from scripts.match_repec import parse_import_csv

def test_parse_import_csv(tmp_path):
    csv_path = str(tmp_path / "matches.csv")
    import csv
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow({
            "researcher_id": "1", "first_name": "Arild", "last_name": "Aakvik",
            "db_affiliation": "", "repec_name": "Arild Aakvik",
            "repec_workplace": "Universitetet i Bergen",
            "repec_homepage": "https://example.com", "repec_handle": "REPEC:per:aakvik",
            "match_type": "exact_name", "confidence": "unique",
        })
    rows = parse_import_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["researcher_id"] == 1
    assert rows[0]["repec_homepage"] == "https://example.com"
    assert rows[0]["repec_workplace"] == "Universitetet i Bergen"
