#!/usr/bin/env python3
"""Multi-platform search aggregator for InnoSpark Skill Demo.

Searches Grok, Tavily, Bilibili (TikHub), Zhihu (TikHub), and YouTube
concurrently and returns a single structured JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).parent.parent.resolve()
GENERAL_MAX_RESULTS = 8
TAVILY_SEARCH_DEPTH = 'basic'
TAVILY_MAX_RESULTS = 8


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

def load_env(path: Path) -> None:
    if not path.exists():
        return
    with path.open(encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            # strip optional surrounding quotes
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


def load_default_env_files() -> None:
    """Load API keys from the skill and common sibling project .env files."""
    bundle_root = SKILL_ROOT.parent.parent if SKILL_ROOT.parent.name == 'skills' else SKILL_ROOT.parent
    candidates = [
        SKILL_ROOT / '.env',
        SKILL_ROOT.parent / 'union-search-skill' / '.env',
        SKILL_ROOT.parent / 'academic-research-skills' / '.env',
        SKILL_ROOT.parent / 'gs-skills' / '.env',
        bundle_root / 'vendor' / 'ROMA_v2' / '.env',
        bundle_root / '.env',
    ]
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        load_env(resolved)


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only, no external deps)
# ---------------------------------------------------------------------------

_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'


def _http_request(url: str, data: bytes | None, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    merged = {'User-Agent': _UA, **headers}
    req = urllib.request.Request(url, data=data, headers=merged)
    if data is not None and 'Content-Type' not in merged:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')[:500]
        return {'error': f'HTTP {exc.code}: {body}'}
    except urllib.error.URLError as exc:
        return {'error': f'URLError: {exc.reason}'}
    except Exception as exc:
        return {'error': str(exc)}


def http_post(url: str, payload: dict, headers: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode('utf-8')
    h = {'Content-Type': 'application/json', **(headers or {})}
    return _http_request(url, data, h, timeout)


def http_get(url: str, headers: dict | None = None, timeout: int = 30) -> dict:
    return _http_request(url, None, headers or {}, timeout)


def _safe_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _clip(text: str, limit: int = 1600) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + '...'


def _first_list_value(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0] or '').strip()
    if isinstance(value, str):
        return value.strip()
    return ''


def _format_authors(authors: Any, *, key: str = 'name', limit: int = 4) -> str:
    if not isinstance(authors, list):
        return ''
    names: list[str] = []
    for item in authors:
        if isinstance(item, dict):
            name = str(item.get(key) or '').strip()
        else:
            name = str(item or '').strip()
        if name:
            names.append(name)
        if len(names) >= limit:
            break
    suffix = ' et al.' if len(authors) > limit else ''
    return ', '.join(names) + suffix


def _openalex_abstract(index: Any) -> str:
    if not isinstance(index, dict):
        return ''
    words: list[tuple[int, str]] = []
    for word, positions in index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            try:
                words.append((int(pos), str(word)))
            except (TypeError, ValueError):
                continue
    words.sort(key=lambda x: x[0])
    return ' '.join(word for _, word in words)


# ---------------------------------------------------------------------------
# Per-platform search functions
# ---------------------------------------------------------------------------

def search_grok(query: str) -> dict:
    """Call Grok (xAI) chat-completions API."""
    api_key = os.environ.get('GROK_API_KEY') or os.environ.get('XAI_API_KEY')
    api_url = (os.environ.get('GROK_API_URL') or 'https://api.x.ai/v1').rstrip('/')
    model = os.environ.get('GROK_MODEL') or 'grok-3-fast'

    if not api_key:
        return {'error': 'GROK_API_KEY not configured'}

    payload = {
        'model': model,
        'stream': False,
        'messages': [
            {
                'role': 'system',
                'content': (
                    '你是一位专业的全网搜索助手。'
                    '请对用户的查询给出准确、全面的分析和摘要，'
                    '重点提炼关键信息，并在答案末尾列出主要信息来源。'
                ),
            },
            {'role': 'user', 'content': query},
        ],
    }

    result = http_post(
        f'{api_url}/chat/completions',
        payload,
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=60,
    )

    if 'error' in result:
        return result

    content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
    return {'answer': content, 'model': model}


def search_tavily(query: str) -> dict:
    """Call Tavily Search API."""
    api_key = os.environ.get('TAVILY_API_KEY')
    if not api_key:
        return {'error': 'TAVILY_API_KEY not configured'}

    payload = {
        'query': query,
        'search_depth': TAVILY_SEARCH_DEPTH,
        'include_answer': True,
        'max_results': TAVILY_MAX_RESULTS,
    }

    return http_post(
        'https://api.tavily.com/search',
        payload,
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=30,
    )


def search_semantic_scholar(query: str) -> dict:
    """Search Semantic Scholar Academic Graph."""
    params = urllib.parse.urlencode({
        'query': query,
        'limit': min(100, _safe_int(GENERAL_MAX_RESULTS, 8)),
        'fields': 'title,authors,year,venue,abstract,url,externalIds,citationCount,publicationDate',
    })
    headers: dict[str, str] = {}
    api_key = os.environ.get('S2_API_KEY')
    if api_key:
        headers['x-api-key'] = api_key
    data = {}
    for attempt in range(3):
        data = http_get(
            f'https://api.semanticscholar.org/graph/v1/paper/search?{params}',
            headers=headers,
            timeout=30,
        )
        if 'HTTP 429' not in str(data.get('error', '')):
            break
        time.sleep(2 * (attempt + 1))
    if 'error' in data:
        return data

    results = []
    for paper in data.get('data', []) if isinstance(data.get('data'), list) else []:
        if not isinstance(paper, dict):
            continue
        title = str(paper.get('title') or '').strip()
        if not title:
            continue
        authors = _format_authors(paper.get('authors'))
        external = paper.get('externalIds') if isinstance(paper.get('externalIds'), dict) else {}
        doi = str(external.get('DOI') or '').strip()
        url = str(paper.get('url') or '').strip()
        if doi and not url:
            url = f'https://doi.org/{doi}'
        year = paper.get('year') or ''
        venue = str(paper.get('venue') or '').strip()
        abstract = str(paper.get('abstract') or '').strip()
        details = ' | '.join(
            part for part in (
                f'authors: {authors}' if authors else '',
                f'year: {year}' if year else '',
                f'venue: {venue}' if venue else '',
                abstract,
            )
            if part
        )
        citation_count = _safe_int(paper.get('citationCount'), 0)
        results.append({
            'title': title,
            'url': url,
            'description': _clip(details),
            'score': min(0.95, 0.62 + min(citation_count, 500) / 2000),
        })
    return {'results': results}


def search_crossref(query: str) -> dict:
    """Search Crossref Works."""
    params = {
        'query': query,
        'rows': min(100, _safe_int(GENERAL_MAX_RESULTS, 8)),
        'select': 'DOI,title,author,published-print,published-online,published,container-title,URL,is-referenced-by-count,abstract',
    }
    url = f'https://api.crossref.org/works?{urllib.parse.urlencode(params)}'
    ua = 'ROMA-WebSearch/1.0'
    mailto = os.environ.get('CROSSREF_MAILTO')
    if mailto:
        ua = f'{ua} (mailto:{mailto})'
    data = http_get(url, headers={'User-Agent': ua}, timeout=30)
    if 'error' in data:
        return data

    items = data.get('message', {}).get('items', [])
    results = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        title = _first_list_value(item.get('title'))
        if not title:
            continue
        authors = _format_authors(item.get('author'), key='family')
        year = ''
        for key in ('published-print', 'published-online', 'published'):
            parts = item.get(key, {}).get('date-parts') if isinstance(item.get(key), dict) else None
            if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                year = str(parts[0][0])
                break
        venue = _first_list_value(item.get('container-title'))
        doi = str(item.get('DOI') or '').strip()
        item_url = str(item.get('URL') or '').strip()
        url_out = item_url or (f'https://doi.org/{doi}' if doi else '')
        abstract = re.sub(r'<[^>]+>', ' ', str(item.get('abstract') or '')).strip()
        details = ' | '.join(
            part for part in (
                f'authors: {authors}' if authors else '',
                f'year: {year}' if year else '',
                f'venue: {venue}' if venue else '',
                abstract,
            )
            if part
        )
        refs = _safe_int(item.get('is-referenced-by-count'), 0)
        results.append({
            'title': title,
            'url': url_out,
            'description': _clip(details),
            'score': min(0.93, 0.6 + min(refs, 500) / 2200),
        })
    return {'results': results}


def search_openalex(query: str) -> dict:
    """Search OpenAlex Works."""
    params = {
        'search': query,
        'per-page': min(100, _safe_int(GENERAL_MAX_RESULTS, 8)),
        'select': 'id,doi,title,display_name,publication_year,primary_location,authorships,cited_by_count,abstract_inverted_index',
    }
    mailto = os.environ.get('OPENALEX_MAILTO')
    if mailto:
        params['mailto'] = mailto
    data = http_get(
        f'https://api.openalex.org/works?{urllib.parse.urlencode(params)}',
        timeout=30,
    )
    if 'error' in data:
        return data

    results = []
    for work in data.get('results', []) if isinstance(data.get('results'), list) else []:
        if not isinstance(work, dict):
            continue
        title = str(work.get('title') or work.get('display_name') or '').strip()
        if not title:
            continue
        authorships = work.get('authorships') if isinstance(work.get('authorships'), list) else []
        author_names = []
        for authorship in authorships[:4]:
            author = authorship.get('author') if isinstance(authorship, dict) else None
            if isinstance(author, dict) and author.get('display_name'):
                author_names.append(str(author.get('display_name')).strip())
        if len(authorships) > 4:
            author_names.append('et al.')
        primary_location = work.get('primary_location') if isinstance(work.get('primary_location'), dict) else {}
        source = primary_location.get('source') if isinstance(primary_location.get('source'), dict) else {}
        venue = str(source.get('display_name') or '').strip()
        landing = str(primary_location.get('landing_page_url') or '').strip()
        doi = str(work.get('doi') or '').strip()
        url_out = landing or doi or str(work.get('id') or '').strip()
        abstract = _openalex_abstract(work.get('abstract_inverted_index'))
        details = ' | '.join(
            part for part in (
                f'authors: {", ".join(author_names)}' if author_names else '',
                f'year: {work.get("publication_year")}' if work.get('publication_year') else '',
                f'venue: {venue}' if venue else '',
                abstract,
            )
            if part
        )
        cites = _safe_int(work.get('cited_by_count'), 0)
        results.append({
            'title': title,
            'url': url_out,
            'description': _clip(details),
            'score': min(0.94, 0.61 + min(cites, 500) / 2100),
        })
    return {'results': results}


def _serper_authors(value: Any, publication_info: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        names = []
        for item in value[:4]:
            if isinstance(item, dict):
                name = str(item.get('name') or item.get('title') or '').strip()
            else:
                name = str(item or '').strip()
            if name:
                names.append(name)
        if len(value) > 4:
            names.append('et al.')
        return ', '.join(names)
    if isinstance(publication_info, dict):
        authors = publication_info.get('authors')
        if isinstance(authors, list):
            return _serper_authors(authors, {})
    return ''


def _serper_cited_by(value: Any) -> tuple[str, str, str]:
    if isinstance(value, dict):
        text = str(
            value.get('total')
            or value.get('count')
            or value.get('citedBy')
            or value.get('cites')
            or ''
        ).strip()
        link = str(value.get('link') or value.get('url') or '').strip()
        cites_id = str(value.get('citesId') or value.get('dataCid') or '').strip()
        return text, link, cites_id
    if isinstance(value, (int, float)):
        return str(int(value)), '', ''
    if isinstance(value, str):
        match = re.search(r'[\d,]+', value)
        return (match.group(0).replace(',', '') if match else value.strip()), '', ''
    return '', '', ''


def _serper_year(item: dict[str, Any], publication_info: Any) -> str:
    year = str(item.get('year') or item.get('date') or '').strip()
    if re.fullmatch(r'(18|19|20)\d{2}', year):
        return year
    summary = ''
    if isinstance(publication_info, dict):
        summary = str(publication_info.get('summary') or '').strip()
    match = re.search(r'\b(18|19|20)\d{2}\b', summary)
    return match.group(0) if match else ''


def search_serper_scholar(query: str) -> dict:
    """Search Google Scholar through Serper's Scholar endpoint."""
    api_key = os.environ.get('SERPER_API_KEY')
    if not api_key:
        return {'error': 'SERPER_API_KEY not configured'}

    payload = {
        'q': query,
        'num': min(20, _safe_int(GENERAL_MAX_RESULTS, 8)),
    }
    data = http_post(
        'https://google.serper.dev/scholar',
        payload,
        headers={'X-API-KEY': api_key},
        timeout=30,
    )
    if 'error' in data:
        return data

    results = []
    organic = data.get('organic') if isinstance(data.get('organic'), list) else []
    for item in organic[: _safe_int(GENERAL_MAX_RESULTS, 8)]:
        if not isinstance(item, dict):
            continue
        title = str(item.get('title') or '').strip()
        if not title:
            continue
        publication_info = item.get('publicationInfo') if isinstance(item.get('publicationInfo'), dict) else {}
        authors = _serper_authors(item.get('authors'), publication_info)
        year = _serper_year(item, publication_info)
        snippet = str(item.get('snippet') or '').strip()
        venue = str(publication_info.get('summary') or '').strip()
        cited_text, cited_link, cites_id = _serper_cited_by(item.get('citedBy'))
        url = str(item.get('link') or item.get('pdfUrl') or '').strip()
        details = ' | '.join(
            part for part in (
                f'authors: {authors}' if authors else '',
                f'year: {year}' if year else '',
                f'venue: {venue}' if venue else '',
                snippet,
                f'cited_by: {cited_text}' if cited_text else '',
                f'cited_by_url: {cited_link}' if cited_link else '',
                f'data_cid: {cites_id}' if cites_id else '',
            )
            if part
        )
        cites = _safe_int(cited_text, 0)
        results.append({
            'title': title,
            'url': url,
            'description': _clip(details),
            'authors': authors,
            'year': year,
            'cited_by': cited_text,
            'cited_by_url': cited_link,
            'data_cid': cites_id,
            'score': min(0.95, 0.66 + min(cites, 500) / 2000),
        })

    return {'results': results}


def search_scholar(query: str) -> dict:
    """Search scholarly material, preferring Serper Scholar with web fallback."""
    serper_result = search_serper_scholar(query)
    if serper_result.get('results'):
        return serper_result

    scholar_query = (
        f'{query} '
        'site:scholar.google.com OR site:scholar.archive.org OR site:papers.ssrn.com'
    )
    result = search_duckduckgo(scholar_query)
    if 'error' in result:
        return serper_result if serper_result.get('error') else result
    fallback = {'results': result.get('results', [])}
    if serper_result.get('error'):
        fallback['serper_error'] = serper_result.get('error')
    return fallback


def search_bilibili(query: str) -> dict:
    """Search Bilibili via TikHub API."""
    token = os.environ.get('TIKHUB_TOKEN')
    if not token:
        return {'error': 'TIKHUB_TOKEN not configured'}

    encoded = urllib.parse.quote(query)
    url = (
        f'https://api.tikhub.io/api/v1/bilibili/web/fetch_general_search'
        f'?keyword={encoded}&order=totalrank&page=1&page_size=8'
    )

    result = http_get(url, headers={'Authorization': f'Bearer {token}'}, timeout=20)

    if 'error' in result:
        return result
    if result.get('code') != 200:
        return {'error': result.get('message', 'Bilibili API error')}

    raw_list = result.get('data', {}).get('data', {}).get('result', [])
    videos = []
    for v in raw_list:
        title = (v.get('title') or '').replace('<em class="keyword">', '').replace('</em>', '')
        aid = v.get('aid', '')
        bvid = v.get('bvid', '')
        if bvid:
            link = f'https://www.bilibili.com/video/{bvid}'
        elif aid:
            link = f'https://www.bilibili.com/video/av{aid}'
        else:
            link = v.get('arcurl', '')
        videos.append({
            'title': title,
            'url': link,
            'description': v.get('description', ''),
            'author': v.get('author', ''),
        })

    return {'results': videos}


_UNION_ROOT = Path(
    os.environ.get('WEB_SEARCH_UNION_ROOT') or SKILL_ROOT.parent / 'union-search-skill'
).expanduser()
_ZHIHU_SCRIPT = _UNION_ROOT / 'scripts' / 'zhihu' / 'zhihu_core.py'
_DDG_SCRIPT = _UNION_ROOT / 'scripts' / 'duckduckgo' / 'duckduckgo_search.py'


def _academic_research_root() -> Path:
    return Path(
        os.environ.get('ACADEMIC_RESEARCH_SKILLS_ROOT')
        or SKILL_ROOT.parent / 'academic-research-skills'
    ).expanduser()


def _normalize_academic_title(title: str) -> str:
    text = str(title or '').lower()
    text = re.sub(r'[\W_]+', ' ', text, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', text).strip()


def _academic_query_terms(query: str) -> list[str]:
    text = str(query or '')
    lower = text.lower()
    known_terms = [
        '土地财政', '土地出让', '国有土地使用权出让收入', '地方财政',
        '房产税', '房地产税', '普通商品房', '利润税', '改善型住房',
        '保障性住房', '保障性租赁住房', '住房券', '先租后售',
        '财政部', '国家统计局', '自然资源部', '贾康', '刘尚希',
        'portfolio diversification', 'number of stocks', 'equity portfolio',
        'evans archer', 'fielitz', 'solnik', 'statman', 'beck',
        'property tax', 'land value tax', 'split-rate tax', 'differential tax',
        'local public finance', 'land finance', 'housing voucher',
    ]
    generic = {
        'the', 'and', 'for', 'with', 'from', 'into', 'what', 'when', 'where',
        'how', 'many', 'are', 'was', 'were', '研究', '报告', '分析', '文献',
        '论文', '中国', '全国', '数据', '定义', '历史', '模式', '机制',
        '政策', '影响', '比较', '来源', '检索',
    }

    terms: list[str] = []
    for term in known_terms:
        if term in lower or term in text:
            terms.append(term)

    for token in re.findall(r'[A-Za-z][A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}', text):
        t = token.strip()
        if not t:
            continue
        if t.lower() in generic or t in generic:
            continue
        if len(t) > 16 and re.fullmatch(r'[\u4e00-\u9fff]+', t):
            continue
        if t not in terms and t.lower() not in terms:
            terms.append(t)
    return terms[:16]


def _academic_query_variants(query: str) -> list[str]:
    text = re.sub(r'\s+', ' ', str(query or '')).strip()
    variants: list[str] = []

    def add(value: str) -> None:
        value = re.sub(r'\s+', ' ', value).strip(' ;；,，')
        if value and value not in variants:
            variants.append(value)

    add(text)
    for part in re.split(r'[;；\n]+', text):
        add(part)

    terms = _academic_query_terms(text)
    if terms:
        add(' '.join(terms[:12]))

    cjk_terms = [t for t in terms if re.search(r'[\u4e00-\u9fff]', t)]
    en_terms = [t for t in terms if re.search(r'[A-Za-z]', t)]
    if cjk_terms:
        add(' '.join(cjk_terms[:10]))
    if en_terms:
        add(' '.join(en_terms[:12]))

    lower = text.lower()
    if 'portfolio' in lower or 'diversification' in lower or '分散' in text or '组合' in text:
        add('Evans Archer Fielitz Solnik Statman Beck portfolio diversification number of stocks')
        add('How many stocks are sufficient for equity portfolio diversification literature review')
    if any(term in text for term in ('土地财政', '土地出让', '地方财政')):
        add('中国 土地财政 土地出让收入 地方政府 财政 分税制 房地产')
        add('China land finance land conveyance fees local government fiscal decentralization')
    if any(term in text for term in ('房产税', '房地产税', '差别税率', '豪宅税')):
        add('房地产税 房产税 累进税率 差别税率 财产税 税负分布 再分配')
        add('property tax progressive rate split-rate land value tax housing equity efficiency')

    max_variants = _safe_int(os.environ.get('ACADEMIC_RESEARCH_QUERY_VARIANTS'), 4)
    return variants[: max(1, max_variants)]


def _academic_result_domain(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url if '://' in url else f'https://{url}')
    except Exception:
        return ''
    host = (parsed.netloc or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    return host


def _academic_relevance_score(item: dict[str, Any], query_terms: list[str], base: float) -> float:
    title = str(item.get('title') or '')
    url = str(item.get('url') or item.get('link') or '')
    summary = str(item.get('description') or item.get('summary') or item.get('snippet') or '')
    haystack = f'{title} {summary} {url}'.lower()
    hits = sum(1 for term in query_terms if str(term).lower() in haystack)
    domain = _academic_result_domain(url)
    authority_bonus = 0.0
    if any(
        domain == d or domain.endswith(f'.{d}')
        for d in (
            'doi.org', 'jstor.org', 'nber.org', 'ssrn.com', 'papers.ssrn.com',
            'sciencedirect.com', 'springer.com', 'tandfonline.com', 'wiley.com',
            'mdpi.com', 'openalex.org', 'semanticscholar.org', 'crossref.org',
        )
    ):
        authority_bonus = 0.04
    return min(0.97, max(0.0, base) + min(hits, 8) * 0.025 + authority_bonus)


def _academic_match_counts(item: dict[str, Any], query_terms: list[str]) -> tuple[int, int]:
    title = str(item.get('title') or '')
    url = str(item.get('url') or item.get('link') or '')
    summary = str(item.get('description') or item.get('summary') or item.get('snippet') or '')
    haystack = f'{title} {summary} {url}'.lower()
    strong = 0
    weak = 0
    for term in query_terms:
        term_s = str(term or '').strip()
        if not term_s:
            continue
        if term_s.lower() not in haystack:
            continue
        if ' ' in term_s or re.search(r'[\u4e00-\u9fff]', term_s):
            strong += 1
        else:
            weak += 1
    return strong, weak


def _academic_should_include(item: dict[str, Any], query_terms: list[str]) -> bool:
    if not query_terms:
        return True
    strong, weak = _academic_match_counts(item, query_terms)
    if strong >= 1:
        return True
    return weak >= min(3, max(2, len(query_terms) // 4))


def search_academic_research(query: str) -> dict:
    """ARS-inspired academic discovery: query expansion, source screening, dedupe."""
    ars_root = _academic_research_root()
    variants = _academic_query_variants(query)
    query_terms = _academic_query_terms(query)
    protocol = 'academic-research-skills:bibliography_agent+semantic_scholar_api_protocol'

    source_funcs: list[tuple[str, Any]] = [
        ('scholar', search_scholar),
        ('openalex', search_openalex),
        ('crossref', search_crossref),
    ]
    # Semantic Scholar free-tier rate limits are tight. Use it inside the ARS
    # adapter only when a key is present; the standalone provider still exists.
    if os.environ.get('S2_API_KEY'):
        source_funcs.append(('semantic_scholar', search_semantic_scholar))

    by_key: dict[str, dict[str, Any]] = {}
    provider_health: dict[str, str] = {}

    for variant in variants:
        for provider, func in source_funcs:
            payload = func(variant)
            if not isinstance(payload, dict):
                provider_health[provider] = 'invalid'
                continue
            if payload.get('error'):
                provider_health[provider] = str(payload.get('error'))[:160]
                continue
            provider_health[provider] = 'ok'
            for item in payload.get('results', []) if isinstance(payload.get('results'), list) else []:
                if not isinstance(item, dict):
                    continue
                title = str(item.get('title') or '').strip()
                url = str(item.get('url') or item.get('link') or '').strip()
                if not title and not url:
                    continue
                summary = str(
                    item.get('description')
                    or item.get('summary')
                    or item.get('snippet')
                    or ''
                ).strip()
                title_key = _normalize_academic_title(title)
                key = title_key or url
                if not key:
                    continue
                base = 0.62
                try:
                    base = float(item.get('score', base))
                except (TypeError, ValueError):
                    base = 0.62
                if not _academic_should_include(item, query_terms):
                    continue
                score = _academic_relevance_score(item, query_terms, base)
                details = summary
                candidate = {
                    'title': title,
                    'url': url,
                    'description': _clip(details),
                    'score': score,
                    'source_provider': provider,
                    'ars_protocol': protocol,
                    'search_variant': variant,
                    'ars_root': str(ars_root) if ars_root.exists() else '',
                }
                current = by_key.get(key)
                if (
                    current is None
                    or score > float(current.get('score') or 0.0)
                    or (len(details) > len(str(current.get('description') or '')) and score >= float(current.get('score') or 0.0) - 0.02)
                ):
                    by_key[key] = candidate

    results = list(by_key.values())
    results.sort(key=lambda item: float(item.get('score') or 0.0), reverse=True)
    return {
        'results': results[: max(1, _safe_int(GENERAL_MAX_RESULTS, 8))],
        'protocol': protocol,
        'query_variants': variants,
        'provider_health': provider_health,
    }


def search_zhihu(query: str) -> dict:
    """Search Zhihu by calling union-search-skill's zhihu_core.py as a subprocess."""
    if not _ZHIHU_SCRIPT.exists():
        return {'error': f'Zhihu script not found: {_ZHIHU_SCRIPT}'}

    env = os.environ.copy()
    # Ensure .env vars are loaded for the child process
    env_file = SKILL_ROOT / '.env'
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            k = k.strip(); v = v.strip()
            if k and k not in env:
                env[k] = v

    try:
        proc = subprocess.run(
            [sys.executable, str(_ZHIHU_SCRIPT), query, '-n', str(min(20, GENERAL_MAX_RESULTS))],
            cwd=str(_ZHIHU_SCRIPT.parent.parent.parent),
            capture_output=True, text=True, timeout=25, env=env, check=False,
        )
    except subprocess.TimeoutExpired:
        return {'error': 'timeout calling zhihu_core.py'}
    except Exception as exc:
        return {'error': str(exc)}

    # Parse the text output: "[N] type: title\n    作者: ...\n    赞同: ...\n"
    items = []
    current: dict | None = None
    for line in proc.stdout.splitlines():
        line_s = line.strip()
        m = re.match(r'^\[(\d+)\]\s+(\S+):\s*(.*)', line_s)
        if m:
            if current and current.get('title'):
                items.append(current)
            item_type = m.group(2)
            title = m.group(3).replace('<em>', '').replace('</em>', '').strip()
            current = {'type': item_type, 'title': title, 'url': '', 'description': '', 'author': ''}
        elif current:
            if line_s.startswith('作者:'):
                current['author'] = line_s[3:].strip()
            elif line_s.startswith('URL:') or line_s.startswith('链接:'):
                current['url'] = line_s.split(':', 1)[1].strip()
    if current and current.get('title'):
        items.append(current)

    # Filter out ad/education items with no title
    real = [i for i in items if i['title'] and i['type'] not in ('education', 'knowledge_ad')]
    if not real:
        real = [i for i in items if i['title']]

    # For items without a direct URL, fall back to a Zhihu search URL
    zhihu_search_base = f'https://www.zhihu.com/search?type=content&q={urllib.parse.quote(query)}'
    out = []
    for i in real:
        url = i['url'] or zhihu_search_base
        out.append({'title': i['title'], 'url': url, 'description': i['description'], 'author': i['author']})

    return {'results': out}


def search_duckduckgo(query: str) -> dict:
    """Search DuckDuckGo via union-search-skill's duckduckgo_search.py (no API key needed)."""
    if not _DDG_SCRIPT.exists():
        return {'error': f'DuckDuckGo script not found: {_DDG_SCRIPT}'}

    try:
        proc = subprocess.run(
            [sys.executable, str(_DDG_SCRIPT), query, '-m', str(min(50, GENERAL_MAX_RESULTS)), '--json'],
            cwd=str(_DDG_SCRIPT.parent.parent.parent),
            capture_output=True, text=True, timeout=25, check=False,
        )
    except subprocess.TimeoutExpired:
        return {'error': 'timeout'}
    except Exception as exc:
        return {'error': str(exc)}

    if not proc.stdout.strip():
        return {'error': proc.stderr[:300] or 'no output'}

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {'error': 'invalid JSON output from DuckDuckGo script'}

    raw = data.get('results', [])
    results = []
    seen = set()
    for r in raw:
        href = (r.get('href') or '').strip()
        title = (r.get('title') or '').strip()
        if not href or href in seen:
            continue
        seen.add(href)
        results.append({
            'title': title,
            'url': href,
            'description': (r.get('body') or '')[:200],
        })

    return {'results': results}


def search_youtube(query: str) -> dict:
    """Search YouTube via YouTube Data API v3."""
    api_key = os.environ.get('YOUTUBE_API_KEY')
    if not api_key:
        return {'error': 'YOUTUBE_API_KEY not configured'}

    params = urllib.parse.urlencode({
        'part': 'snippet',
        'q': query,
        'type': 'video',
        'maxResults': min(50, GENERAL_MAX_RESULTS),
        'relevanceLanguage': 'zh',
        'key': api_key,
    })

    result = http_get(
        f'https://www.googleapis.com/youtube/v3/search?{params}',
        timeout=20,
    )

    if 'error' in result:
        return result

    items = result.get('items', [])
    videos = []
    for item in items:
        snippet = item.get('snippet', {})
        vid_id = item.get('id', {}).get('videoId', '')
        videos.append({
            'title': snippet.get('title', ''),
            'url': f'https://www.youtube.com/watch?v={vid_id}' if vid_id else '',
            'description': snippet.get('description', ''),
            'channel': snippet.get('channelTitle', ''),
        })

    return {'results': [v for v in videos if v['url']]}


# ---------------------------------------------------------------------------
# Platform registry
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, Any] = {
    'grok': search_grok,
    'tavily': search_tavily,
    'academic_research': search_academic_research,
    'semantic_scholar': search_semantic_scholar,
    'crossref': search_crossref,
    'openalex': search_openalex,
    'scholar': search_scholar,
    'bilibili': search_bilibili,
    'zhihu': search_zhihu,
    'youtube': search_youtube,
    'duckduckgo': search_duckduckgo,
}

DEFAULT_SOURCES = 'tavily,bilibili,zhihu,youtube,duckduckgo'


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    global GENERAL_MAX_RESULTS, TAVILY_SEARCH_DEPTH, TAVILY_MAX_RESULTS

    parser = argparse.ArgumentParser(description='Multi-platform search aggregator')
    parser.add_argument('--query', required=True, help='Search query')
    parser.add_argument('--sources', default=DEFAULT_SOURCES, help='Comma-separated list of sources')
    parser.add_argument('--max-results', type=int, default=int(os.environ.get('WEB_SEARCH_MAX_RESULTS', '8')), help='Per-source result limit')
    parser.add_argument('--tavily-depth', choices=['basic', 'advanced'], default=os.environ.get('TAVILY_SEARCH_DEPTH', 'basic'), help='Tavily search depth')
    parser.add_argument('--tavily-max-results', type=int, default=None, help='Tavily max results')
    args = parser.parse_args()

    load_default_env_files()
    GENERAL_MAX_RESULTS = _safe_int(args.max_results, 8)
    TAVILY_SEARCH_DEPTH = args.tavily_depth
    TAVILY_MAX_RESULTS = _safe_int(args.tavily_max_results, GENERAL_MAX_RESULTS) if args.tavily_max_results is not None else GENERAL_MAX_RESULTS

    sources = [s.strip() for s in args.sources.split(',') if s.strip() and s.strip() in PLATFORMS]
    if not sources:
        print(json.dumps({'error': f'No valid sources specified. Available: {", ".join(PLATFORMS)}'}))
        return 1

    results: dict[str, Any] = {}

    with ThreadPoolExecutor(max_workers=len(sources)) as executor:
        future_to_source = {
            executor.submit(PLATFORMS[s], args.query): s
            for s in sources
        }
        for future in as_completed(future_to_source):
            source = future_to_source[future]
            try:
                results[source] = future.result()
            except Exception as exc:
                results[source] = {'error': str(exc)}

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
