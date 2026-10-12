"""Safety boundaries for automatic, network-free website candidate policy."""
import pytest

from backend.discovery.mining_policy import (
    MAX_CONTEXT_CHARS,
    extract_links,
    norm_name,
    normalized_url,
    page_type,
    paper_overlap,
    permitted_page,
    same_owner,
    scope_for,
    section_links,
    url_key,
    validate_identity,
)


ACADEMIC = (
    "Alice Smith is an assistant professor of economics at Example University. "
    "Her research interests include labor markets, education, and the economics "
    "of cities. Her working papers and published journal articles are listed below."
)
PAPER = "The Consequences of Local Credit Supply for Household Consumption"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/research", "http://[::1]/", "https://localhost/",
    "https://secret.internal/", "https://person:password@alice.test/",
    "https://alice.test:8080/", "https://alice.test/%2e%2e/bob/",
    "https://alice.test/%0aheader", "https://alice.test/a%5cb", "javascript:alert(1)",
    "mailto:alice@example.com", None,
])
def test_unsafe_urls_never_become_candidates(url):
    assert normalized_url(url) is None
    assert url_key(url) is None
    assert not permitted_page(url)
    assert not same_owner(scope_for('https://alice.test/'), url)


def test_url_key_dedupes_aliases_but_preserves_case_and_functional_query():
    first = "http://www.alice.test/Research/?tab=all&id=4&utm_source=letter#papers"
    second = "https://alice.test/Research?id=4&tab=all"
    assert url_key(first) == url_key(second)
    assert url_key(second) != url_key(second.replace('/Research', '/research'))
    assert url_key(second) != url_key(second.replace('id=4', 'id=5'))
    assert normalized_url('../papers?utm_source=x&view=all', 'https://alice.test/research/') == 'https://alice.test/papers?view=all'


@pytest.mark.parametrize('base,home,other', [
    ('https://sites.google.com/view/alice/research', 'https://sites.google.com/view/alice', 'https://sites.google.com/view/bob/research'),
    ('https://sites.google.com/a/college.edu/alice/research', 'https://sites.google.com/a/college.edu/alice', 'https://sites.google.com/a/college.edu/bob/research'),
    ('https://econ.example.edu/~alice/research.html', 'https://econ.example.edu/~alice', 'https://econ.example.edu/~bob/research.html'),
    ('https://example.edu/people/alice/research', 'https://example.edu/people/alice', 'https://example.edu/people/bob/research'),
    ('https://example.edu/people/a/alice/research', 'https://example.edu/people/a/alice', 'https://example.edu/people/a/bob/research'),
    ('https://example.edu/staff/profiles/business/alice/publications', 'https://example.edu/staff/profiles/business/alice', 'https://example.edu/staff/profiles/business/bob'),
    ('https://homepage.uni-graz.at/en/alice/research', 'https://homepage.uni-graz.at/en/alice', 'https://homepage.uni-graz.at/en/bob/research'),
    ('https://alice.github.io/research', 'https://alice.github.io/', 'https://bob.github.io/research'),
    ('https://alice.pages.iu.edu/research', 'https://alice.pages.iu.edu/', 'https://bob.pages.iu.edu/research'),
])
def test_home_inference_preserves_individual_boundary(base, home, other):
    scope = scope_for(base)
    assert scope.home_url == home
    assert same_owner(scope, home)
    assert not same_owner(scope, other, explicit=True)
    assert not same_owner(scope, 'https://example.edu/', explicit=True)


def test_person_file_preserves_name_and_other_people_are_rejected():
    scope = scope_for('https://example.edu/people/alice-smith.html')
    assert same_owner(scope, 'https://example.edu/people/alice-smith-publications.html')
    assert not same_owner(scope, 'https://example.edu/people/bob-smith-publications.html')
    assert not same_owner(scope, 'https://example.edu/people/alice-smithson-research.html')


def test_query_identity_survives_home_inference_and_repeated_selectors_fail():
    scope = scope_for('https://example.edu/profile.php?id=37&tab=research')
    assert scope.home_url == 'https://example.edu/profile.php?id=37'
    assert same_owner(scope, 'https://example.edu/profile.php?id=37&tab=publications')
    assert not same_owner(scope, 'https://example.edu/profile.php?tab=research')
    assert not same_owner(scope, 'https://example.edu/profile.php?id=38&tab=research')
    assert not same_owner(scope, 'https://example.edu/profile.php?id=38&id=37&tab=research')
    named = scope_for('https://example.edu/profile.php?page=alice&section=research')
    assert named.home_url == 'https://example.edu/profile.php?page=alice'
    assert not same_owner(named, 'https://example.edu/profile.php?page=bob&section=research')


@pytest.mark.parametrize('base', ['https://unknown-campus.com/', 'https://example.edu/profile', 'https://example.edu/people'])
def test_extensionless_and_root_query_profiles_keep_the_person(base):
    scope = scope_for(base + '?id=4&tab=research')
    assert scope.home_url == base + '?id=4'
    assert same_owner(scope, base + '?id=4&tab=publications')
    assert not same_owner(scope, base + '?id=5&tab=publications')
    assert not same_owner(scope, base)


def test_query_does_not_erase_an_existing_person_path():
    scope = scope_for('https://example.edu/person/alice/research?id=4')
    assert scope.home_url == 'https://example.edu/person/alice?id=4'
    assert same_owner(scope, 'https://example.edu/person/alice/publications?id=4')
    assert not same_owner(scope, 'https://example.edu/person/bob/publications?id=4')


def test_explicit_research_subdomains_work_but_tenant_and_campus_siblings_do_not():
    scope = scope_for('https://alice-smith.co.uk/research')
    assert same_owner(scope, 'https://papers.alice-smith.co.uk/', explicit=True)
    assert not same_owner(scope, 'https://papers.alice-smith.co.uk/')
    assert not same_owner(scope, 'https://bob.alice-smith.co.uk/', explicit=True)
    assert not same_owner(scope, 'https://bob-smith.co.uk/', explicit=True)
    assert not same_owner(scope_for('https://alice.github.io/'), 'https://research.alice.github.io/', explicit=True)
    assert not same_owner(scope_for('https://davidcard.berkeley.edu/'), 'https://economics.berkeley.edu/research', explicit=True)
    assert same_owner(scope_for('https://davidcard.berkeley.edu/'), 'https://davidcard.berkeley.edu/papers.html', explicit=True)


def test_generic_roots_cannot_expand_into_a_campus_directory():
    for url in ('https://example.edu/', 'https://example.edu/people', 'https://example.edu/people/directory'):
        scope = scope_for(url)
        assert scope.home_url is None
        assert not same_owner(scope, 'https://example.edu/people/alice/research', explicit=True)


@pytest.mark.parametrize('url', [
    'https://alice.test/paper.pdf', 'https://alice.test/papers/123-a-paper',
    'https://alice.test/article/policy', 'https://alice.test/news/policy',
    'https://doi.org/10.1/paper', 'https://twitter.com/alice',
    'https://alice.test/research?share=facebook', 'https://scholar.google.com/alice',
])
def test_non_listing_pages_are_rejected_even_with_research_label(url):
    assert not permitted_page(url, 'RESEARCH')


def test_section_classification_handles_nested_index_and_query_navigation():
    assert page_type('https://alice.test/research/index.html') == 'RESEARCH'
    assert page_type('https://alice.test/profile.php?id=4&tab=working-papers') == 'WORKING_PAPERS'
    assert page_type('https://alice.test/index.php?page_id=2', 'Publications') == 'PUBLICATIONS'
    assert page_type('https://alice.test/cv.html') is None


def test_all_sections_and_base_are_collected_without_guessing_paths():
    rows = section_links('''
      <a href="/">Home</a><a href="/publications">Publications</a>
      <a href="/working-papers">Working papers</a>
      <a href="/selected-papers">Selected papers</a>
      <a href="https://papers.alice.test/">Research</a>
      <a href="https://bob.test/">Research</a><a href="/one.pdf">Publications</a>
    ''', 'https://alice.test/research')
    assert {row['url']: row['page_type'] for row in rows} == {
        'https://alice.test/': 'HOME',
        'https://alice.test/research': 'RESEARCH',
        'https://papers.alice.test/': 'RESEARCH',
        'https://alice.test/publications': 'PUBLICATIONS',
        'https://alice.test/selected-papers': 'PUBLICATIONS',
        'https://alice.test/working-papers': 'WORKING_PAPERS',
    }
    assert section_links('', 'https://alice.test/') == [{'url': 'https://alice.test/', 'page_type': 'HOME'}]


def test_sections_never_add_another_campus_person_or_drop_query_selector():
    rows = section_links('''<a href="profile.php?id=4&tab=publications">Publications</a>
      <a href="profile.php?id=5&tab=publications">Publications</a>
      <a href="profile.php?tab=research">Research</a><a href="/">Home</a>''',
      'https://example.edu/profile.php?id=4&tab=research')
    assert all('id=4' in row['url'] for row in rows)
    assert {row['page_type'] for row in rows} == {'HOME', 'RESEARCH', 'PUBLICATIONS'}


def test_extract_links_uses_local_context_not_unrelated_papers_or_scripts():
    html = f'''<p>{PAPER}, with <a href="https://alice.test">Alice Smith</a>.</p>
      <p>A different paper, with <a href="https://bob.test">Bob Jones</a>.</p>
      <script><a href="https://bad.test">Alice Smith</a></script>
      <p hidden><a href="https://hidden.test">Hidden Person</a></p>'''
    links = extract_links(html, 'https://source.test/')
    assert len(links) == 2
    assert paper_overlap(links[0]['context'], [PAPER])
    assert not paper_overlap(links[1]['context'], [PAPER])


def test_extract_links_bounds_article_context_and_handles_relative_and_image_links():
    html = '<article>' + 'prefix ' * 2000 + '<a href="../research" aria-label="Research"></a>' + ' tail' * 2000 + '</article>'
    links = extract_links(html, 'https://alice.test/pages/list')
    assert links[0]['url'] == 'https://alice.test/research'
    assert len(links[0]['context']) <= MAX_CONTEXT_CHARS
    image = extract_links('<a href="/research"><img alt="Research papers"></a>', 'https://alice.test/')
    assert image[0]['label'] == 'Research papers'


def test_external_base_tag_does_not_retarget_all_relative_links():
    links = extract_links('<base href="https://other.test/"><a href="papers">Publications</a>', 'https://alice.test/')
    assert links[0]['url'] == 'https://alice.test/papers'


def test_visible_name_in_title_and_substantive_research_confirms_page_identity():
    result = validate_identity(f'<title>Alice Smith | Economics</title><main>{ACADEMIC}</main>', 'Alice Smith')
    assert result['confirmed']
    assert ACADEMIC == result['text']
    assert norm_name('François Søren Łukas') == 'francois soren lukas'


@pytest.mark.parametrize('name', ['A. Smith', 'Alice S.', 'Smith', ''])
def test_initial_only_names_require_review_even_with_matching_text(name):
    assert not validate_identity(f'<h1>{name}</h1><p>{ACADEMIC}</p>', name)['confirmed']


def test_body_name_or_hidden_heading_cannot_prove_ownership():
    assert not validate_identity(f'<title>Another Person</title><p>{ACADEMIC}</p>', 'Alice Smith')['confirmed']
    assert not validate_identity(f'<h1 hidden>Alice Smith</h1><p>{ACADEMIC}</p>', 'Alice Smith')['confirmed']


@pytest.mark.parametrize('heading', ['Faculty Directory', 'Our Team', 'Alice Smith Lab', 'Interview with Alice Smith', 'Alice Smith appointed professor'])
def test_directory_lab_and_article_pages_are_not_personal(heading):
    result = validate_identity(f'<title>{heading}</title><h2>Alice Smith</h2><p>{ACADEMIC}</p>', 'Alice Smith')
    assert not result['confirmed']


def test_multiple_secondary_person_headings_do_not_make_a_personal_site():
    html = f'<h2>Alice Smith</h2><p>{ACADEMIC}</p><h2>Bob Jones</h2>'
    assert not validate_identity(html, 'Alice Smith')['confirmed']
    group = f'<title>Alice Smith</title><h2>Our team</h2><h2>Alice Smith</h2><p>{ACADEMIC}</p><h2>Bob Jones</h2>'
    assert not validate_identity(group, 'Alice Smith')['confirmed']


@pytest.mark.parametrize('html', [
    '<title>Alice Smith - 404 Page Not Found</title>',
    '<title>Alice Smith</title><p>Buy this domain</p>',
    '<title>Alice Smith</title><input type="password">',
    '<title>Alice Smith</title><meta http-equiv="refresh" content="0;url=https://new.test">',
    '<title>Alice Smith</title><nav>Working papers Publications Research University</nav><p>Hello!</p>',
])
def test_error_auth_redirect_and_navigation_only_pages_need_review(html):
    assert not validate_identity(html, 'Alice Smith')['confirmed']


def test_name_match_does_not_drop_surname_tokens_or_assume_homonym_identity():
    html = f'<h1>Alice Smith</h1><p>{ACADEMIC}</p>'
    assert not validate_identity(html, 'Alice Smith Jones')['confirmed']
    assert validate_identity(html, 'Alice Q. Smith')['confirmed']
    # Same-name pages pass the page-level identity check; independent titles
    # still prevent automatic association to the wrong researcher record.
    assert validate_identity(html, 'Alice Smith')['confirmed']
    assert not paper_overlap(ACADEMIC, [PAPER])


def test_paper_corroboration_requires_a_long_complete_normalized_title():
    assert paper_overlap('Published: THE CONSEQUENCES OF LOCAL CREDIT SUPPLY FOR HOUSEHOLD CONSUMPTION (2025)', [PAPER])
    assert not paper_overlap('Credit supply for household consumption', [PAPER])
    assert not paper_overlap('Introduction Working Papers Trade and Growth', ['Introduction', 'Working Papers', 'Trade and Growth'])
    assert not paper_overlap('The Consequences of Local Credit Supply for Household Consumptions', [PAPER])


def test_unnamed_research_section_can_inherit_only_with_separate_owner_proof():
    body = ACADEMIC.replace('Alice Smith', 'I').replace('Her ', 'My ')
    proof = validate_identity(f'<title>Research</title><h1>Research</h1><p>{body}</p><p>{PAPER}</p>', 'Alice Smith')
    assert not proof['confirmed']
    assert proof['inheritable_section']
    assert paper_overlap(proof['text'], [PAPER])


@pytest.mark.parametrize('owner_heading', ['Bob Jones', 'Bob Jones | Publications', "Bob Jones’s Papers"])
def test_coauthors_named_papers_page_cannot_inherit_target_identity(owner_heading):
    body = ACADEMIC.replace('Alice Smith', 'Bob Jones')
    proof = validate_identity(f'<title>{owner_heading}</title><h1>Research</h1><p>{body}</p><p>{PAPER}</p>', 'Alice Smith')
    assert paper_overlap(proof['text'], [PAPER])
    assert not proof['confirmed']
    assert not proof['inheritable_section']


@pytest.mark.parametrize('extra', [
    '<input type="password">',
    '<meta http-equiv="refresh" content="0;url=https://other.test/">',
    '<h2>Bob Jones</h2>', '<h2>Our Team</h2>', '<h1>Faculty Directory</h1>',
    '<h1>Research Lab</h1>', '<h1>404 Page Not Found</h1>',
])
def test_generic_section_does_not_inherit_unsafe_or_nonpersonal_page(extra):
    proof = validate_identity(f'<title>Research</title><h1>Research</h1><p>{ACADEMIC}</p>{extra}', 'Alice Smith')
    assert not proof['inheritable_section']


def test_inheritance_flag_is_false_for_confirmed_and_insubstantial_pages():
    assert not validate_identity(f'<h1>Alice Smith</h1><p>{ACADEMIC}</p>', 'Alice Smith')['inheritable_section']
    assert not validate_identity('<h1>Research</h1><p>Coming soon</p>', 'Alice Smith')['inheritable_section']
