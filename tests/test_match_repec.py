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
