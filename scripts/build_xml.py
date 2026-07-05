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

def render_inline(node, skip_senses=True):
    """Flatten a TEI node's mixed content into styled HTML (no nested senses)."""
    frags = []
    if node.text:
        frags.append(html.escape(clean_text(node.text)))
    for child in node:
        tag = child.tag.split('}')[-1]
        if skip_senses and tag == 'sense':
            if child.tail:
                frags.append(html.escape(clean_text(child.tail)))
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
        elif tag in ('bibl', 'cit', 'quote'):
            frags.append(f'<span class="citation">{html.escape(text)}</span>')
        elif tag == 'usg':
            if child.attrib.get('type') == 'dom':
                frags.append(f'<span class="usg-domain">{html.escape(text)}</span>')
            else:
                frags.append(f'<span class="usg-style">{html.escape(text)}</span>')
        elif tag in ('case', 'mood', 'number'):
            frags.append(f'<span class="gram-abbr">{html.escape(text)}</span>')
        elif tag == 'lbl':
            frags.append(f'<span class="gram-lbl">{html.escape(text)}</span>')
        elif tag in ('pb', 'cb'):
            pass
        else:
            frags.append(html.escape(text))
        if child.tail:
            frags.append(html.escape(clean_text(child.tail)))
    out = ' '.join(f for f in frags if f)
    return re.sub(r'\s+([,;:.])', r'\1', re.sub(r'\s+', ' ', out)).strip()


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
    # in progress, a new lettered group has begun - flag it with a divider.
    last_ordinal_by_depth = {}

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

        major = ' sense-major' if depth == 0 and n and ROMAN_NUM_RE.match(n) else ''

        restart = ''
        if depth >= 1 and n:
            ordv = label_ordinal(n)
            if ordv is not None:
                prev = last_ordinal_by_depth.get(depth)
                if prev is not None and ordv <= prev:
                    restart = ' sense-group-restart'
                last_ordinal_by_depth[depth] = ordv
                # A restarted group invalidates tracking for any deeper level
                for d in list(last_ordinal_by_depth):
                    if d > depth:
                        del last_ordinal_by_depth[d]

        num_html = f'<span class="sense-num">{html.escape(n)}</span> ' if n else ''
        parts.append(
            f'<div class="sense sense-depth-{min(depth, 4)}{major}{restart}">'
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


def render_synonyms(articles, spinelli_syns=None):
    parts = ['<div class="syn-section">',
             '<p class="section-label">Synonyms &amp; Near-Synonyms</p>']
    if spinelli_syns:
        items = ', '.join(html.escape(s) for s in spinelli_syns)
        parts.append('<div class="syn-article">')
        parts.append(f'<p class="syn-body syn-spinelli">{items} '
                     f'<span class="syn-ref">(Spinelli–Fenzi 2019)</span></p>')
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


def classify_and_grid(rows):
    """From (form, analyses) rows build noun grid and verb principal parts."""
    form_analyses = {}
    for form, analyses in rows:
        groups = ANALYSIS_GROUP_RE.findall(analyses or '')
        if groups:
            form_analyses.setdefault(form, set()).update(groups)
    form_analyses = drop_enclitic_variants(form_analyses)

    noun_grid = defaultdict(lambda: defaultdict(set))
    verb_parts = defaultdict(set)   # (tense, voice) -> 1st sg ind forms
    infinitives = defaultdict(set)  # (tense, voice) -> forms
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

            if tense and 'inf' in tokset:
                infinitives[(tense, voice or 'act')].add(form)
                n_verbal += 1
            elif tense and 'ind' in tokset and '1st' in tokset and number == 'sg' \
                    and 'part' not in tokset:
                verb_parts[(tense, voice or 'act')].add(form)
                n_verbal += 1
            elif tense:
                n_verbal += 1

            if number and 'part' not in tokset and not tense:
                if combined:
                    for t in combined:
                        for p in t.split('/'):
                            if p in CASES:
                                noun_grid[p][number].add(form)
                                n_nominal += 1
                elif case:
                    noun_grid[case][number].add(form)
                    n_nominal += 1

    return noun_grid, verb_parts, infinitives, n_nominal, n_verbal


def render_morphology(rows):
    noun_grid, verb_parts, infinitives, n_nominal, n_verbal = classify_and_grid(rows)
    parts = []

    if n_verbal > n_nominal and verb_parts:
        parts.append('<div class="morph-section">')
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
        parts.append('</table></div>')
    elif noun_grid:
        parts.append('<div class="morph-section">')
        parts.append('<p class="section-label">Morphology — Declension</p>')
        parts.append('<table class="morphology-table">')
        parts.append('<tr><th>Case</th><th>Singular</th><th>Plural</th></tr>')
        for c in CASES:
            if c not in noun_grid:
                continue
            sg = join_forms(noun_grid[c].get('sg', [])) or '—'
            pl = join_forms(noun_grid[c].get('pl', [])) or '—'
            parts.append(f'<tr><td class="case-label">{CASE_LABELS[c]}</td>'
                         f'<td>{html.escape(sg)}</td><td>{html.escape(pl)}</td></tr>')
        parts.append('</table></div>')

    return ''.join(parts)


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

    n = n_syn = n_morph = n_parse_fail = 0
    with open(OUTPUT_XML_PATH, 'w', encoding='utf-8') as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write('<d:dictionary xmlns="http://www.w3.org/1999/xhtml" '
                  'xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng">\n\n')

        lcur.execute('SELECT key, lemma, xml FROM entries ORDER BY rowid')
        for key, lemma_display, fragment in lcur:
            n += 1
            base_key = re.sub(r'\d+$', '', key)
            title = sanitize_key(strip_length_marks(lemma_display).replace('-', '')) or base_key

            # ---- search index: headword variants + all inflected forms
            indices = set()
            indices |= search_variants(lemma_display)
            indices |= search_variants(base_key)
            mcur.execute('SELECT form, analyses FROM forms WHERE lemma = ? OR lemma = ?',
                         (key, base_key))
            morph_rows = mcur.fetchall()
            for form, _ in morph_rows:
                indices |= search_variants(form)

            # ---- L&S body
            domain_badges = []
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
            if syn_articles or spinelli_syns:
                n_syn += 1
                out.write(f'        {render_synonyms(syn_articles, spinelli_syns)}\n')
            if morph_rows:
                morph_html = render_morphology(morph_rows)
                if morph_html:
                    n_morph += 1
                    out.write(f'        {morph_html}\n')
            out.write('    </d:entry>\n\n')

            if n % 5000 == 0:
                print(f'  {n}/{total} (syn: {n_syn}, morph: {n_morph})')

        n_grammar = write_grammar_entries(out, n)

        out.write('</d:dictionary>\n')

    print(f'Done. {n} entries; {n_syn} with synonyms, {n_morph} with morphology, '
          f'{n_parse_fail} XML-fallback; {n_grammar} grammar entries.')


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


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    build()
