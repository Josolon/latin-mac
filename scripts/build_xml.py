"""Generate src/LatinDictionary.xml (Apple Dictionary source) from the SQLite
databases built by scripts/build_dbs.py.

Each entry contains, in one always-visible scrollable page:
  1. The full Lewis & Short article (sense hierarchy + overview box)
  2. A Synonyms section from Ramshorn's Dictionary of Latin Synonymes (1841)
  3. A Morphology section (declension grid / verb principal parts) from the
     Perseus/Morpheus full-form analyses

Run from the repo root:  python3 scripts/build_xml.py
"""
import html
import json
import os
import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict

LS_DB_PATH = 'data/ls.db'
MORPH_DB_PATH = 'data/morph.db'
SYN_DB_PATH = 'data/synonyms.db'
OUTPUT_XML_PATH = 'src/LatinDictionary.xml'

ROMAN_NUM_RE = re.compile(r'^[IVX]+\.?$')
ROMAN_VALUES = {'I': 1, 'V': 5, 'X': 10}


def roman_to_int(s):
    s = s.rstrip('.').upper()
    total = 0
    prev = 0
    for ch in reversed(s):
        v = ROMAN_VALUES.get(ch, 0)
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def label_ordinal(n):
    """Best-effort ordinal for a TEI sense n-value, used only to detect when
    a lettered/numbered cycle restarts (A..E, then A again) - not to sort."""
    n = n.strip().rstrip('.')
    if not n:
        return None
    if ROMAN_NUM_RE.match(n):
        return roman_to_int(n)
    if re.match(r'^[a-z]$', n):
        return ord(n) - ord('a')
    if re.match(r'^[A-Z]$', n):
        return ord(n) - ord('A')
    if n.isdigit():
        return int(n)
    return None

# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def clean_text(text):
    if not text:
        return ''
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Cc')
    return re.sub(r'\s+', ' ', text.replace('\t', ' ')).strip()


def strip_length_marks(text):
    """ămo -> amo, mūrus -> murus: remove macrons/breves for search keys."""
    if not text:
        return ''
    decomposed = unicodedata.normalize('NFD', text)
    filtered = ''.join(ch for ch in decomposed if ord(ch) not in (0x0304, 0x0306))
    return unicodedata.normalize('NFC', filtered)


def search_variants(word):
    """All lookup spellings for a Latin word: plain, i-for-j, u-for-v, both."""
    w = strip_length_marks(word).replace('-', '')
    if not w:
        return set()
    variants = {w}
    variants.add(w.replace('j', 'i').replace('J', 'I'))
    variants.add(w.replace('v', 'u').replace('V', 'U'))
    variants.add(w.replace('j', 'i').replace('J', 'I').replace('v', 'u').replace('V', 'U'))
    return variants


def norm_join_key(word):
    """Same normalization as build_dbs.norm_join_key (join across sources)."""
    w = strip_length_marks(word).lower()
    return w.replace('j', 'i').replace('v', 'u').replace(' ', '').replace('-', '')


def sanitize_key(text):
    kw = unicodedata.normalize('NFC', (text or '').strip())
    while kw and not unicodedata.category(kw[0]).startswith(('L', 'N')):
        kw = kw[1:]
    return kw


# ---------------------------------------------------------------------------
# Lewis & Short entry rendering
# ---------------------------------------------------------------------------

def node_segments(node):
    """Flatten a node's mixed content into an ordered list of
    ('text', str) / ('elem', child) segments - tails become their own
    'text' segments, decoupled from the element they trail. This lets a
    segment list be split and re-rendered as two independent fragments."""
    segments = []
    if node.text:
        segments.append(('text', node.text))
    for child in node:
        segments.append(('elem', child))
        if child.tail:
            segments.append(('text', child.tail))
    return segments


def render_segments(segments, skip_senses=True):
    """Render a list of node_segments()-style segments to styled HTML."""
    frags = []
    for kind, val in segments:
        if kind == 'text':
            frags.append(html.escape(clean_text(val)))
            continue
        child = val
        tag = child.tag.split('}')[-1]
        if skip_senses and tag == 'sense':
            continue
        text = clean_text(''.join(child.itertext()))
        if tag in ('orth',):
            frags.append(f'<b class="la-word">{html.escape(text)}</b>')
        elif tag in ('itype',):
            frags.append(f'<span class="itype">{html.escape(text)}</span>')
        elif tag in ('pos', 'gen'):
            frags.append(f'<span class="pos">{html.escape(text)}</span>')
        elif tag in ('foreign',):
            frags.append(f'<span class="foreign">{html.escape(text)}</span>')
        elif tag in ('hi', 'title', 'author'):
            frags.append(f'<i>{html.escape(text)}</i>')
        elif tag == 'cit':
            # A cit typically wraps <quote> (the Latin example), an optional
            # <trans>/<tr> (its English gloss), and <bibl> (the reference) -
            # recurse so each renders distinctly instead of one flattened blob.
            frags.append(render_segments(node_segments(child), skip_senses=skip_senses))
        elif tag == 'quote':
            frags.append(f'<i class="cit-quote">{html.escape(text)}</i>')
        elif tag == 'trans':
            inner = render_segments(node_segments(child), skip_senses=skip_senses)
            frags.append(f'<span class="cit-trans">‘{inner}’</span>')
        elif tag == 'tr':
            frags.append(html.escape(text))
        elif tag == 'bibl':
            frags.append(f'<span class="cit-bibl">{html.escape(text)}</span>')
        elif tag == 'usg':
            if child.attrib.get('type') == 'dom':
                frags.append(f'<span class="usg-domain">{html.escape(text)}</span>')
            else:
                frags.append(f'<span class="usg-style">{html.escape(text)}</span>')
        elif tag in ('case', 'mood', 'number'):
            frags.append(f'<span class="gram-abbr">{html.escape(text)}</span>')
        elif tag == 'lbl':
            frags.append(f'<span class="gram-lbl">{html.escape(text)}</span>')
        elif tag == 'etym':
            frags.append(f'<span class="etym">[{html.escape(text)}]</span>')
        elif tag == 'xr':
            # Wraps a <lbl> ("v.", already styled) and a <ref> - almost
            # always a symbolic print-navigation marker (supra/infra/the
            # foll.) rather than a specific headword, so no hyperlink is
            # attempted; just recurse so the lbl+ref render together, styled.
            frags.append(render_segments(node_segments(child), skip_senses=skip_senses))
        elif tag == 'ref':
            frags.append(f'<span class="xr-ref">{html.escape(text)}</span>')
        elif tag in ('pb', 'cb'):
            pass
        else:
            frags.append(html.escape(text))
    out = ' '.join(f for f in frags if f)
    return re.sub(r'\s+([,;:.])', r'\1', re.sub(r'\s+', ' ', out)).strip()


def render_inline(node, skip_senses=True):
    """Flatten a TEI node's mixed content into styled HTML (no nested senses)."""
    return render_segments(node_segments(node), skip_senses=skip_senses)


HENCE_SPLIT_RE = re.compile(r'Hence,')


def split_at_hence(sense_el):
    """Detect L&S's run-in-derived-headword transition ('...—Hence, ămans,
    antis, P. a., ...') inside a sense's own content, and split the sense's
    segments there. Returns (before_segments, after_segments) if found
    (after_segments starts at 'Hence,'), else None.
    """
    segments = node_segments(sense_el)
    for i, (kind, val) in enumerate(segments):
        if kind != 'text':
            continue
        m = HENCE_SPLIT_RE.search(val)
        if not m:
            continue
        if i + 1 >= len(segments) or segments[i + 1][0] != 'elem':
            continue
        if segments[i + 1][1].tag.split('}')[-1] != 'orth':
            continue
        before_text = val[:m.start()]
        after_text = val[m.start():]
        before = segments[:i] + ([('text', before_text)] if before_text.strip() else [])
        after = [('text', after_text)] + segments[i + 1:]
        return before, after
    return None


def brief_text(node, max_chars=150):
    text = []
    if node.text:
        text.append(node.text)
    for child in node:
        if child.tag.split('}')[-1] == 'sense':
            break
        text.append(''.join(child.itertext()))
        if child.tail:
            text.append(child.tail)
    t = clean_text(''.join(text))
    t = re.sub(r'\s+\(cf\..*?\)', '', t)
    m = re.search(r'[.:;]', t)
    if m and m.start() > 5:
        t = t[:m.start()]
    if len(t) > max_chars:
        t = t[:max_chars].rstrip() + '…'
    return t.strip()


def render_entry_body(entry_el):
    """Render the L&S article: preamble (principal parts, etymology) + senses."""
    senses = [c for c in entry_el.iter() if c.tag.split('}')[-1] == 'sense']

    preamble = render_inline(entry_el, skip_senses=True)
    # The headword itself is repeated as the h1, so drop the leading bold word
    preamble = re.sub(r'^<b class="la-word">.*?</b>\s*,?\s*', '', preamble, count=1)

    # L&S TEI quirk: when the entry preamble spills over, the source wraps its
    # tail in a spurious first <sense> that duplicates the real first sense's
    # level and n (e.g. two level-1 n="I" senses in a row). Treat that first
    # sense as preamble continuation: render it unnumbered, keep it out of the
    # overview.
    demoted = set()
    if len(senses) >= 2:
        a, b = senses[0], senses[1]
        if (a.attrib.get('level'), clean_text(a.attrib.get('n', ''))) == \
           (b.attrib.get('level'), clean_text(b.attrib.get('n', ''))):
            demoted.add(id(a))

    overview = []
    for s in senses:
        level = s.attrib.get('level', '')
        n = clean_text(s.attrib.get('n', ''))
        if level == '1' and n and ROMAN_NUM_RE.match(n) and id(s) not in demoted:
            overview.append((n, brief_text(s)))

    parts = []
    if preamble:
        parts.append(f'<div class="entry-preamble">{preamble}</div>')
    if len(overview) > 1:
        items = ''.join(
            f'<span class="overview-item"><span class="sense-num">{html.escape(n)}</span> '
            f'{html.escape(b)}</span>' for n, b in overview)
        parts.append(f'<div class="sense-overview">{items}</div>')

    # Lewis & Short frequently mid-sense introduces a derived headword (e.g.
    # "—Hence, amans, antis, P. a., ...") whose own senses then reuse A, B,
    # C... at the same TEI level as the group they trail. There's no source
    # markup distinguishing the two A-Z cycles, so detect the restart myself:
    # when a label's ordinal drops back down (E, then A) at a depth already
    # in progress, a new lettered group has begun.
    last_ordinal_by_depth = {}
    records = []

    for s in senses:
        try:
            depth = max(0, int(s.attrib.get('level', '1')) - 1)
        except ValueError:
            depth = 0
        n = clean_text(s.attrib.get('n', ''))
        if id(s) in demoted:
            n = ''
        body = render_inline(s, skip_senses=True)
        if not (n or body):
            continue

        major = depth == 0 and bool(n) and bool(ROMAN_NUM_RE.match(n))

        # A new top-level Roman-numeral division (depth 0) always starts its
        # own fresh A/B/C sub-cycle underneath it - that's normal L&S
        # structure, not a "Hence"-style anomaly, but it must still clear any
        # leftover deeper-depth tracking from the *previous* numeral's
        # letters, or numeral II's own first "A" looks like a spurious
        # restart relative to numeral I's last letter.
        if depth == 0 and n:
            for d in list(last_ordinal_by_depth):
                if d > depth:
                    del last_ordinal_by_depth[d]

        restart = False
        if depth >= 1 and n:
            ordv = label_ordinal(n)
            if ordv is not None:
                prev = last_ordinal_by_depth.get(depth)
                if prev is not None and ordv <= prev:
                    restart = True
                last_ordinal_by_depth[depth] = ordv
                # A restarted group invalidates tracking for any deeper level
                for d in list(last_ordinal_by_depth):
                    if d > depth:
                        del last_ordinal_by_depth[d]

        records.append({'s': s, 'n': n, 'depth': depth, 'major': major,
                         'body': body, 'restart': restart})

    # Prefer splitting the *preceding* sense right at its "Hence," transition -
    # the precise point L&S actually introduces the derived word - over
    # marking the following restarted sense. Falls back to marking the
    # follower when no such transition is found (e.g. a restart not caused
    # by a "Hence, <newword>" pattern).
    for i, rec in enumerate(records):
        depth, n, major, body = rec['depth'], rec['n'], rec['major'], rec['body']
        major_class = ' sense-major' if major else ''
        num_html = f'<span class="sense-num">{html.escape(n)}</span> ' if n else ''

        next_restarts = (i + 1 < len(records)) and records[i + 1]['restart']
        split = split_at_hence(rec['s']) if next_restarts else None

        if split is not None:
            before_segs, after_segs = split
            before_body = render_segments(before_segs, skip_senses=True)
            after_body = render_segments(after_segs, skip_senses=True)
            parts.append(
                f'<div class="sense sense-depth-{min(depth, 4)}{major_class}">'
                f'{num_html}<span class="sense-body">{before_body}</span></div>')
            parts.append(
                f'<div class="sense sense-depth-{min(depth, 4)} sense-group-restart">'
                f'<span class="sense-body">{after_body}</span></div>')
            records[i + 1]['restart'] = False  # handled here, not on the follower
        else:
            restart_class = ' sense-group-restart' if rec['restart'] else ''
            parts.append(
                f'<div class="sense sense-depth-{min(depth, 4)}{major_class}{restart_class}">'
                f'{num_html}<span class="sense-body">{body}</span></div>')

    return ''.join(parts)


# ---------------------------------------------------------------------------
# Synonyms section
# ---------------------------------------------------------------------------

def styled_synonym_body(body, max_chars=2500):
    if len(body) > max_chars:
        cut = body.rfind('.', 0, max_chars)
        body = body[:cut + 1] if cut > 200 else body[:max_chars] + '…'
    return html.escape(body)


def render_synonyms(articles, spinelli_syns=None, doederlein_articles=None):
    parts = ['<div class="syn-section">',
             '<p class="section-label">Synonyms &amp; Near-Synonyms</p>']
    if spinelli_syns:
        items = ', '.join(html.escape(s) for s in spinelli_syns)
        parts.append('<div class="syn-article">')
        parts.append(f'<p class="syn-body syn-spinelli">{items} '
                     f'<span class="syn-ref">(Spinelli–Fenzi 2019)</span></p>')
        parts.append('</div>')
    for num, headwords, body in (doederlein_articles or []):
        words = ', '.join(headwords.split(','))
        parts.append('<div class="syn-article">')
        # No section number in the citation: unlike Ramshorn/A&G, Doederlein's
        # original has no printed article numbers - "num" here is just our
        # own parse-order index, which isn't stable across re-downloads of
        # the source (Gutenberg silently revises its transcriptions), so
        # presenting it as if it were a citable locator would be misleading.
        parts.append(f'<p class="syn-headwords">{html.escape(words)} '
                     f'<span class="syn-ref">(Döderlein)</span></p>')
        parts.append(f'<p class="syn-body">{styled_synonym_body(body)}</p>')
        parts.append('</div>')
    for num, headwords, body in articles:
        words = ', '.join(headwords.split(','))
        parts.append('<div class="syn-article">')
        parts.append(f'<p class="syn-headwords">{html.escape(words)} '
                     f'<span class="syn-ref">(Ramshorn §{num})</span></p>')
        parts.append(f'<p class="syn-body">{styled_synonym_body(body)}</p>')
        parts.append('</div>')
    parts.append('</div>')
    return ''.join(parts)


# ---------------------------------------------------------------------------
# Morphology section
# ---------------------------------------------------------------------------

CASES = ['nom', 'gen', 'dat', 'acc', 'abl', 'voc', 'loc']
CASE_LABELS = {'nom': 'Nominative', 'gen': 'Genitive', 'dat': 'Dative',
               'acc': 'Accusative', 'abl': 'Ablative', 'voc': 'Vocative',
               'loc': 'Locative'}
GENDER_ORDER = ['masc', 'fem', 'neut']
GENDER_LABELS = {'masc': 'Masculine', 'fem': 'Feminine', 'neut': 'Neuter'}
DEGREE_LABELS = {'comp': 'Comparative', 'superl': 'Superlative'}
TENSES = ['pres', 'imperf', 'fut', 'perf', 'plup', 'futperf']
TENSE_LABELS = {'pres': 'Present', 'imperf': 'Imperfect', 'fut': 'Future',
                'perf': 'Perfect', 'plup': 'Pluperfect', 'futperf': 'Future Perfect'}
# Longest first: -que, -ne (incl. elided -n), -ve/-ue, and -st (est contraction)
ENCLITICS = ('que', 'ne', 've', 'ue', 'st', 'n')

ANALYSIS_GROUP_RE = re.compile(r'\(([^()]*)\)')


def drop_enclitic_variants(form_analyses):
    """Remove amoque/amon/amarest-style duplicates when the base form is present."""
    forms = set(form_analyses)
    out = {}
    for form, analyses in form_analyses.items():
        base_hit = False
        for enc in ENCLITICS:
            if form.lower().endswith(enc) and form[:-len(enc)] in forms:
                base_hit = True
                break
        if not base_hit:
            out[form] = analyses
    return out


def join_forms(forms):
    """Join forms for a table cell, collapsing u/v spelling duplicates
    (amaui/amavi) into the v-spelling L&S prints."""
    by_norm = {}
    for f in sorted(forms):
        norm = f.replace('v', 'u').replace('V', 'U')
        cur = by_norm.get(norm)
        if cur is None or ('v' in f and 'v' not in cur):
            by_norm[norm] = f
    return ', '.join(sorted(by_norm.values()))


# Morpheus reliably tags regularly-formed comparatives/superlatives (altior,
# altissimus) with comp/superl - but a handful of common suppletive
# superlatives are listed under the positive lemma's forms with no degree
# tag at all (optimus under bonus, maximus under magnus, etc.), because
# they're orthographically unrelated to the positive stem. Recognized by
# stem so they still land in the superlative grid/entry instead of getting
# jumbled into the positive's own declension table.
SUPPLETIVE_SUPERL_STEMS = {
    'bonus': 'optim', 'malus': 'pessim', 'magnus': 'maxim',
    'parvus': 'minim', 'multus': 'plurim',
}
# Same five adjectives also have rare/late regularized forms attested
# alongside the classical suppletive ones (e.g. "bonior"/"bonissimus"
# beside "melior"/"optimus"); prefer the classical form as the citation
# headword when both are attested for the same paradigm slot.
SUPPLETIVE_COMP_STEMS = {
    'bonus': 'melior', 'malus': 'pei', 'magnus': 'mai',
    'parvus': 'min', 'multus': 'plus',
}


def _is_suppletive_superl(lemma_hint, form):
    stem = SUPPLETIVE_SUPERL_STEMS.get(lemma_hint)
    return bool(stem) and form.lower().startswith(stem)


def raw_degree_forms(rows, lemma_hint=None):
    """All literal spellings (base and enclitic variants alike) tagged
    comp/superl anywhere in their raw analyses - used to keep degree forms
    out of the positive entry's search index even when drop_enclitic_variants
    has already collapsed the enclitic spelling out of classify_and_grid's
    own working set."""
    forms = set()
    for form, analyses in rows:
        if _is_suppletive_superl(lemma_hint, form):
            forms.add(form)
            continue
        for g in ANALYSIS_GROUP_RE.findall(analyses or ''):
            toks = set(g.split())
            if 'comp' in toks or 'superl' in toks:
                forms.add(form)
                break
    return forms


def classify_and_grid(rows, lemma_hint=None):
    """From (form, analyses) rows build a per-degree declension grid and
    verb principal parts.

    Morpheus ties comparative/superlative adjective forms (melior, optimus)
    back to the positive-degree lemma (bonus), tagged with 'comp'/'superl'
    in the analysis. Grouping purely by case/number would jumble all three
    degrees - and all three genders - into the same table cell, so the
    grid is keyed [degree]['pos'|'comp'|'superl'][case][number][gender].
    """
    form_analyses = {}
    for form, analyses in rows:
        groups = ANALYSIS_GROUP_RE.findall(analyses or '')
        if groups:
            form_analyses.setdefault(form, set()).update(groups)
    form_analyses = drop_enclitic_variants(form_analyses)

    grid3d = {deg: defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
              for deg in ('pos', 'comp', 'superl')}
    comp_raw_forms = set()
    superl_raw_forms = set()
    verb_parts = defaultdict(set)     # (tense, voice) -> 1st sg ind forms
    infinitives = defaultdict(set)    # (tense, voice) -> forms
    participles = defaultdict(set)    # (tense, voice) -> masc nom sg forms
    subj_parts = defaultdict(set)     # (tense, voice) -> 1st sg subjunctive forms
    imperatives = defaultdict(set)    # (tense, person, number) -> forms (voice-agnostic)
    supines = set()
    gerundives = set()
    n_nominal = n_verbal = 0

    for form, groups in form_analyses.items():
        for g in groups:
            toks = g.split()
            tokset = set(toks)
            case = next((c for c in CASES if any(t.startswith(c) for t in toks
                                                 for c2 in [c] if '/' not in t) or c in tokset), None)
            # handle combined "nom/voc" tokens
            combined = [t for t in toks if '/' in t and any(p in CASES for p in t.split('/'))]
            number = 'sg' if 'sg' in tokset else ('pl' if 'pl' in tokset else None)
            tense = next((t for t in TENSES if t in tokset), None)
            voice = 'act' if 'act' in tokset else ('pass' if 'pass' in tokset else None)
            if 'superl' in tokset or _is_suppletive_superl(lemma_hint, form):
                degree = 'superl'
            elif 'comp' in tokset:
                degree = 'comp'
            else:
                degree = 'pos'

            genders = [ge for ge in GENDER_ORDER if ge in tokset]
            combined_gender = [t for t in toks if '/' in t and any(p in GENDER_ORDER for p in t.split('/'))]
            if combined_gender:
                seen = []
                for t in combined_gender:
                    for p in t.split('/'):
                        if p in GENDER_ORDER and p not in seen:
                            seen.append(p)
                genders = seen
            if not genders:
                genders = [None]

            if 'part' in tokset:
                p_tense = next((t for t in ('pres', 'perf', 'fut') if t in tokset), None)
                p_voice = 'pass' if 'pass' in tokset else 'act'
                is_masc = 'masc' in tokset or any('masc' in t.split('/') for t in toks if '/' in t)
                is_nom = 'nom' in tokset or any('nom' in t.split('/') for t in toks if '/' in t)
                if p_tense and is_masc and is_nom and number == 'sg':
                    participles[(p_tense, p_voice)].add(form)
                continue
            if 'supine' in tokset:
                supines.add(form)
                continue
            if 'gerundive' in tokset:
                is_masc = 'masc' in tokset or any('masc' in t.split('/') for t in toks if '/' in t)
                is_nom = 'nom' in tokset or any('nom' in t.split('/') for t in toks if '/' in t)
                if is_masc and is_nom and number == 'sg':
                    gerundives.add(form)
                continue

            if tense and 'inf' in tokset:
                infinitives[(tense, voice or 'act')].add(form)
                n_verbal += 1
            elif tense and 'ind' in tokset and '1st' in tokset and number == 'sg':
                verb_parts[(tense, voice or 'act')].add(form)
                n_verbal += 1
            elif tense and 'subj' in tokset and '1st' in tokset and number == 'sg':
                subj_parts[(tense, voice or 'act')].add(form)
                n_verbal += 1
            elif tense and 'imperat' in tokset:
                person = next((p for p in ('2nd', '3rd') if p in tokset), None)
                if person and number:
                    imperatives[(tense, person, number)].add(form)
                n_verbal += 1
            elif tense:
                n_verbal += 1

            if number and 'part' not in tokset and not tense:
                cases_here = []
                if combined:
                    for t in combined:
                        cases_here.extend(p for p in t.split('/') if p in CASES)
                elif case:
                    cases_here = [case]
                for c in cases_here:
                    for ge in genders:
                        grid3d[degree][c][number][ge].add(form)
                        n_nominal += 1
                if cases_here and degree == 'comp':
                    comp_raw_forms.add(form)
                elif cases_here and degree == 'superl':
                    superl_raw_forms.add(form)

    return (grid3d, comp_raw_forms, superl_raw_forms, verb_parts, infinitives,
            participles, subj_parts, imperatives, supines, gerundives, n_nominal, n_verbal)


def render_principal_parts(verb_parts, infinitives, participles, supines, is_deponent):
    """The classic 4-part (3-part for deponents) citation form Latin is
    taught with: amo, amare, amavi, amatus - or for deponents,
    hortor, hortari, hortatus sum. Built from whichever of these forms the
    Morpheus analyses actually attest; gracefully omits any that are
    missing rather than guessing.
    """
    if is_deponent:
        pres = join_forms(verb_parts.get(('pres', 'pass'), []))
        inf = join_forms(infinitives.get(('pres', 'pass'), []))
        perf_participle = join_forms(participles.get(('perf', 'pass'), []))
        perf = f'{perf_participle} sum' if perf_participle else ''
        forms = [pres, inf, perf]
    else:
        pres = join_forms(verb_parts.get(('pres', 'act'), []))
        inf = join_forms(infinitives.get(('pres', 'act'), []))
        perf = join_forms(verb_parts.get(('perf', 'act'), []))
        # 4th part: perfect passive participle: the standard citation form for
        # transitive verbs. Intransitive verbs (no PPP) cite the future active
        # participle instead (e.g. cursurus), the conventional substitute.
        fourth = join_forms(participles.get(('perf', 'pass'), [])) or \
            join_forms(participles.get(('fut', 'act'), []))
        forms = [pres, inf, perf, fourth]

    if sum(1 for f in forms if f) < 2:
        return ''  # too little attested to be a useful citation

    forms_html = ', '.join(f'<b class="la-word">{html.escape(f)}</b>' if f
                           else '<span class="pp-missing">—</span>' for f in forms)
    label = 'Principal Parts (deponent)' if is_deponent else 'Principal Parts'
    return (f'<div class="principal-parts">'
            f'<span class="pp-label">{html.escape(label)}</span> '
            f'<span class="pp-forms">{forms_html}</span></div>')


def render_subjunctive(subj_parts):
    if not subj_parts:
        return ''
    rows = []
    for tense in ('pres', 'imperf', 'perf', 'plup'):
        act = join_forms(subj_parts.get((tense, 'act'), []))
        pas = join_forms(subj_parts.get((tense, 'pass'), []))
        if act or pas:
            rows.append(f'<tr><td class="case-label">{TENSE_LABELS[tense]}</td>'
                        f'<td>{html.escape(act) or "—"}</td><td>{html.escape(pas) or "—"}</td></tr>')
    if not rows:
        return ''
    return ('<div class="morph-section">'
            '<p class="section-label">Morphology — Subjunctive (1st sg.)</p>'
            '<table class="morphology-table"><tr><th>Tense</th><th>Active</th><th>Passive</th></tr>'
            + ''.join(rows) + '</table></div>')


def render_imperative(imperatives):
    if not imperatives:
        return ''
    rows = []
    for tense, label in (('pres', 'Present'), ('fut', 'Future')):
        sg = join_forms(imperatives.get((tense, '2nd', 'sg'), set()))
        pl = join_forms(imperatives.get((tense, '2nd', 'pl'), set()))
        if sg or pl:
            rows.append(f'<tr><td class="case-label">{label}</td>'
                        f'<td>{html.escape(sg) or "—"}</td><td>{html.escape(pl) or "—"}</td></tr>')
    if not rows:
        return ''
    return ('<div class="morph-section">'
            '<p class="section-label">Morphology — Imperative</p>'
            '<table class="morphology-table"><tr><th>Tense</th><th>2nd Singular</th><th>2nd Plural</th></tr>'
            + ''.join(rows) + '</table></div>')


def genders_in(degree_grid):
    """Which genders (besides the ungendered slot) are actually populated
    in this degree's grid, in traditional citation order."""
    found = set()
    for numbers in degree_grid.values():
        for gendered in numbers.values():
            for ge, forms in gendered.items():
                if ge and forms:
                    found.add(ge)
    return [g for g in GENDER_ORDER if g in found]


def slice_gender(degree_grid, gender):
    """Collapse a [case][number][gender] grid to [case][number] for one
    gender - or, if gender is None, union across every gender key (the
    plain-noun / no-gender-attested case, keeping the old flat behavior)."""
    out = defaultdict(lambda: defaultdict(set))
    for c, numbers in degree_grid.items():
        for num, gendered in numbers.items():
            if gender is None:
                for forms in gendered.values():
                    out[c][num] |= forms
            else:
                out[c][num] |= gendered.get(gender, set())
    return out


def write_declension_table(grid2d):
    rows = []
    for c in CASES:
        if c not in grid2d:
            continue
        sg = join_forms(grid2d[c].get('sg', set())) or '—'
        pl = join_forms(grid2d[c].get('pl', set())) or '—'
        if sg == '—' and pl == '—':
            continue
        rows.append(f'<tr><td class="case-label">{CASE_LABELS[c]}</td>'
                    f'<td>{html.escape(sg)}</td><td>{html.escape(pl)}</td></tr>')
    if not rows:
        return ''
    return ('<table class="morphology-table">'
            '<tr><th>Case</th><th>Singular</th><th>Plural</th></tr>'
            + ''.join(rows) + '</table>')


def write_declension_section(degree_grid):
    """Render one declension table, split into one per gender only when
    more than one gender is actually attested for this degree - so plain
    nouns/single-gender adjectives still get the old flat table."""
    genders = genders_in(degree_grid)
    parts = []
    if len(genders) > 1:
        for ge in genders:
            tbl = write_declension_table(slice_gender(degree_grid, ge))
            if tbl:
                parts.append(f'<p class="gender-label">{GENDER_LABELS[ge]}</p>')
                parts.append(tbl)
    else:
        tbl = write_declension_table(slice_gender(degree_grid, None))
        if tbl:
            parts.append(tbl)
    return ''.join(parts)


def pick_canonical_form(degree_grid, prefer_stem=None):
    """Nominative singular, preferring masculine (the traditional
    dictionary-citation gender), falling back through ungendered -> feminine
    -> neuter -> any populated cell at all. When multiple spellings are
    attested for the same slot, prefer_stem picks out the classical one
    (e.g. "melior" over the rare regularized "bonior")."""
    def pick(forms):
        candidates = sorted(forms)
        if prefer_stem:
            preferred = [f for f in candidates if f.lower().startswith(prefer_stem)]
            if preferred:
                return sorted(preferred)[0]
        return candidates[0]

    nom_sg = degree_grid.get('nom', {}).get('sg', {})
    for ge in ('masc', None, 'fem', 'neut'):
        forms = nom_sg.get(ge)
        if forms:
            return pick(forms)
    for numbers in degree_grid.values():
        for gendered in numbers.values():
            for forms in gendered.values():
                if forms:
                    return pick(forms)
    return ''


def render_morphology(classified, is_deponent=False, lemma_hint=None):
    (grid3d, comp_raw_forms, superl_raw_forms, verb_parts, infinitives, participles,
     subj_parts, imperatives, supines, gerundives, n_nominal, n_verbal) = classified
    noun_grid = grid3d['pos']
    comp_stem = SUPPLETIVE_COMP_STEMS.get(lemma_hint)
    superl_stem = SUPPLETIVE_SUPERL_STEMS.get(lemma_hint)
    parts = []

    if n_verbal > n_nominal and verb_parts:
        pp_html = render_principal_parts(verb_parts, infinitives, participles,
                                         supines, is_deponent)
        parts.append('<div class="morph-section">')
        if pp_html:
            parts.append(pp_html)
        parts.append('<p class="section-label">Morphology — Indicative (1st sg.) &amp; Infinitives</p>')
        parts.append('<table class="morphology-table">')
        parts.append('<tr><th>Tense</th><th>Active</th><th>Passive</th></tr>')
        for tense in TENSES:
            act = join_forms(verb_parts.get((tense, 'act'), [])) or '—'
            pas = join_forms(verb_parts.get((tense, 'pass'), [])) or '—'
            if act == '—' and pas == '—':
                continue
            parts.append(f'<tr><td class="case-label">{TENSE_LABELS[tense]}</td>'
                         f'<td>{html.escape(act)}</td><td>{html.escape(pas)}</td></tr>')
        inf_rows = []
        for tense in TENSES:
            act = join_forms(infinitives.get((tense, 'act'), []))
            pas = join_forms(infinitives.get((tense, 'pass'), []))
            if act or pas:
                inf_rows.append(
                    f'<tr><td class="case-label">{TENSE_LABELS[tense]} Infinitive</td>'
                    f'<td>{html.escape(act) or "—"}</td><td>{html.escape(pas) or "—"}</td></tr>')
        if inf_rows:
            parts.append('<tr class="morph-secondary-header"><td colspan="3">Infinitives</td></tr>')
            parts.extend(inf_rows)
        if gerundives:
            parts.append('<tr class="morph-secondary-header"><td colspan="3">Gerundive</td></tr>')
            parts.append(f'<tr><td class="case-label">masc. nom. sg.</td>'
                        f'<td colspan="2">{html.escape(join_forms(gerundives))}</td></tr>')
        parts.append('</table></div>')
        parts.append(render_subjunctive(subj_parts))
        parts.append(render_imperative(imperatives))
    elif noun_grid:
        parts.append('<div class="morph-section">')
        has_degrees = bool(grid3d['comp']) or bool(grid3d['superl'])
        if has_degrees:
            pos_form = pick_canonical_form(noun_grid)
            comp_form = pick_canonical_form(grid3d['comp'], prefer_stem=comp_stem)
            superl_form = pick_canonical_form(grid3d['superl'], prefer_stem=superl_stem)
            bits = []
            if pos_form:
                bits.append(f'Positive: <b class="la-word">{html.escape(pos_form)}</b>')
            if comp_form:
                bits.append(f'Comparative: <b class="la-word">{html.escape(comp_form)}</b>')
            if superl_form:
                bits.append(f'Superlative: <b class="la-word">{html.escape(superl_form)}</b>')
            if bits:
                parts.append(f'<p class="entry-degree-forms">{" · ".join(bits)}</p>')
        parts.append('<p class="section-label">Morphology — Declension</p>')
        parts.append(write_declension_section(noun_grid))
        parts.append('</div>')

    return ''.join(parts)


def write_degree_entries(out, deg_counter, lemma_display, classified, lemma_hint=None):
    """Comparative/superlative forms get their own synthetic dictionary
    entries (own headword, own gender-split declension table, own search
    index) rather than being crammed into the positive entry's table -
    see Josolon/recap.md for the design rationale (ported from the
    ancient-greek-mac sister project). Returns the updated counter."""
    grid3d, comp_raw_forms, superl_raw_forms = classified[0], classified[1], classified[2]
    raw_by_degree = {'comp': comp_raw_forms, 'superl': superl_raw_forms}
    stem_by_degree = {'comp': SUPPLETIVE_COMP_STEMS.get(lemma_hint),
                       'superl': SUPPLETIVE_SUPERL_STEMS.get(lemma_hint)}

    for degree in ('comp', 'superl'):
        degree_grid = grid3d[degree]
        raw_forms = raw_by_degree[degree]
        if not degree_grid or not raw_forms:
            continue
        canonical = pick_canonical_form(degree_grid, prefer_stem=stem_by_degree[degree])
        if not canonical:
            continue
        deg_counter += 1
        title = sanitize_key(strip_length_marks(canonical).replace('-', '')) or canonical

        indices = set()
        indices |= search_variants(canonical)
        for form in raw_forms:
            indices |= search_variants(form)

        out.write(f'    <d:entry id="deg_{deg_counter}" d:title="{html.escape(title)}">\n')
        for kw in sorted(indices):
            kw = sanitize_key(kw)
            if kw:
                out.write(f'        <d:index d:value="{html.escape(kw)}"/>\n')
        out.write(f'        <h1 class="entry-lemma">{html.escape(canonical)}</h1>\n')
        out.write(f'        <p class="ag-level-label">{DEGREE_LABELS[degree]} degree of '
                  f'<b class="la-word">{html.escape(lemma_display)}</b>.</p>\n')
        out.write('        <div class="morph-section">\n')
        out.write(f'        <p class="section-label">Morphology — Declension ({DEGREE_LABELS[degree]})</p>\n')
        out.write(write_declension_section(degree_grid) + '\n')
        out.write('        </div>\n')
        out.write('    </d:entry>\n\n')

    return deg_counter


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build():
    ls = sqlite3.connect(LS_DB_PATH)
    morph = sqlite3.connect(MORPH_DB_PATH)
    syn = sqlite3.connect(SYN_DB_PATH)
    lcur, mcur, scur = ls.cursor(), morph.cursor(), syn.cursor()

    lcur.execute('SELECT COUNT(*) FROM entries')
    total = lcur.fetchone()[0]
    print(f'Building {total} entries -> {OUTPUT_XML_PATH}')

    # Ramshorn's index cites verbs by infinitive ("amare 66") but L&S keys
    # verbs by 1st sg ("amo"), so resolve each index word to lemmata: directly,
    # and via the morphology (form whose analysis is a present infinitive).
    syn_by_lemma = defaultdict(set)
    scur.execute('SELECT word, num FROM index_words')
    for word, num in scur.fetchall():
        syn_by_lemma[word].add(num)
        mcur.execute("SELECT DISTINCT lemma FROM forms WHERE form = ? "
                     "AND analyses LIKE '%pres inf%'", (word,))
        for (lemma,) in mcur.fetchall():
            syn_by_lemma[lemma.lower()].add(num)
    print(f'  synonym index resolved for {len(syn_by_lemma)} lookup keys')

    # Spinelli-Fenzi near-synonyms, keyed by normalized headword
    spinelli = {}
    scur.execute('SELECT norm_key, synonyms FROM spinelli')
    for norm_key, syns_json in scur.fetchall():
        spinelli[norm_key] = json.loads(syns_json)
    print(f'  Spinelli near-synonyms loaded for {len(spinelli)} headwords')

    # Döderlein also cites verbs by infinitive; same resolution trick as Ramshorn.
    doed_by_lemma = defaultdict(set)
    try:
        scur.execute('SELECT word, order_idx FROM doederlein_index')
        rows = scur.fetchall()
    except sqlite3.OperationalError:
        rows = []
        print('  (no doederlein_index table - run scripts/build_dbs.py to regenerate)')
    for word, order_idx in rows:
        doed_by_lemma[word].add(order_idx)
        mcur.execute("SELECT DISTINCT lemma FROM forms WHERE form = ? "
                     "AND analyses LIKE '%pres inf%'", (word,))
        for (lemma,) in mcur.fetchall():
            doed_by_lemma[lemma.lower()].add(order_idx)
    print(f'  Doederlein index resolved for {len(doed_by_lemma)} lookup keys')

    n = n_syn = n_morph = n_degree = n_parse_fail = 0
    deg_counter = 0
    with open(OUTPUT_XML_PATH, 'w', encoding='utf-8') as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write('<d:dictionary xmlns="http://www.w3.org/1999/xhtml" '
                  'xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng">\n\n')

        lcur.execute('SELECT key, lemma, xml FROM entries ORDER BY rowid')
        for key, lemma_display, fragment in lcur:
            n += 1
            base_key = re.sub(r'\d+$', '', key)
            title = sanitize_key(strip_length_marks(lemma_display).replace('-', '')) or base_key

            mcur.execute('SELECT form, analyses FROM forms WHERE lemma = ? OR lemma = ?',
                         (key, base_key))
            morph_rows = mcur.fetchall()
            classified = classify_and_grid(morph_rows, lemma_hint=base_key.lower()) if morph_rows else None
            degree_raw_forms = raw_degree_forms(morph_rows, lemma_hint=base_key.lower()) if morph_rows else set()

            # ---- search index: headword variants + all inflected forms.
            # Comparative/superlative forms are indexed only on their own
            # synthetic entry (below), not here - see write_degree_entries.
            indices = set()
            indices |= search_variants(lemma_display)
            indices |= search_variants(base_key)
            for form, _ in morph_rows:
                if form not in degree_raw_forms:
                    indices |= search_variants(form)

            # ---- L&S body
            domain_badges = []
            is_deponent = False
            try:
                entry_el = ET.fromstring(fragment)
                body_html = render_entry_body(entry_el)
                seen_badges = set()
                for usg in entry_el.iter():
                    if usg.tag.split('}')[-1] == 'usg' and usg.attrib.get('type') == 'dom':
                        label = clean_text(''.join(usg.itertext()))
                        if label and label not in seen_badges:
                            seen_badges.add(label)
                            domain_badges.append(label)
                for pos_el in entry_el.iter():
                    if pos_el.tag.split('}')[-1] == 'pos':
                        is_deponent = 'dep' in clean_text(''.join(pos_el.itertext())).lower()
                        break
            except ET.ParseError:
                n_parse_fail += 1
                text = clean_text(re.sub(r'<[^>]+>', ' ', fragment))
                body_html = f'<div class="sense">{html.escape(text)}</div>'

            # ---- synonyms
            syn_articles = []
            seen_nums = set()
            nums = sorted(syn_by_lemma.get(key.lower(), set()) |
                          syn_by_lemma.get(base_key.lower(), set()))
            for num in nums[:4]:
                scur.execute('SELECT num, headwords, body FROM articles WHERE num = ?', (num,))
                row = scur.fetchone()
                if row and row[0] not in seen_nums:
                    seen_nums.add(row[0])
                    syn_articles.append(row)
            spinelli_syns = spinelli.get(norm_join_key(lemma_display)) or \
                spinelli.get(norm_join_key(base_key))

            doed_articles = []
            seen_doed_nums = set()
            doed_nums = sorted(doed_by_lemma.get(key.lower(), set()) |
                               doed_by_lemma.get(base_key.lower(), set()))
            for order_idx in doed_nums[:4]:
                scur.execute('SELECT order_idx, headwords, body FROM doederlein WHERE order_idx = ?',
                            (order_idx,))
                row = scur.fetchone()
                if row and row[0] not in seen_doed_nums:
                    seen_doed_nums.add(row[0])
                    doed_articles.append(row)

            # ---- assemble
            out.write(f'    <d:entry id="ls_{n}" d:title="{html.escape(title)}">\n')
            for kw in sorted(indices):
                kw = sanitize_key(kw)
                if kw:
                    out.write(f'        <d:index d:value="{html.escape(kw)}"/>\n')
            out.write(f'        <h1 class="entry-lemma">{html.escape(lemma_display)}</h1>\n')
            if domain_badges:
                badges = ''.join(f'<span class="domain-badge">{html.escape(b)}</span>'
                                 for b in domain_badges)
                out.write(f'        <div class="domain-badges">{badges}</div>\n')
            out.write(f'        <div class="definition">{body_html}</div>\n')
            if syn_articles or spinelli_syns or doed_articles:
                n_syn += 1
                out.write(f'        {render_synonyms(syn_articles, spinelli_syns, doed_articles)}\n')
            if classified:
                morph_html = render_morphology(classified, is_deponent=is_deponent,
                                               lemma_hint=base_key.lower())
                if morph_html:
                    n_morph += 1
                    out.write(f'        {morph_html}\n')
            out.write('    </d:entry>\n\n')

            if classified and (classified[1] or classified[2]):
                before = deg_counter
                deg_counter = write_degree_entries(out, deg_counter, lemma_display, classified,
                                                    lemma_hint=base_key.lower())
                n_degree += deg_counter - before

            if n % 5000 == 0:
                print(f'  {n}/{total} (syn: {n_syn}, morph: {n_morph}, degree: {n_degree})')

        n_grammar = write_grammar_entries(out, n)
        n_terms = write_termination_entries(out, n + n_grammar)

        out.write('</d:dictionary>\n')

    print(f'Done. {n} entries; {n_syn} with synonyms, {n_morph} with morphology, '
          f'{n_parse_fail} XML-fallback; {n_degree} comparative/superlative entries; '
          f'{n_grammar} grammar entries; {n_terms} termination entries.')


# ---------------------------------------------------------------------------
# Grammar entries (Allen & Greenough, via scripts/build_grammar.py)
# ---------------------------------------------------------------------------

def write_grammar_entries(out, start_n):
    grammar_db = 'data/grammar.db'
    if not os.path.exists(grammar_db):
        print('  (no data/grammar.db - skipping grammar entries; '
              'run scripts/build_grammar.py first)')
        return 0

    conn = sqlite3.connect(grammar_db)
    cur = conn.cursor()
    n = start_n

    cur.execute('SELECT title, level, html FROM sections ORDER BY order_idx')
    for title, level, body_html in cur.fetchall():
        n += 1
        index_title = sanitize_key(strip_length_marks(title))
        out.write(f'    <d:entry id="ag_sect_{n}" d:title="{html.escape(index_title)}">\n')
        for kw in {index_title, index_title.lower(), title}:
            kw = sanitize_key(kw)
            if kw:
                out.write(f'        <d:index d:value="{html.escape(kw)}"/>\n')
        out.write(f'        <h1 class="entry-lemma ag-heading">{html.escape(title)}</h1>\n')
        out.write(f'        <p class="ag-level-label">Allen &amp; Greenough’s New Latin '
                  f'Grammar — {html.escape(level)}</p>\n')
        out.write(f'        <div class="definition ag-section">{body_html}</div>\n')
        out.write('    </d:entry>\n\n')

    cur.execute('SELECT title, section_num, html FROM rules ORDER BY order_idx')
    for title, sect_num, body_html in cur.fetchall():
        n += 1
        index_title = sanitize_key(strip_length_marks(title))
        out.write(f'    <d:entry id="ag_rule_{n}" d:title="{html.escape(index_title)}">\n')
        word_keys = {index_title, index_title.lower(), title}
        for kw in word_keys:
            kw = sanitize_key(kw)
            if kw:
                out.write(f'        <d:index d:value="{html.escape(kw)}"/>\n')
        # citation-style keys ("AG 419", "§419") bypass sanitize_key, which
        # would otherwise strip the leading "§" down to a bare, too-generic "419"
        for kw in (f'AG {sect_num}', f'A&G {sect_num}', f'§{sect_num}'):
            out.write(f'        <d:index d:value="{html.escape(kw)}"/>\n')
        out.write(f'        <h1 class="entry-lemma ag-heading">{html.escape(title)}</h1>\n')
        out.write(f'        <p class="ag-level-label">Allen &amp; Greenough §{html.escape(sect_num)}</p>\n')
        out.write(f'        <div class="definition ag-section">{body_html}</div>\n')
        out.write('    </d:entry>\n\n')

    conn.close()
    total = n - start_n
    print(f'  grammar: {total} entries added (Allen & Greenough)')
    return total


def write_termination_entries(out, start_n):
    if not os.path.exists(SYN_DB_PATH):
        print('  (no synonyms.db - skipping Terminations entries)')
        return 0

    conn = sqlite3.connect(SYN_DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute('SELECT roman, category, body FROM terminations ORDER BY order_idx')
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        print('  (no terminations table - run scripts/build_dbs.py to regenerate)')
        conn.close()
        return 0

    n = start_n
    for roman, category, body in rows:
        n += 1
        title = f'Latin Terminations {roman}'
        out.write(f'    <d:entry id="term_{n}" d:title="{html.escape(title)}">\n')
        for kw in (title, f'Ramshorn {roman}', f'Terminations {roman}'):
            out.write(f'        <d:index d:value="{html.escape(kw)}"/>\n')
        out.write(f'        <h1 class="entry-lemma ag-heading">{html.escape(title)}</h1>\n')
        out.write(f'        <p class="ag-level-label">Ramshorn, Dictionary of Latin '
                  f'Synonymes (1841) — {html.escape(category)}</p>\n')
        out.write(f'        <div class="definition ag-section"><p class="ag-p">'
                  f'{html.escape(body)}</p></div>\n')
        out.write('    </d:entry>\n\n')

    conn.close()
    total = n - start_n
    print(f'  terminations: {total} entries added (Ramshorn front matter)')
    return total


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    build()
