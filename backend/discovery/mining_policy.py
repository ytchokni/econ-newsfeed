"""Pure conservative policy for mining researchers' public website links.

These helpers do no networking or database writes. A scoped URL is a candidate,
not a verified page; callers must fetch it and verify its owner before approval.
DNS/rebinding protection belongs to the HTTP fetcher, not URL normalization.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
import unicodedata
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

INSTITUTES = {
    'worldbank.org', 'imf.org', 'bankofcanada.ca', 'bank.gov.ua', 'ecb.europa.eu',
    'bis.org', 'norges-bank.no', 'frb.gov', 'federalreserve.gov', 'newyorkfed.org',
    'chicagofed.org', 'philadelphiafed.org', 'stlouisfed.org', 'minneapolisfed.org',
    'bostonfed.org', 'atlantafed.org', 'clevelandfed.org', 'dallasfed.org',
    'kansascityfed.org', 'richmondfed.org', 'frbsf.org', 'iza.org', 'liser.lu',
    'ifpri.org', 'ifs.org.uk', 'wifo.ac.at', 'diw.de', 'ifo.de', 'zew.de',
    'rwi-essen.de', 'bruegel.org', 'motu.nz', 'upjohn.org', 'crei.cat',
    'crest.science', 'cemfi.es', 'bse.eu', 'nber.org', 'cepr.org', 'rand.org',
    'iwh-halle.de', 'tse-fr.eu', 'pse.ens.fr', 'parisschoolofeconomics.eu',
    'sciencespo.fr', 'sciencespo-aix.fr', 'tbs-education.com', 'insead.edu',
    'hec.fr', 'hec.edu', 'rff.org', 'brookings.edu', 'piie.com', 'wzb.eu',
}

FILE_EXT = re.compile(r'\.(?:pdf|docx?|xlsx?|pptx?|csv|tsv|txt|bib|ris|tex|ps|eps|zip|gz|tgz|rar|7z|jpe?g|png|gif|svg|webp|mp[34]|wav|avi|mov|rds|dta|sav|sas7bdat)(?:$|/)', re.I)
NON_PAGE_PART = re.compile(r'^(?:doi|article|articles|news|newsroom|press|press-releases?|events?|downloads?|attachments?|files?|wp-content|wp-admin|wp-login\.php|login|logout|signin|sign-in|search|tag|tags|category|categories)$', re.I)
REPOSITORY_HOSTS = {'doi.org','dx.doi.org','arxiv.org','ssrn.com','ideas.repec.org','econpapers.repec.org','nber.org','cepr.org','jstor.org','sciencedirect.com','link.springer.com','springer.com','researchgate.net','academia.edu','scholar.google.com','orcid.org','openalex.org','semanticscholar.org','osf.io','zenodo.org','dropbox.com','drive.google.com','docs.google.com','github.com','linkedin.com','facebook.com','twitter.com','x.com','accounts.google.com','researchmap.jp','archive.org','web.archive.org'}
TENANT_HOSTS = ('github.io','weebly.com','wordpress.com','wixsite.com','squarespace.com','netlify.app','vercel.app','blogspot.com','webnode.com','jimdosite.com','mystrikingly.com')
PROFILE_MARKERS = {'people','person','persons','faculty','staff','profiles','profile','researcher','researchers','experts','expert','employees','employee','members','member','users','user','personal','biography','bio','fellow','fellows','professor','professors'}
PROFILE_CATEGORIES = {'faculty','staff','academic','academic-staff','research-staff','faculty-and-research','emeritus','visiting','phd-students','doctoral-students','all','en','fr'}
LANGUAGES = {'en','eng','english','fr','de','it','es','pt','nl','ja','zh','cn','ko','no','nb','nn','da','sv'}
TRACKING = {'fbclid','gclid','dclid','msclkid','mc_cid','mc_eid','_ga','_gl','igshid'}
ROUTING = {'page','p','section','tab','view','menu','content','action','lang','language','locale'}
HOME_LEAVES = {'','home','homepage','home-page','index','default','about','about-me','aboutme','welcome','start','accueil','inicio'}
ABOUT_LEAVES = {'about','about-me','aboutme','about-us','aboutus'}
CAMPUS_HOST_TENANTS = ('pages.iu.edu','mycpanel.princeton.edu','sites.tau.ac.il','people.wm.edu',
    'people.stanford.edu','people.clemson.edu','web.unc.edu','host.dartmouth.edu',
    'facultysite.georgetown.edu','uvacreate.virginia.edu','faculty.ucdavis.edu',
    'sites.ucsc.edu','net.technion.ac.il','faculty.economics.utoronto.ca')
# Exact academic hosts whose cached title/heading identifies the researcher.
# These do not confer ownership of sibling department/university subdomains.
PERSONAL_ACADEMIC_HOSTS = {
    'abrahamneyman.huji.ac.il','jadbabaie.mit.edu','anujit.ucdavis.edu',
    'ageromichalos.ucdavis.edu','avraham-ebenstein.huji.ac.il','sprenger.caltech.edu',
    'dho.stanford.edu','davidcard.berkeley.edu','emiguel.econ.berkeley.edu',
    'moretti.econ.berkeley.edu','rossihansberg.economics.uchicago.edu',
    'prasad.dyson.cornell.edu','fotini.mit.edu','shapiro.macmillan.yale.edu',
    'bushnell.ucdavis.edu','fletcher.lafollette.wisc.edu','liaukonyte.dyson.cornell.edu',
    'hasker.bilkent.edu.tr','imai.fas.harvard.edu','saito.caltech.edu',
    'agranov.caltech.edu','michaelkremer.economics.uchicago.edu',
    'rglennerster.ssd.uchicago.edu','allred.camden.rutgers.edu','zke.fas.harvard.edu',
    'valerieramey.stanford.edu'}
PERSONAL_NAV_LEAVES = {'cv','curriculum-vitae','vitae','vita','resume'}
REGISTRY_DOUBLE = {'co.uk','org.uk','ac.uk','com.au','org.au','edu.au','co.nz','ac.nz','co.jp','ac.jp','com.br','com.cn','com.hk','com.sg','co.in','co.za','co.kr','com.tr','com.tw','com.ar','com.mx'}


def norm_name(value):
    """Accent/punctuation-insensitive matching; not a proof of identity."""
    value = str(value or '').translate(str.maketrans({'ø':'o','Ø':'O','æ':'ae','Æ':'AE','œ':'oe','Œ':'OE','ß':'ss','ł':'l','Ł':'L'}))
    value = ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')) if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


def normalized_url(url, base=None):
    try:
        if not isinstance(url, str) or not url or re.search(r'[\x00-\x1f\x7f]', url):
            return None
        url = urljoin(base, url.strip()) if base else url.strip()
        p = urlsplit(url)
        if p.scheme.lower() not in {'http','https'} or not p.hostname or p.username is not None or p.password is not None:
            return None
        if p.port not in (None, 80 if p.scheme == 'http' else 443):
            return None
        host = p.hostname.rstrip('.').lower().encode('idna').decode('ascii')
        if host in {'localhost'} or host.endswith(('.localhost','.local','.internal')):
            return None
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global or address.is_reserved or address.is_multicast:
                return None
        except ValueError:
            pass
        # Decode only for traversal checks. Preserve existing escapes and path
        # case; encode ordinary spaces without permitting control characters.
        decoded = unquote(p.path)
        if re.search(r'[\x00-\x1f\x7f\\]', decoded) or any(part in {'.','..'} for part in decoded.split('/')):
            return None
        path = quote(p.path or '/', safe="/%:@!$&'()*+,;=-._~")
        query = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
                 if k.lower() not in TRACKING and not k.lower().startswith('utm_')]
        netloc = '[' + host + ']' if ':' in host else host
        return urlunsplit((p.scheme, netloc, path, urlencode(query, doseq=True), ''))
    except (AttributeError, TypeError, UnicodeError, ValueError):
        return None


def url_key(url):
    """Comparison key, retaining case and every non-tracking query parameter."""
    cleaned = normalized_url(url)
    if not cleaned:
        return None
    parts = urlsplit(cleaned)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit(('', host_key(cleaned), parts.path.rstrip('/'), query, ''))


def host_key(url):
    return (urlsplit(url).hostname or '').removeprefix('www.').lower()


def leaf_value(path):
    leaf = unquote(path).rstrip('/').rsplit('/',1)[-1]
    return re.sub(r'\.(?:html?|php|aspx?)$', '', leaf, flags=re.I).lower()


def section_type(value):
    value = norm_name(value)
    if not value:
        return None
    value = re.sub(r'^(?:my|our) ', '', value)
    value = re.sub(r'\s+(?:page|section)$', '', value)
    value = re.sub(r'\s+\d{1,4}$', '', value)
    compact = value.replace(' ','')
    if compact in {'workingpaper','workingpapers','workinprogress','workprogress','wip','ongoingpapers','unpublishedpapers'}:
        return 'WORKING_PAPERS'
    if compact in {'publication','publications','paper','papers','selectedpaper','selectedpapers','selectedpublications','publishedpapers','publishedwork','publishedresearch','journalarticles','publikationen','publicaciones','publicacoes','publicationsrefereed'}:
        return 'PUBLICATIONS'
    if compact in {'research','researchpapers','researchinterests','researchoutput','researchoutputs','researchprojects','currentresearch','recentresearch','forschung','recherche','investigacion','ricerca'}:
        return 'RESEARCH'
    if value.replace(' ','-') in HOME_LEAVES or compact in {'mainpage','personalhomepage','personalwebsite'}:
        return 'HOME'
    return None


def page_type(url, label=''):
    url = normalized_url(url)
    if not url:
        return None
    p = urlsplit(url)
    leaf = leaf_value(p.path)
    if leaf in {'index','default'}:
        parent_type = section_type(leaf_value(p.path.rsplit('/',1)[0]))
        if parent_type and parent_type != 'HOME':return parent_type
    candidate = section_type(leaf)
    if candidate and candidate != 'HOME':
        return candidate
    for key, value in parse_qsl(p.query):
        if key.lower() in ROUTING and section_type(value):
            return section_type(value)
    labelled = section_type(label)
    if labelled:
        return labelled
    return candidate if leaf in HOME_LEAVES else None


def permitted_page(url, kind=None):
    url = normalized_url(url)
    if not url:
        return False
    p = urlsplit(url)
    host = host_key(url)
    if any(host == other or host.endswith('.'+other) for other in REPOSITORY_HOSTS):
        return False
    if host != 'sites.google.com' and re.search(r'(^|\.)google\.', host):
        return False
    if any(k.lower()=='share' and v.lower() in {'facebook','twitter','linkedin','pinterest','telegram','email','reddit'} for k,v in parse_qsl(p.query)):
        return False
    if FILE_EXT.search(unquote(p.path)):
        return False
    if re.search(r'(^|\.)(?:repository|repositorio|eprints|scholarworks|archives|archive|papers|publications)\.', host) and not kind:
        return False
    parts = [part for part in unquote(p.path).split('/') if part]
    # A /papers or /publications listing can qualify, but not one paper under it.
    for index, part in enumerate(parts):
        if NON_PAGE_PART.match(part):
            return False
        if part.lower() in {'paper','papers','publication','publications','working-papers','working_papers'} and index < len(parts)-1:
            tail = parts[index+1:]
            if not (len(tail) == 1 and (section_type(leaf_value(tail[0])) or re.fullmatch(r'(?:19|20)\d{2}', leaf_value(tail[0])))):
                return False
    return True


def institutional(host):
    return bool(re.search(r'(^|\.)(?:edu|ac)(?:\.|$)|university|universit|(?:^|[.\-])uni(?:v)?[.\-]|(?:^|\.)(?:users?|people|staff|faculty|profiles?|researchers?)\.', host)) or any(host == d or host.endswith('.'+d) for d in INSTITUTES) or any(value in host for value in ('sciencespo','uib.no','bundesbank','bankof','federalreserve','econweb','web.mit.edu','campuspress','sites.stanford.edu'))


def registrable(host):
    parts = host.split('.')
    n = 3 if '.'.join(parts[-2:]) in REGISTRY_DOUBLE else 2
    return '.'.join(parts[-n:])


def _identity_query(query):
    # "page=alice" is an identity selector even though "page=research" is
    # navigation. Preserve unknown routing values instead of widening ownership.
    return tuple(
        (key, value) for key, value in parse_qsl(query, keep_blank_values=True)
        if not (
            key.lower() in ROUTING
            and (section_type(value) or (key.lower() in {'lang', 'language', 'locale'} and value.lower() in LANGUAGES))
        )
    )


@dataclass(frozen=True)
class Scope:
    host: str
    prefix: str
    mode: str
    home_url: str | None
    identity_query: tuple = ()
    personal_domain: str | None = None
    person_file_stem: str | None = None


def scope_for(url):
    url = normalized_url(url)
    if not url:
        return Scope('', '', 'exact', None)
    p = urlsplit(url);host = host_key(url)
    parts = [part for part in unquote(p.path).split('/') if part]
    identity = _identity_query(p.query)
    scheme = p.scheme
    def home(prefix,identity=()):
        return normalized_url(urlunsplit((scheme,p.netloc,prefix or '/', urlencode(identity), '')))
    def result(prefix,mode,infer=True,identity=(),domain=None,stem=None):
        inferred=home(prefix,identity) if infer else None
        if inferred:
            q=urlsplit(inferred)
            if unquote(p.path).rstrip('/')==unquote(q.path).rstrip('/') and sorted(parse_qsl(p.query))==sorted(parse_qsl(q.query)):
                inferred=url
        return Scope(host,prefix,mode,inferred,tuple(identity),domain,stem)
    if host == 'sites.google.com':
        length = 3 if len(parts)>=3 and parts[0]=='a' else 2
        if len(parts) < length:
            return result(p.path,'exact',False)
        return result('/'+'/'.join(parts[:length]),'google_tenant')
    for index,part in enumerate(parts):
        if part.startswith('~') and len(part)>1:
            return result('/'+'/'.join(parts[:index+1]),'tilde_tenant')
    if host in PERSONAL_ACADEMIC_HOSTS or any(host.endswith('.'+suffix) for suffix in CAMPUS_HOST_TENANTS):
        return result('','host_tenant')
    for suffix in TENANT_HOSTS:
        if host.endswith('.'+suffix):
            if suffix=='wixsite.com':
                return result('/'+parts[0],'host_path_tenant') if parts else result('/', 'exact', False)
            return result('','host_tenant')
        if host==suffix:
            return result(p.path,'exact',False)
    # Query-selected people can live at a bare domain or extensionless endpoint.
    # Recognize those before generic-directory and custom-domain handling.
    if identity and (
        not parts
        or parts[-1].lower() in PROFILE_MARKERS | {'directory'}
        or (
            not any(part.lower() in PROFILE_MARKERS for part in parts[:-1])
            and any(key.lower() in {'id', 'person', 'person_id', 'researcher', 'researcher_id', 'user', 'uid', 'username', 'netid', 'profile', 'up'} for key, _ in identity)
        )
    ):
        return result(p.path or '/', 'query_profile', True, identity)
    if parts and parts[-1].lower() in PROFILE_MARKERS | PROFILE_CATEGORIES | {'directory', 'team'}:
        return result(p.path, 'exact', False)
    markers=[index for index,part in enumerate(parts[:-1]) if part.lower() in PROFILE_MARKERS]
    if markers:
        # A marker can be followed by organization/department/alphabet buckets.
        # Preserve the entire known person path, stripping only recognized page
        # sections; /people/a/name must never confer ownership of /people/a.
        meaningful=list(parts)
        while len(meaningful)>markers[-1]+2:
            last=leaf_value(meaningful[-1])
            if section_type(last) or last in {'biography','bio','cv','overview','mijnwebsite'} or last in LANGUAGES or re.fullmatch(r'(?:index|home|default)(?:[0-9]+|[_-][a-z]{2,3})',last):
                meaningful.pop()
            else:break
        prefix='/'+'/'.join(meaningful)
        leaf=meaningful[-1]
        identity=_identity_query(p.query)
        if re.search(r'\.(?:html?|php|aspx?)$',leaf,re.I):
            stem=re.sub(r'\.(?:html?|php|aspx?)$','',leaf,flags=re.I)
            # Repeated person slug in /name/name.html safely identifies a tenant.
            if len(meaningful)>1 and stem.lower()==meaningful[-2].lower():
                return result('/'+'/'.join(meaningful[:-1]),'person_path',True,identity)
            return result(prefix,'person_file',False,identity,stem=stem)
        return result(prefix,'person_path',True,identity)
    identity=_identity_query(p.query)
    if identity and re.search(r'\.(?:php|aspx?)$',p.path,re.I):
        return result(p.path,'query_profile',True,identity)
    is_institution=institutional(host)
    meaningful=list(parts)
    while meaningful and (section_type(leaf_value(meaningful[-1])) or leaf_value(meaningful[-1]) in HOME_LEAVES | PERSONAL_NAV_LEAVES):
        meaningful.pop()
    if is_institution:
        if not parts:
            return result('/','exact',False)
        if meaningful:
            prefix='/'+'/'.join(meaningful)
            if re.search(r'\.(?:html?|php|aspx?)$',prefix,re.I):
                return result(prefix,'person_file',False,identity,stem=leaf_value(prefix))
            return result(prefix,'campus_path',True,identity)
        return result(p.path,'exact',False)
    # Unknown organizational hosts do not acquire whole-domain ownership merely
    # because their person profile was confirmed. Keep its full path boundary.
    if meaningful and any(part.lower() not in LANGUAGES for part in meaningful):
        prefix='/'+'/'.join(meaningful)
        if re.search(r'\.(?:html?|php|aspx?)$',prefix,re.I):
            return result(prefix,'person_file',False,identity,stem=leaf_value(prefix))
        return result(prefix,'conservative_path',True,identity)
    return result('','personal_domain',True,domain=registrable(host))


def same_owner(scope, url, explicit=False):
    url = normalized_url(url)
    if not url or not scope.host:
        return False
    host=host_key(url);p=urlsplit(url);path=unquote(p.path).rstrip('/') or '/'
    if host != scope.host:
        # Dedicated public-hosting tenants and university hosts cannot expand
        # across subdomains. Custom domains may, only through an observed link.
        if not (explicit and scope.mode=='personal_domain' and registrable(host)==scope.personal_domain):
            return False
        if institutional(host) or any(host==suffix or host.endswith('.'+suffix) for suffix in TENANT_HOSTS):
            return False
        # Only explicit research subdomains imply the same personal site.
        root = scope.personal_domain or scope.host
        research_prefixes = {'research', 'papers', 'publications', 'workingpapers', 'working-papers'}
        if host != root and host.removesuffix('.'+root) not in research_prefixes:
            return False
        if scope.host != root and scope.host.removesuffix('.'+root) not in research_prefixes:
            return False
        return True
    current_query=parse_qsl(p.query,keep_blank_values=True)
    if any([value for key, value in current_query if key == k] != [value for key, value in scope.identity_query if key == k] for k, _ in scope.identity_query):
        return False
    if scope.mode in {'personal_domain','host_tenant'}:
        return True
    prefix=scope.prefix.rstrip('/') or '/'
    if scope.mode in {'exact','query_profile'}:
        return path==prefix
    if scope.mode=='person_file':
        if path==prefix:return True
        base_parent=prefix.rsplit('/',1)[0]
        if path.rsplit('/',1)[0] != base_parent:return False
        leaf=leaf_value(path);stem=(scope.person_file_stem or '').lower()
        if not leaf.startswith(stem):return False
        suffix=leaf[len(stem):].lstrip('-_.')
        return bool(suffix and section_type(suffix))
    return path==prefix or path.startswith(prefix+'/')


MAX_HTML_CHARS = 3_000_000
MAX_LINKS = 2_000
MAX_CONTEXT_CHARS = 2_000


def _soup(html):
    """Parse a bounded document and discard non-visible instructions/content."""
    soup = BeautifulSoup(str(html or '')[:MAX_HTML_CHARS], 'html.parser')
    for tag in soup.select('script,style,noscript,svg,template,[hidden],[aria-hidden="true"]'):
        tag.decompose()
    return soup


def _text(tag):
    return ' '.join(tag.get_text(' ', strip=True).split())


def extract_links(html, base_url):
    """Return visible links and bounded local evidence, never page-wide context.

    A paper title in the closest paragraph/list item/article can corroborate its
    linked coauthor. Text from an unrelated paper elsewhere in the page cannot.
    HTML base overrides are accepted only inside the original owner's scope.
    """
    base_url = normalized_url(base_url)
    if not base_url:
        return []
    soup = _soup(html)
    base = soup.find('base', href=True)
    if base:
        proposed = normalized_url(base.get('href'), base_url)
        if proposed and same_owner(scope_for(base_url), proposed, explicit=True):
            base_url = proposed
    result = []
    seen = set()
    for anchor in soup.find_all('a', href=True, limit=MAX_LINKS):
        href = str(anchor.get('href') or '').strip()
        if not href or href.startswith('#'):
            continue
        url = normalized_url(href, base_url)
        if not url:
            continue
        label = _text(anchor) or str(anchor.get('aria-label') or anchor.get('title') or '')
        if not label:
            img = anchor.find('img', alt=True)
            if img:
                label = str(img.get('alt') or '')
        label = ' '.join(label.split())[:300]
        parent = anchor.find_parent(['p', 'li', 'article'])
        context = _text(parent) if parent else label
        # Center long contexts on the anchor when its label is available.
        offset = context.find(label) if label else -1
        if len(context) > MAX_CONTEXT_CHARS and offset >= 0:
            start = max(0, offset - MAX_CONTEXT_CHARS // 2)
            context = context[start:start + MAX_CONTEXT_CHARS]
        else:
            context = context[:MAX_CONTEXT_CHARS]
        key = (url_key(url), label, context)
        if key in seen:
            continue
        seen.add(key)
        result.append({'url': url, 'label': label, 'context': context})
    return result


def section_links(html, base_url):
    """Find candidate HOME and all explicit research listings inside one owner.

    Inferred home URLs require a later successful fetch. No conventional path
    guesses are emitted for research pages: each one must be an observed link.
    """
    base_url = normalized_url(base_url)
    if not base_url or not permitted_page(base_url):
        return []
    scope = scope_for(base_url)
    rows = {}

    def add(url, kind):
        if url and kind and permitted_page(url, kind):
            key = url_key(url)
            if key not in rows or kind == 'HOME':
                rows[key] = {'url': url, 'page_type': kind}

    add(base_url, page_type(base_url) or 'HOME')
    if scope.home_url and same_owner(scope, scope.home_url):
        add(scope.home_url, 'HOME')
    for link in extract_links(html, base_url):
        if re.match(r'^(?:search|chercher)\b', norm_name(link['label'])):
            continue
        url = link['url']
        kind = page_type(url, link['label'])
        if kind and same_owner(scope, url, explicit=True):
            add(url, kind)
    priority = {'HOME': 0, 'RESEARCH': 1, 'PUBLICATIONS': 2, 'WORKING_PAPERS': 3}
    return sorted(rows.values(), key=lambda row: (priority[row['page_type']], row['url']))


_ERROR = re.compile(
    r'\b(?:404|page (?:not found|does not exist|is unavailable)|access denied|'
    r'just a moment|verify (?:that )?you are human|checking your browser|'
    r'website under construction|coming soon|domain (?:name )?(?:is )?for sale|'
    r'buy this domain|domain has expired|domain parking)\b', re.I,
)
_NONPERSONAL_HEADING = re.compile(
    r'\b(?:directory|our team|team members|research group|laboratory|lab|'
    r'interview with|obituary|in memoriam|awarded|wins award|appointed|'
    r'symposium|conference programme|conference program)\b', re.I,
)
_GENERIC_PRIMARY = re.compile(
    r'^(?:people|faculty|staff|team|members|our people|our faculty|our staff|'
    r'department of .+|school of .+|college of .+|university of .+)\s*$', re.I,
)
_ROLE = re.compile(
    r'\b(?:professor|lecturer|economist|researcher|research (?:fellow|scientist|associate|analyst)|'
    r'postdoc(?:toral)?|ph\.?\s?d\.? (?:student|candidate)|doctoral (?:student|candidate))\b', re.I,
)
_RESEARCH = re.compile(
    r'\b(?:working papers?|publications?|published papers?|research interests?|research areas?|'
    r'my research|research fields?|curriculum vitae|academic papers?|journal articles?|'
    r'journal of|dissertation|university|department of economics|school of economics)\b', re.I,
)
_HEADING_STOP = {
    'research', 'papers', 'publications', 'working', 'selected', 'contact', 'information',
    'team', 'members', 'group', 'projects', 'teaching', 'university', 'school', 'of', 'and',
    'the', 'our', 'academic', 'economics', 'personal', 'profile', 'cv', 'about', 'home',
    'news', 'data', 'science', 'education', 'service', 'awards', 'honors', 'interests',
}


def _name_match(name, value):
    target = norm_name(name)
    target = re.sub(r'^(?:(?:prof|professor|dr)\s+)+', '', target)
    words = target.split()
    if len(words) < 2 or len(words[0]) < 2 or len(words[-1]) < 2:
        return False
    text = ' ' + norm_name(value) + ' '
    if ' ' + target + ' ' in text:
        return True
    # Omitted middle initials are common; full extra surname tokens must remain.
    full_words = [word for index, word in enumerate(words) if not (0 < index < len(words)-1 and len(word) == 1)]
    return ' ' + ' '.join(full_words) + ' ' in text


def _looks_person_heading(value):
    words = value.split()
    normalized = norm_name(value).split()
    return (
        2 <= len(words) <= 5
        and len(value) < 65
        and not set(normalized) & _HEADING_STOP
        and all(word[0].isupper() or word.lower() in {'de', 'van', 'von', 'der', 'da', 'del', 'di'} for word in words)
    )


def _conflicting_person_heading(value, name):
    """Find a named owner even in 'Bob Jones | Papers' or possessive titles."""
    if _name_match(name, value):
        return False
    parts = re.split(r'[|:–—]', value)
    for part in parts:
        stripped = re.sub(r"['’]s\b", '', part)
        stripped = re.sub(
            r'\b(?:my|selected|published|working|research|publications?|papers?|page|website|homepage|of)\b',
            ' ', stripped, flags=re.I,
        )
        stripped = ' '.join(stripped.strip(' -').split())
        if _looks_person_heading(stripped):
            return True
    return False


def validate_identity(html, name):
    """Require a distinctive full-name heading and substantive academic content.

    This establishes that the page presents *a* scholar with the supplied name;
    it does not resolve two scholars sharing that name. The caller must require
    independent ownership/coauthorship evidence before automatically adding it.
    ``inheritable_section`` is only eligibility for an unnamed generic research
    page: the caller must separately require a verified owner on the same host
    and a matching paper title. It never overrides a conflicting named owner.
    """
    soup = _soup(html)
    title = _text(soup.title) if soup.title else ''
    primary = [_text(tag) for tag in soup.find_all('h1', limit=12)]
    headings = [_text(tag) for tag in soup.find_all(['h1', 'h2'], limit=40)]
    main = soup.find('main') or soup.find('article') or soup.body or soup
    core = BeautifulSoup(str(main), 'html.parser')
    for tag in core.select('nav,header,footer,[role="navigation"],.cookie-banner,.cookie-consent'):
        tag.decompose()
    text = _text(core)

    def result(confirmed, reason, inheritable_section=False):
        return {'confirmed': confirmed, 'reason': reason, 'text': text,
                'inheritable_section': inheritable_section}

    if not _name_match(name, name):
        return result(False, 'full_name_required')
    prominent = ' '.join([title] + primary)
    if _ERROR.search(prominent) or (len(text) < 1600 and _ERROR.search(text[:600])):
        return result(False, 'error_or_parked_page')
    if soup.find('input', attrs={'type': re.compile(r'^password$', re.I)}):
        return result(False, 'authentication_required')
    if _NONPERSONAL_HEADING.search(prominent):
        return result(False, 'nonpersonal_or_article_page')
    if any(str(meta.get('http-equiv', '')).lower() == 'refresh' for meta in soup.find_all('meta')):
        return result(False, 'redirect_requires_verification')
    if re.search(r'\b(?:my|this|the) (?:web\s?page|website|home\s?page|site) (?:has |is )?(?:now )?moved\b', text[:1500], re.I):
        return result(False, 'moved_page')
    primary_match = any(len(value) <= 220 and _name_match(name, value) for value in [title] + primary)
    distinctive = primary_match or any(len(value) <= 180 and _name_match(name, value) for value in headings)
    if not primary_match and any(_GENERIC_PRIMARY.match(value.split('|')[0].strip()) for value in [title] + primary):
        return result(False, 'directory_or_institution_page')
    person_headings = {norm_name(value) for value in headings if _looks_person_heading(value)}
    if len(person_headings) > 1 and re.search(r'\b(?:our team|team members|research group|group members|laboratory members)\b', text, re.I):
        return result(False, 'multiple_people_page')
    if not primary_match and len(person_headings) > 1:
        return result(False, 'multiple_people_page')
    academic_hits = {norm_name(match.group(0)) for match in _RESEARCH.finditer(text)}
    has_role = bool(_ROLE.search(text))
    substantive = len(text) >= 120 and len(text.split()) >= 20
    if not substantive or not (has_role and academic_hits or len(academic_hits) >= 2):
        return result(False, 'insufficient_academic_content')
    if not distinctive:
        generic_section = any(
            section_type(part.strip()) in {'RESEARCH', 'PUBLICATIONS', 'WORKING_PAPERS'}
            for value in [title] + primary for part in re.split(r'[|:–—]', value)
        )
        conflicting_owner = any(_conflicting_person_heading(value, name) for value in [title] + headings)
        nonpersonal = any(_NONPERSONAL_HEADING.search(value) for value in headings)
        return result(False, 'name_missing_from_distinctive_heading',
                      generic_section and not conflicting_owner and not nonpersonal)
    return result(True, 'full_name_heading_and_academic_content')


def paper_overlap(text, paper_titles):
    """Exact normalized title corroboration, excluding short/generic titles."""
    body = ' ' + norm_name(text) + ' '
    generic = {
        'introduction', 'conclusion', 'working paper', 'working papers', 'research paper',
        'research papers', 'selected publications', 'work in progress', 'current research',
        'research in progress', 'journal of economic literature',
    }
    for raw in paper_titles or ():
        title = norm_name(raw)
        if len(title) < 35 or len(title.split()) < 6 or title in generic:
            continue
        if ' ' + title + ' ' in body:
            return True
    return False
