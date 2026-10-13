"""Regressions for false publication decisions and site-wide publication widgets."""
import zlib
from unittest.mock import patch

import pytest

from backend.llm.client import StructuredResponse
from backend.pipeline.html_fetcher import HTMLFetcher
from backend.pipeline.publication import (
    Publication, PublicationChangeList, PublicationExtractionList,
    verify_publication_evidence,
)


@pytest.mark.parametrize('wrapper', [
    '<footer>{}</footer>', '<div role="contentinfo">{}</div>',
    '<div class="region-footer">{}</div>', '<aside>{}</aside>',
])
def test_site_recommendations_removed_in_active_and_legacy_extractors(wrapper):
    html = '<main><p>My <em>actual</em> research paper</p></main>' + wrapper.format(
        '<h2>Publications</h2><a href="https://example.org/other">Unrelated paper</a>')
    for extract in (HTMLFetcher.extract_text_content, Publication.extract_relevant_html):
        text = extract(html)
        assert 'Unrelated' not in text
        assert HTMLFetcher.normalize_text(text) == 'My actual research paper'


def test_historical_html_uses_same_cleaning_as_current():
    html = '<main>My paper</main><footer>Unrelated published paper</footer>'
    with patch('backend.pipeline.html_fetcher.fetch_one', return_value={
        'raw_html_compressed': zlib.compress(html.encode()),
    }):
        assert HTMLFetcher.get_previous_text(1) == 'My paper'


def test_paper_title_inside_article_header_is_retained():
    text = HTMLFetcher.extract_text_content(
        '<header>Site navigation</header><main><article><header>Trade and Growth</header>'
        '<p>By Jane Doe</p></article></main>')
    assert 'Site navigation' not in text
    assert 'Trade and Growth' in text


@pytest.mark.parametrize('status,label', [
    ('revise_and_resubmit', 'under revision & temporarily unavailable'),
    ('revise_and_resubmit', 'under review'),
    ('revise_and_resubmit', 'revised October 2026'),
    ('revise_and_resubmit', 'not R&R'),
    ('revise_and_resubmit', 'never received an R&R'),
    ('revise_and_resubmit', 'not yet invited to revise and resubmit'),
    ('revise_and_resubmit', 'no revisions requested'),
    ('accepted', 'not yet accepted'),
])
def test_generic_revision_and_negated_acceptance_do_not_create_editorial_claims(status, label):
    text = 'Limits on Banks FX Forward Positions — ' + label
    pub = {'title': 'Limits on Banks FX Forward Positions', 'status': status,
           'status_evidence': text}
    verify_publication_evidence(pub, text)
    assert pub['status'] is None


@pytest.mark.parametrize('status,label', [
    ('revise_and_resubmit', 'R&R'),
    ('revise_and_resubmit', 'Revise and resubmit'),
    ('revise_and_resubmit', 'Revisions requested'),
    ('revise_and_resubmit', 'Invited to revise and resubmit'),
    ('accepted', 'forthcoming'),
    ('accepted', 'accepted for publication'),
])
def test_explicit_editorial_evidence_preserved_without_disclosed_journal(status, label):
    text = 'Trade and Growth. ' + label
    pub = {'title': 'Trade and Growth', 'status': status, 'venue': None,
           'status_evidence': text}
    verify_publication_evidence(pub, text)
    assert pub['status'] == status
    assert pub['venue'] is None


def test_editorial_evidence_cannot_be_invented_or_borrowed_from_another_title():
    pub = {'title': 'Trade and Growth', 'status': 'accepted',
           'status_evidence': 'Trade and Growth. Forthcoming'}
    verify_publication_evidence(pub, 'Trade and Growth. Working paper. Another paper. Forthcoming')
    assert pub['status'] is None

    pub.update(status='accepted', status_evidence='Trade and Growth. Another paper. Forthcoming')
    verify_publication_evidence(pub, pub['status_evidence'], ['trade and growth', 'another paper'])
    assert pub['status'] is None


def test_editorial_words_in_paper_title_are_not_decision_evidence():
    text = 'The Economics of Revise and Resubmit. Working paper'
    pub = {'title': 'The Economics of Revise and Resubmit',
           'status': 'revise_and_resubmit', 'status_evidence': text}
    verify_publication_evidence(pub, text)
    assert pub['status'] is None


def test_explicit_rr_with_intervening_byline_preserves_undisclosed_journal():
    title = 'A Validation Paper on Household Saving'
    entry = f'{title}\nAuthors: Jane Example\nR&R. Journal not disclosed.'
    pub = {'title': title, 'authors': [['Jane', 'Example']],
           'status': 'revise_and_resubmit', 'venue': None,
           'status_evidence': entry,
           'authors_evidence': f'{title}\nAuthors: Jane Example'}
    verify_publication_evidence(pub, f'Working Papers\n{entry}')
    assert pub['status'] == 'revise_and_resubmit'
    assert pub['venue'] is None


def test_series_is_not_editorial_venue_but_explicit_decision_is_preserved():
    text = 'Trade and Growth. R&R. NBER Working Paper 1234'
    pub = {'title': 'Trade and Growth', 'status': 'revise_and_resubmit',
           'venue': 'NBER Working Paper 1234', 'status_evidence': text}
    verify_publication_evidence(pub, text)
    assert pub['status'] == 'revise_and_resubmit'
    assert pub['venue'] is None


@pytest.mark.parametrize('diff', [False, True])
def test_evidence_guard_runs_in_full_and_diff_extraction(diff):
    text = 'Trade and Growth. Under revision and temporarily unavailable.'
    pub = {'title': 'Trade and Growth', 'authors': [['Jane', 'Doe']],
           'status': 'revise_and_resubmit', 'status_evidence': text}
    parsed = (PublicationChangeList(changes=[dict(pub, change_type='status_change')])
              if diff else PublicationExtractionList(publications=[pub]))
    with patch('backend.pipeline.publication.extract_json', return_value=StructuredResponse(parsed, None)):
        result = (Publication.try_extract_changes('old page', text, 'https://example.org') if diff
                  else Publication.try_extract_publications(text, 'https://example.org'))
    assert result.pubs[0]['status'] is None


@pytest.mark.parametrize('byline,complete', [
    ('by Jane Doe and John Smith', True),
    ('Authors: Jane Doe and John Smith', True),
    ('with Jane Doe and John Smith', False),
])
def test_only_verified_complete_bylines_can_exclude_page_owner(byline, complete):
    text = 'Trade and Growth. ' + byline
    pub = {'title': 'Trade and Growth', 'authors': [['Jane', 'Doe'], ['John', 'Smith']],
           'authors_evidence': text}
    verify_publication_evidence(pub, text)
    assert bool(pub.get('_authors_complete')) == complete


def test_fabricated_complete_byline_cannot_exclude_page_owner():
    pub = {'title': 'Trade and Growth', 'authors': [['Jane', 'Doe']],
           'authors_evidence': 'Trade and Growth. Authors: Jane Doe'}
    verify_publication_evidence(pub, 'Trade and Growth. With Jane Doe')
    assert not pub.get('_authors_complete')


def test_owner_hallucinated_by_model_is_removed_when_complete_byline_conflicts():
    text = 'Trade and Growth. By Jane Doe'
    pub = {'title': 'Trade and Growth',
           'authors': [['Jane', 'Doe'], ['Simeon', 'Djankov']], 'authors_evidence': text}
    verify_publication_evidence(pub, text)
    assert pub['_authors_complete'] is True
    assert pub['authors'] == [['Jane', 'Doe']]
