"""Restore only explicitly source-verified Unicode collision records.

Dry-run: --export /backups/unicode-export.json, then --bundle <printed path>.
Apply: --bundle <printed path> --apply --backup /backups/unicode-before.json.
Use --paper-ids 68907,68920 to limit an export. Backups must be on durable storage.
No researcher profile is merged, changed, or reactivated. Displaced works remain
in the backup/worklist and active source caches are queued for seed extraction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database.papers import compute_title_hash, normalize_title
from scripts.repair_verified_papers import write_backup
from scripts.repair_unicode_identity import TABLES, _canonical, _read

# Every author ID below is checked against its exact source-backed name at apply.
# 2284 combines distinct Yan Chen profiles; use the existing SDU-native identity.
SDU_SOURCE = 'https://faculty.sdu.edu.cn/chenyan5/zh_CN/index.htm'
SOURCE_OWNER_HOLDS = {2284}  # Known mixed Michigan/Shandong/BIT identity; never requeue it here.
VERIFIED = {
    68907: {
        'title': '新冠肺炎疫情对2020年山东省投资的影响及对策',
        'status': 'published', 'year': '2020', 'venue': '山东社会科学',
        'source_url': SDU_SOURCE, 'authors': [(39063, '言', '陈')],
        'evidence_urls': ['https://faculty.sdu.edu.cn/chenyan5/en/index.htm'],
        'bibliographic_note': 'Sole author 陈言; 2020 issue 4, pp.31–37.',
    },
    68920: {
        'title': '2015年（第十五届）中国制度经济学年会会议综述',
        'status': 'published', 'year': '2016', 'venue': '制度经济学研究',
        'source_url': SDU_SOURCE,
        'authors': [(39063, '言', '陈'), (39076, '欣泽', '李'), (39083, '阳荷', '刘')],
        'evidence_urls': ['https://www.cer.sdu.edu.cn/info/1055/9954.htm',
                          'https://www.cer.sdu.edu.cn/jsjj/cy.htm'],
        'bibliographic_note': '2016 issue 2, pp.198–208; publisher confirms ordered byline.',
    },
}
VERIFIED.update({279630: {'title': '「2 部門カレツキ・カルドアモデルと成長レジーム分析」',
          'status': 'published',
          'year': '2022',
          'venue': '制度と進化の政治経済学：調整の重層性と多様性',
          'source_url': 'https://sites.google.com/view/hiroshinishi/research',
          'authors': [(9162, 'Hiroshi', 'Nishi')],
          'evidence_urls': ['https://sites.google.com/view/hiroshinishi/research'],
          'bibliographic_note': 'Live author page Japanese publications item1 identifies canonical title '
                                'as2022 chapter in edited book, published by日本経済評論社. Editors磯谷明徳/植村博恭 are '
                                'not paper coauthors. Same live author page item14 separately identifies2006 '
                                'Nishi paper in経済論究124,pp67–83. Morita live papers page separately lists2022 '
                                'Jurist article in1578,pp50–55; home page identifies HatsuruMorita as site '
                                'owner. Current DOI10.20561/00031731 and W7144170833 belong to unrelated2014 '
                                'book chapter第2章 繊維・アパレル, sole author山形,辰史. Official repository '
                                'https://ir.ide.go.jp/records/31736 has exact citation meta tags. Saved HTML '
                                'unicode-279630-doi.html.',
          'displaced_works': [{'title': '「分配と成長のポスト・ケインジアン・アプローチ：2 部門モデルを用いた成長レジームと需要形成パターンの導出」',
                               'status': 'published',
                               'venue': '『経済論究』',
                               'year': '2006',
                               'source_url': 'https://sites.google.com/view/hiroshinishi/research',
                               'snapshot_ids': [159402],
                               'author_ids_in_order': [9162],
                               'note': '2006 journal article; preserve original snapshot fields/hash. Shared '
                                       'Nishi source with canonical.'},
                              {'title': '「マーケットモデルとイベントスタディ――虚偽記載事例を題材に 〔実務法曹のための分析手法の基礎知識 第2回 '
                                        '証券訴訟における損害算定〕」',
                               'status': 'published',
                               'venue': 'ジュリスト',
                               'year': '2022',
                               'source_url': 'https://sites.google.com/view/hatsuru/home/works/papers',
                               'snapshot_ids': [186962],
                               'author_ids_in_order': [15198],
                               'note': '2022 Jurist article; remove Morita attachment from canonical only '
                                       'after full before-image archive and reconstruction worklist.'}]},
 312775: {'title': '「清算義務は合理的か？(1)(2・完)」',
          'status': 'published',
          'year': '2005',
          'venue': 'NBL',
          'source_url': 'https://sites.google.com/view/hatsuru/home/works/papers',
          'authors': [(15198, 'Hatsuru', 'Morita')],
          'evidence_urls': ['https://sites.google.com/view/hatsuru/home/works/papers'],
          'bibliographic_note': 'Live author papers page lists canonical title separately as '
                                'NBL801,pp25–31(January2005) and NBL802,pp52–57(February2005). Separate '
                                'title in snapshot186951 is法学67(1),pp39–77(April2003), '
                                'and67(6),pp1140–1167(February2004).2003 is first installment year, not date '
                                'of both installments. Owner home page '
                                'https://sites.google.com/view/hatsuru/home explicitly identifies Hatsuru '
                                'Morita. Both entries have no coauthor label, unlike coauthored entries on '
                                'same page.',
          'displaced_works': [{'title': '「株主間契約をめぐる抵触法上の問題(1)(2・完)」',
                               'status': 'published',
                               'venue': '法学',
                               'year': '2003',
                               'source_url': 'https://sites.google.com/view/hatsuru/home/works/papers',
                               'snapshot_ids': [186951],
                               'author_ids_in_order': [15198],
                               'note': 'Two-part work spans2003–2004; preserve existing snapshot2003 as '
                                       'first-installment year; shared source/author with canonical.'}]},
 205830: {'title': '「家計の住宅投資、貯蓄、金融資産選択：2017年「日本家計パネル調査」の結果概要」',
          'status': 'working_paper',
          'year': '2018',
          'venue': 'HIT-REFINED Working Paper Series No. 78',
          'source_url': 'https://valley68.com/english/research',
          'authors': [(89777, '得夫', '祝迫'),
                      (89763, '威一郎', '植杉'),
                      (13571, 'Arito', 'Ono'),
                      (89793, '千弘', '清水'),
                      (89794, '道生', '直井'),
                      (89795, '雅博', '堀')],
          'evidence_urls': ['https://valley68.com/research/',
                            'https://www.ier.hit-u.ac.jp/hit-refined/Japanese/wp.078HIT-REFINED.pdf'],
          'bibliographic_note': 'Official PDF cover and page1 list exact six authors, March2018 and '
                                'workingpaper78. Original supplied URL redirects to live research page.',
          'displaced_works': [{'canonical_id': 205830,
                               'snapshot_id': 201649,
                               'title': '2017诺贝尔经济学奖得主理查德.塞勒对行为经济学的贡献',
                               'authors': ['Te Bao', '戴芸 (Yun Dai)'],
                               'year': '2017',
                               'status': 'published',
                               'venue': '经济与管理',
                               'issue': '6',
                               'pages': '1–4',
                               'primary_urls': ['https://baote.weebly.com/research.html'],
                               'verified_location': 'Live author page 中文论文 item8; coauthor戴芸.',
                               'safe_action': 'Split legitimate Chinese publication to its own Unicode title '
                                              'identity, preserve source snapshot, linkTeBao and '
                                              'verifiedYunDai identity; do not discard as junk.'}]},
 319920: {'title': '2011 年台灣人民法律紛爭面訪：設計及基本統計',
          'status': 'published',
          'year': '2016',
          'venue': '調查研究—方法與應用',
          'source_url': 'http://idv.sinica.edu.tw/kongpin/research.html',
          'authors': [(129696, '林', '常青'),
                      (16520, 'Kong-Pin', 'Chen'),
                      (129695, '黃', '國昌'),
                      (129700, '游', '雅婷')],
          'evidence_urls': ['https://www.rchss.sinica.edu.tw/srma/journals/3524'],
          'bibliographic_note': 'Publisher record supplies title, bilingual authors, date,issue,pages,DOI.',
          'displaced_works': [{'canonical_id': 319920,
                               'snapshot_id': 192716,
                               'title': '紛爭諮詢的尋求：2011年台灣人民紛爭解決行為面訪調查研究之發現',
                               'authors': ['林常青', '陳恭平', '黃國昌', '賴宏彬'],
                               'year': '2014',
                               'status': 'working_paper',
                               'venue': None,
                               'primary_urls': ['http://idv.sinica.edu.tw/kongpin/research.html',
                                                'https://idv.sinica.edu.tw/kongpin/2.pdf'],
                               'evidence_kind': 'stored primary source plus indexed author PDF title',
                               'cached_url_id': 27162,
                               'cached_at': '2026-08-31 17:13:39',
                               'verified_location': 'Stored author page explicitly lists four '
                                                    'names,(2014),exacttitle,mimeo. Live personaldomain '
                                                    'currently does not resolve locally; indexedPDF confirms '
                                                    'title and separate abstract.',
                               'safe_action': 'Split as distinct legitimate2014workingpaper; '
                                              'preserveunknownvenue; do not reuse2016journal of '
                                              'survey-designarticle.'},
                              {'canonical_id': 319920,
                               'snapshot_id': 206308,
                               'title': 'भारतातील जनगणनेचा पूर्व इतिहास व 2011 च्या जनगणनेतील वास्तव',
                               'authors_confirmed': ['Vijay Maruti Kumbhar'],
                               'year': '2011',
                               'status': 'published',
                               'venue': 'Yojana, Govt. of India',
                               'primary_urls': ['https://sites.google.com/view/vmkumbhar/research-papers'],
                               'verified_location': 'Live author-owned ResearchPapers page lists '
                                                    'exactMarathititle,2011,Yojana,Govt.ofIndia.',
                               'author_limit': 'Ownerlistswork; no coauthorbyline present, so no evidence '
                                               'for additionalauthors or '
                                               'independentconfirmationofsoleauthorship.',
                               'safe_action': 'Split legitimateMarathiarticle; preserveKumbhar authorship '
                                              'and avoid importingTaiwaneseauthors. No issue/pages/DOI '
                                              'inferable fromsource.'}],
          'doi': '10.7014/SRMA.2016100003'}})



def scope_ids(ids=None):
    ids = sorted(VERIFIED if ids is None else ids)
    if not ids or len(set(ids)) != len(ids) or not set(ids) <= set(VERIFIED):
        raise ValueError('Scope must contain unique source-verified paper IDs only')
    return ids


def plan_digest(ids):
    return hashlib.sha256(_canonical({pid: VERIFIED[pid] for pid in ids}).encode()).hexdigest()


def metadata(pid):
    plan = VERIFIED[pid]
    return {key: plan[key] for key in ('title', 'status', 'year', 'venue', 'source_url')} | {
        'title_hash': compute_title_hash(plan['title']), 'doi': plan.get('doi'), 'openalex_id': None,
        'abstract': None, 'draft_url': None, 'draft_url_status': 'unchecked',
        'draft_url_checked_at': None, 'enrichment_attempted_at': None,
    }


def read_scope(cursor, ids, *, lock=False):
    """Indexed parent/child reads only; source caches are bounded by this scope."""
    suffix = ' FOR UPDATE' if lock else ''
    papers, source_urls, author_ids = {}, set(), set()
    # Lock parent IDs in a stable order before all child rows.
    for pid in ids:
        rows = {table: _read(cursor, f'SELECT * FROM {table} WHERE {key} = %s ORDER BY id' + suffix, (pid,))
                for table, key in TABLES.items()}
        rows['feed_event_reviews'] = _read(cursor,
            'SELECT r.* FROM feed_event_reviews r JOIN feed_events e ON e.id = r.feed_event_id '
            'WHERE e.paper_id = %s ORDER BY r.id' + suffix, (pid,))
        papers[str(pid)] = rows
        author_ids.update(a['researcher_id'] for a in rows['authorship'])
        author_ids.update(a[0] for a in VERIFIED[pid]['authors'])
        source_urls.add(VERIFIED[pid]['source_url'])
        source_urls.update(r['url'] for r in rows['paper_urls'])
        source_urls.update(r['source_url'] for r in rows['paper_snapshots'] if r.get('source_url'))
    researchers = _read(cursor, 'SELECT * FROM researchers WHERE id IN (' +
                        ','.join(['%s'] * len(author_ids)) + ') ORDER BY id' + suffix, tuple(sorted(author_ids)))
    tracked = _read(cursor, 'SELECT * FROM researcher_urls WHERE url IN (' +
                    ','.join(['%s'] * len(source_urls)) + ') ORDER BY id' + suffix, tuple(sorted(source_urls)))
    by_id = {r['id']: r for r in tracked}
    cache = _read(cursor, 'SELECT * FROM html_content WHERE url_id IN (' +
                  ','.join(['%s'] * len(by_id)) + ') ORDER BY id' + suffix, tuple(by_id)) if by_id else []
    return {'papers': papers, 'researchers': researchers, 'tracked_sources': tracked, 'source_cache': cache}


def build_plan(rows, ids):
    researchers = {r['id']: r for r in rows['researchers']}
    result = {'papers': {}, 'queue_url_ids': [], 'inactive_source_url_ids': [],
              'held_source_url_ids': [], 'canonical_only_url_ids': []}
    reconstruction_sources = set()
    for pid in ids:
        spec, current = VERIFIED[pid], rows['papers'][str(pid)]
        if len(current['papers']) != 1 or normalize_title(current['papers'][0]['title']) != normalize_title(spec['title']):
            raise ValueError(f'Canonical identity drift for {pid}; re-review source evidence')
        if current['feed_events']:
            raise ValueError(f'Unexpected feed events for {pid}; reviewed scope had none')
        for rid, first, last in spec['authors']:
            person = researchers.get(rid, {})
            if (person.get('first_name'), person.get('last_name')) != (first, last):
                raise ValueError(f'Author identity drift for researcher {rid}')
        titles = {normalize_title(spec['title'])}
        sources = {spec['source_url'], *spec['evidence_urls']}
        keep = [r['id'] for r in current['paper_snapshots']
                if normalize_title(r.get('title')) in titles and r.get('source_url') in sources]
        displaced = [{'snapshot_id': r['id'], 'title': r.get('title'), 'source_url': r.get('source_url'),
                      'status': r.get('status'), 'year': r.get('year'), 'venue': r.get('venue')}
                     for r in current['paper_snapshots'] if r['id'] not in keep]
        reconstruction_sources.update(r['source_url'] for r in displaced if r.get('source_url'))
        result['papers'][str(pid)] = {
            'metadata': metadata(pid), 'author_ids_in_order': [a[0] for a in spec['authors']],
            'preserve_snapshot_ids': keep, 'displaced_work_worklist': displaced,
            'primary_evidence': spec['evidence_urls'], 'bibliographic_note': spec['bibliographic_note'],
            'verified_displaced_works': spec.get('displaced_works', []),
        }
    by_id = {r['id']: r for r in rows['tracked_sources']}
    for cache in rows['source_cache']:
        tracked = by_id[cache['url_id']]
        if tracked.get('researcher_id') in SOURCE_OWNER_HOLDS:
            result['held_source_url_ids'].append(cache['url_id'])
        elif not tracked['is_active']:
            result['inactive_source_url_ids'].append(cache['url_id'])
        elif tracked['url'] not in reconstruction_sources:
            # Avoid reintroducing duplicate native-name authors on canonicals
            # whose complete identity was just explicitly restored.
            result['canonical_only_url_ids'].append(cache['url_id'])
        elif cache.get('content_hash') and (cache.get('content') or cache.get('raw_html')):
            result['queue_url_ids'].append(cache['url_id'])
    return result


def already_restored(rows, plan, ids):
    for pid in ids:
        current, desired = rows['papers'][str(pid)], plan['papers'][str(pid)]
        if any(current['papers'][0].get(key) != value for key, value in desired['metadata'].items()
               if key != 'enrichment_attempted_at'):
            return False
        authors = sorted(current['authorship'], key=lambda r: r['author_order'])
        if [r['researcher_id'] for r in authors] != desired['author_ids_in_order']:
            return False
        if any(r['id'] not in desired['preserve_snapshot_ids'] for r in current['paper_snapshots']):
            return False
        expected_links = {(f"https://doi.org/{VERIFIED[pid]['doi']}", VERIFIED[pid]['doi'], 'doi')} if VERIFIED[pid].get('doi') else set()
        if {(r['url'], r.get('doi'), r['link_type']) for r in current['paper_links']} != expected_links:
            return False
        if any(current[t] for t in ('feed_events', 'openalex_coauthors', 'paper_topics')):
            return False
        if {r['url'] for r in current['paper_urls']} != {VERIFIED[pid]['source_url'], *VERIFIED[pid]['evidence_urls']}:
            return False
    return True


def restore(conn, evidence, *, apply=False, backup=None):
    ids = scope_ids(evidence['scope_paper_ids'])
    if evidence.get('plan_digest') != plan_digest(ids):
        raise ValueError('Verified plan changed since export; export and review again')
    if apply and not backup:
        raise ValueError('--apply requires a durable --backup path')
    conn.start_transaction()
    try:
        with conn.cursor(dictionary=True) as cursor:
            rows = read_scope(cursor, ids, lock=True)
            plan = build_plan(rows, ids)
            if already_restored(rows, plan, ids):
                conn.rollback()
                return {'applied': False, 'already_restored': True, 'plan': plan}
            if _canonical(rows) != _canonical(evidence['rows']):
                raise ValueError('Scoped before-images drifted; export and review again')
            for pid in ids:
                collision = _read(cursor, 'SELECT id FROM papers WHERE title_hash = %s AND id != %s FOR UPDATE',
                                  (metadata(pid)['title_hash'], pid))
                if collision:
                    raise ValueError(f'Correct Unicode hash for {pid} already belongs to another paper')
            if not apply:
                conn.rollback()
                return {'applied': False, 'plan': plan}
            archived = write_backup(backup, {'rows': rows, 'plan': plan, 'original_export': evidence})
            for pid in ids:
                desired = plan['papers'][str(pid)]
                for table, key in TABLES.items():
                    if table not in ('papers', 'paper_snapshots'):
                        cursor.execute(f'DELETE FROM {table} WHERE {key} = %s', (pid,))
                keep = desired['preserve_snapshot_ids']
                if keep:
                    cursor.execute('DELETE FROM paper_snapshots WHERE paper_id = %s AND id NOT IN (' +
                                   ','.join(['%s'] * len(keep)) + ')', (pid, *keep))
                else:
                    cursor.execute('DELETE FROM paper_snapshots WHERE paper_id = %s', (pid,))
                values = desired['metadata']
                cursor.execute('UPDATE papers SET ' + ', '.join(f'{key} = %s' for key in values) +
                               ' WHERE id = %s', (*values.values(), pid))
                for order, rid in enumerate(desired['author_ids_in_order'], 1):
                    cursor.execute('INSERT INTO authorship (publication_id, researcher_id, author_order) VALUES (%s, %s, %s)', (pid, rid, order))
                if VERIFIED[pid].get('doi'):
                    doi = VERIFIED[pid]['doi']
                    cursor.execute("INSERT INTO paper_links (paper_id, url, link_type, doi, discovered_at) VALUES (%s, %s, 'doi', %s, UTC_TIMESTAMP())", (pid, 'https://doi.org/' + doi, doi))
                for url in sorted({VERIFIED[pid]['source_url'], *VERIFIED[pid]['evidence_urls']}):
                    cursor.execute('INSERT INTO paper_urls (paper_id, url, discovered_at) VALUES (%s, %s, UTC_TIMESTAMP())', (pid, url))
            for cache in rows['source_cache']:
                if cache['url_id'] not in plan['queue_url_ids']:
                    continue
                cursor.execute('UPDATE html_content hc JOIN researcher_urls ru ON ru.id = hc.url_id '
                    'SET hc.extracted_hash = NULL, hc.extracted_at = NULL '
                    'WHERE hc.id = %s AND hc.content_hash <=> %s AND hc.extracted_hash <=> %s '
                    'AND hc.extracted_at <=> %s AND ru.is_active = TRUE',
                    (cache['id'], cache['content_hash'], cache.get('extracted_hash'), cache.get('extracted_at')))
                if cursor.rowcount != 1 and (cache.get('extracted_hash') is not None or cache.get('extracted_at') is not None):
                    raise ValueError('Source changed during seed queueing')
            conn.commit()
            return {'applied': True, 'backup': archived, 'plan': plan}
    except Exception:
        conn.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--export', type=Path)
    mode.add_argument('--bundle', type=Path)
    parser.add_argument('--paper-ids', help='Comma-separated source-verified IDs; export only')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.apply and (not args.backup or args.export):
        parser.error('--apply requires --bundle and --backup')
    if args.paper_ids and not args.export:
        parser.error('--paper-ids is only allowed with --export; bundle scope is immutable')
    from backend.database.connection import get_connection
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('SET SESSION MAX_EXECUTION_TIME=8000')
            cursor.execute('SET SESSION innodb_lock_wait_timeout=10')
        conn.commit()
        if args.export:
            ids = scope_ids([int(v) for v in args.paper_ids.split(',')] if args.paper_ids else None)
            conn.start_transaction(consistent_snapshot=True, readonly=True)
            with conn.cursor(dictionary=True) as cursor:
                rows = read_scope(cursor, ids)
            conn.rollback()
            evidence = {'scope_paper_ids': ids, 'plan_digest': plan_digest(ids), 'rows': rows}
            result = {'export': write_backup(args.export, evidence), 'plan': build_plan(rows, ids)}
        else:
            result = restore(conn, json.loads(args.bundle.read_text()), apply=args.apply, backup=args.backup)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == '__main__':
    main()
