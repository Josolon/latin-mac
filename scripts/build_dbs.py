"""Build the three SQLite databases for the Latin dictionary from raw sources.

  data/ls.db        <- data/lewis_short/lat.ls.perseus-eng2.xml  (Lewis & Short TEI-XML)
  data/morph.db     <- data/analyses/latin-lemmata.txt           (Morpheus full-form data via Diogenes)
  data/synonyms.db  <- data/ramshorn/ramshorn_1841_djvu.txt      (Ramshorn 1841, OCR)
                       + data/spinelli/latin_near_synonyms.json  (Spinelli-Fenzi 2019, CC BY)

Run from the repo root:  python3 scripts/build_dbs.py
"""
import json
import os
import re
import sqlite3
import sys
import unicodedata

LS_XML_PATH = 'data/lewis_short/lat.ls.perseus-eng2.xml'
LEMMATA_PATH = 'data/analyses/latin-lemmata.txt'
RAMSHORN_PATH = 'data/ramshorn/ramshorn_1841_djvu.txt'
SPINELLI_PATH = 'data/spinelli/latin_near_synonyms.json'
DOEDERLEIN_PATH = 'data/doederlein/doederlein_gutenberg.txt'

LS_DB_PATH = 'data/ls.db'
MORPH_DB_PATH = 'data/morph.db'
SYN_DB_PATH = 'data/synonyms.db'


# --------------------------------------------------------------------------
# 1. Lewis & Short
# --------------------------------------------------------------------------

ENTRY_RE = re.compile(r'<entryFree\b.*?</entryFree>', re.DOTALL)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
ORTH_RE = re.compile(r'<orth[^>]*>(.*?)</orth>', re.DOTALL)
TAG_RE = re.compile(r'<[^>]+>')


def build_ls():
    print('== Lewis & Short ==')
    with open(LS_XML_PATH, encoding='utf-8') as f:
        content = f.read()

    if os.path.exists(LS_DB_PATH):
        os.remove(LS_DB_PATH)
    conn = sqlite3.connect(LS_DB_PATH)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE entries (
        key TEXT PRIMARY KEY, tei_id TEXT, entry_type TEXT,
        lemma TEXT, xml TEXT)''')
    cur.execute('CREATE INDEX idx_entries_lemma ON entries(lemma)')

    n = 0
    for m in ENTRY_RE.finditer(content):
        fragment = m.group(0)
        open_tag = fragment[:fragment.index('>') + 1]
        attrs = dict(ATTR_RE.findall(open_tag))
        key = attrs.get('key')
        if not key:
            continue
        om = ORTH_RE.search(fragment)
        lemma = TAG_RE.sub('', om.group(1)).strip() if om else key
        cur.execute('INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?)',
                    (key, attrs.get('id', ''), attrs.get('type', ''), lemma, fragment))
        n += 1
        if n % 10000 == 0:
            print(f'  {n} entries...')

    conn.commit()
    conn.close()
    print(f'  done: {n} entries -> {LS_DB_PATH}')


# --------------------------------------------------------------------------
# 2. Morphology (full-form) from the Diogenes latin-lemmata file
# --------------------------------------------------------------------------

FORM_RE = re.compile(r'^(\S+)\s+(\(.*\))$')


def build_morph():
    print('== Morphology ==')
    if os.path.exists(MORPH_DB_PATH):
        os.remove(MORPH_DB_PATH)
    conn = sqlite3.connect(MORPH_DB_PATH)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE forms (
        lemma TEXT, form TEXT, analyses TEXT)''')

    n_lemmas = n_forms = 0
    with open(LEMMATA_PATH, encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 3:
                continue
            lemma = parts[0]
            n_lemmas += 1
            rows = []
            for cell in parts[2:]:
                cell = cell.strip()
                if not cell:
                    continue
                fm = FORM_RE.match(cell)
                if fm:
                    form, analyses = fm.group(1), fm.group(2)
                else:
                    form, analyses = cell.split(' ')[0], ''
                rows.append((lemma, form, analyses))
            cur.executemany('INSERT INTO forms VALUES (?,?,?)', rows)
            n_forms += len(rows)
            if n_lemmas % 10000 == 0:
                print(f'  {n_lemmas} lemmata...')

    cur.execute('CREATE INDEX idx_forms_lemma ON forms(lemma)')
    cur.execute('CREATE INDEX idx_forms_form ON forms(form)')
    conn.commit()
    conn.close()
    print(f'  done: {n_lemmas} lemmata, {n_forms} forms -> {MORPH_DB_PATH}')


# --------------------------------------------------------------------------
# 3. Ramshorn synonyms
# --------------------------------------------------------------------------

NOISE_RE = re.compile(r'^\s*(Digitized by Google|[0-9]{1,3}|[A-Z]\.?)\s*$')
# Running page heads look like "2. Abdere. 5. Abominari." or "6. Absolvere. 9. Abstinens* 47"
RUNNING_HEAD_RE = re.compile(
    r'^\s*\d+\.\s+[A-Z][A-Za-z]*[.*,]?\s+\d+\.\s+[A-Z][A-Za-z]*[.*]?\s*\d*\s*$')
ARTICLE_START_RE = re.compile(r'^(\d+)\.\s+([A-Z].*)$')
# A headword token: a capitalized Latin word, possibly with internal space stripped by OCR
HEADWORD_TOKEN_RE = re.compile(r'^[A-Z][a-zA-Z]*$')

INDEX_ENTRY_RE = re.compile(r'^(.+?)[\s,]+((?:[0-9IVXLC]+[.,]?\s*)+)\s*$')


def _clean_body_lines(lines, extra_noise_re=None):
    """Drop scanner noise / page furniture, then de-hyphenate OCR line breaks."""
    kept = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if NOISE_RE.match(s) or RUNNING_HEAD_RE.match(s):
            continue
        if extra_noise_re and extra_noise_re.match(s):
            continue
        kept.append(s)
    text = '\n'.join(kept)
    text = re.sub(r'[¬¬]\s*\n\s*', '', text)   # join hyphenated breaks
    text = re.sub(r'[¬¬]\s*', '', text)          # stray OCR hyphens
    return text.split('\n')


def build_synonyms():
    print('== Ramshorn synonyms ==')
    with open(RAMSHORN_PATH, encoding='utf-8') as f:
        raw_lines = f.readlines()

    # Locate the main body and index boundaries
    body_start = index_start = None
    for i, ln in enumerate(raw_lines):
        if body_start is None and ln.strip() == 'LATIN SYNONYMES.':
            body_start = i + 1
        if ln.strip() == 'INDEX' and i > 20000:
            index_start = i + 1
            break
    if body_start is None or index_start is None:
        sys.exit('  ERROR: could not locate body/index boundaries in Ramshorn OCR')

    body_lines = _clean_body_lines(raw_lines[body_start:index_start - 1])

    # Split into numbered articles; article numbers increase monotonically,
    # which lets us reject inline "N." matches that are not real article starts.
    articles = []          # (num, [lines])
    current = None
    expected = 1
    for ln in body_lines:
        m = ARTICLE_START_RE.match(ln)
        if m:
            num = int(m.group(1))
            if expected <= num <= expected + 30:
                if current:
                    articles.append(current)
                current = (num, [m.group(2)])
                expected = num + 1
                continue
        if current:
            current[1].append(ln)
    if current:
        articles.append(current)

    if os.path.exists(SYN_DB_PATH):
        os.remove(SYN_DB_PATH)
    conn = sqlite3.connect(SYN_DB_PATH)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE articles (
        num INTEGER PRIMARY KEY, headwords TEXT, body TEXT)''')
    cur.execute('''CREATE TABLE index_words (
        word TEXT, num INTEGER)''')
    cur.execute('CREATE INDEX idx_index_words ON index_words(word)')

    for num, lines in articles:
        text = ' '.join(lines)
        text = re.sub(r'\s+', ' ', text).strip()
        # Headword list: leading capitalized words separated by commas, up to
        # the first period (the discussion then restarts with the first word).
        head_end = text.find('.')
        headwords = []
        if head_end > 0:
            for tok in re.split(r'[,;]', text[:head_end]):
                tok = tok.strip().rstrip('.;')
                tok = re.sub(r'\s+', '', tok)   # OCR splits inside words
                if HEADWORD_TOKEN_RE.match(tok):
                    headwords.append(tok)
        cur.execute('INSERT OR REPLACE INTO articles VALUES (?,?,?)',
                    (num, ','.join(headwords), text))

    # Index: "volitare 1036." / "Zona 202. 646." / "urbanus XI, 2.231."
    # Keep only arabic article references; Roman numerals refer to the
    # front-matter terminations chapters, which we do not include.
    n_index = 0
    for ln in raw_lines[index_start:]:
        s = ln.strip()
        if not s or NOISE_RE.match(s):
            continue
        s = re.sub(r'[¬¬]', '', s)
        m = re.match(r'^([A-Za-z][A-Za-z ,\-]*?)[\s,]+([0-9][0-9IVXLC ,.]*)\.?\s*$', s)
        if not m:
            continue
        words_part, refs_part = m.group(1), m.group(2)
        nums = [int(x) for x in re.findall(r'\b(\d+)\b', refs_part)]
        nums = [x for x in nums if 1 <= x <= len(articles) + 100]
        if not nums:
            continue
        for w in words_part.split(','):
            w = w.strip().lower()
            if not w or ' ' in w and len(w.split()) > 3:
                continue
            for x in nums:
                cur.execute('INSERT INTO index_words VALUES (?,?)', (w, x))
                n_index += 1

    conn.commit()
    conn.close()
    print(f'  done: {len(articles)} articles, {n_index} index refs -> {SYN_DB_PATH}')


# --------------------------------------------------------------------------
# 3b. Ramshorn's "Terminations" front matter (word-formation / suffix guide)
# --------------------------------------------------------------------------

# Running page heads here look like "Adjective Forms. XI." or
# "Adjective Forms. VII. — VIII.", optionally trailed by a page number.
TERM_RUNNING_HEAD_RE = re.compile(
    r'^\s*[A-Za-z]+ Forms\s*\.?\s*[IVXLC]+\.?(\s*[-—]+\s*[IVXLC]+\.?)?\s*\d*\s*$')
TERM_CATEGORY_RE = re.compile(r'^([A-E])\.\s+([A-Z][A-Za-z ]+?)\s*\.?\s*$')
TERM_SECTION_RE = re.compile(r'^([IVXLC]+)\.\s+(.+)$')


def build_terminations():
    """Ramshorn's front matter (before 'LATIN SYNONYMES.') is a continuous,
    monotonically Roman-numeral-numbered guide to Latin word-formation
    suffixes (I. S ..., II. tas ..., ... up to XXIV.), grouped under a
    handful of untitled major categories (A. Substantive Forms, B. Adjective
    Forms, C. Forms of Verbs, D. Adverbial Forms, E. Reduplication). The
    back-of-book index cites these as e.g. 'urbanus XI, 2.231.' - the 'XI'
    here, distinct from the arabic synonym-article numbers already parsed
    into `articles`.
    """
    print('== Ramshorn Terminations ==')
    with open(RAMSHORN_PATH, encoding='utf-8') as f:
        raw_lines = f.readlines()

    start = end = None
    for i, ln in enumerate(raw_lines):
        if start is None and ln.strip() == 'A. Substantive Forms .':
            start = i
        if ln.strip() == 'LATIN SYNONYMES.':
            end = i
            break
    if start is None or end is None:
        print('  WARNING: could not locate Terminations boundaries - skipping')
        return

    lines = _clean_body_lines(raw_lines[start:end], extra_noise_re=TERM_RUNNING_HEAD_RE)

    # Walk lines, tracking the current lettered category and splitting on
    # Roman-numeral section starts. The print numbering only increases (it
    # is not gap-free - e.g. it jumps straight from II to IV, skipping III
    # entirely), so a strictly-increasing check - not an exact-sequence
    # match - is what distinguishes a real header from stray OCR noise. The
    # very first section (I) is never labeled at all in the source; its text
    # is captured as a synthetic leading section instead.
    conn = sqlite3.connect(SYN_DB_PATH)
    cur = conn.cursor()
    cur.execute('DROP TABLE IF EXISTS terminations')
    cur.execute('''CREATE TABLE terminations (
        order_idx INTEGER PRIMARY KEY, roman TEXT, category TEXT, title TEXT, body TEXT)''')

    current_category = ''
    sections = []          # (roman, category, [lines])
    current = ('I', '', [])
    last_value = 0
    for ln in lines:
        cm = TERM_CATEGORY_RE.match(ln)
        if cm:
            current_category = cm.group(2).strip()
            if not current[2]:
                current = (current[0], current_category, current[2])
            continue
        sm = TERM_SECTION_RE.match(ln)
        if sm:
            roman_value = roman_to_int(sm.group(1))
            if roman_value > last_value:
                if current[2]:
                    sections.append(current)
                current = (sm.group(1), current_category, [sm.group(2)])
                last_value = roman_value
                continue
        current[2].append(ln)
    if current[2]:
        sections.append(current)

    for i, (roman, category, body_lines) in enumerate(sections, start=1):
        text = re.sub(r'\s+', ' ', ' '.join(body_lines)).strip()
        title = text[:100].rsplit(' ', 1)[0] if len(text) > 100 else text
        cur.execute('INSERT INTO terminations VALUES (?,?,?,?,?)',
                    (i, roman, category, title, text))

    conn.commit()
    conn.close()
    print(f'  done: {len(sections)} termination sections -> {SYN_DB_PATH} (table terminations)')


def roman_to_int(s):
    vals = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100}
    total = prev = 0
    for ch in reversed(s.upper()):
        v = vals.get(ch, 0)
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


# --------------------------------------------------------------------------
# 3c. Doederlein's Hand-book of Latin Synonymes (1875 tr., Project Gutenberg)
# --------------------------------------------------------------------------

# A headword line looks like "ABESSE; DEESSE; DEFICERE. 1. +Abesse+ denotes..."
# - one or more ALL-CAPS Latin headwords, semicolon/comma-separated, ending
# in a period right before the numbered discussion begins on the same line.
DOED_ARTICLE_RE = re.compile(
    r'^([A-ZÆŒ][A-ZÆŒ]+(?:[;,]\s+[A-ZÆŒ][A-ZÆŒ \'.]+)*)\.\s', re.MULTILINE)
# Cross-reference stubs: "ABDERE, see _Celare_." - redirect to another article
# rather than a discussion of its own.
DOED_XREF_RE = re.compile(r'^([A-ZÆŒ][A-ZÆŒ]+), see _([A-Za-z]+)_\.\s*$', re.MULTILINE)


def _clean_doederlein_text(text):
    """Convert Gutenberg's plain-text emphasis markup to readable prose."""
    text = re.sub(r'\+([^+]+)\+', r'\1', text)     # +word+ (bold/spaced) -> word
    text = re.sub(r'_([^_]+)_', r'\1', text)        # _word_ (italic) -> word
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def build_doederlein():
    """Döderlein's headword lines double as their own index - no back-of-book
    index parsing needed, unlike Ramshorn."""
    print("== Döderlein's Hand-book of Latin Synonymes ==")
    with open(DOEDERLEIN_PATH, encoding='utf-8') as f:
        content = f.read()

    start_m = re.search(r'\n\nA\.\n\n', content)
    end_idx = content.find('INDEX OF GREEK WORDS.')
    if not start_m or end_idx < 0:
        print('  WARNING: could not locate Doederlein body boundaries - skipping')
        return
    body = content[start_m.end():end_idx]

    matches = list(DOED_ARTICLE_RE.finditer(body))
    conn = sqlite3.connect(SYN_DB_PATH)
    cur = conn.cursor()
    cur.execute('DROP TABLE IF EXISTS doederlein')
    cur.execute('DROP TABLE IF EXISTS doederlein_index')
    cur.execute('''CREATE TABLE doederlein (
        order_idx INTEGER PRIMARY KEY, headwords TEXT, body TEXT)''')
    cur.execute('''CREATE TABLE doederlein_index (
        word TEXT, order_idx INTEGER)''')
    cur.execute('CREATE INDEX idx_doed_index_word ON doederlein_index(word)')

    for i, m in enumerate(matches):
        headwords_raw = m.group(1)
        headwords = [h.strip().rstrip('.') for h in re.split(r'[;,]', headwords_raw)]
        seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = _clean_doederlein_text(body[m.end():seg_end])
        cur.execute('INSERT INTO doederlein VALUES (?,?,?)',
                    (i + 1, ','.join(headwords), text))
        for h in headwords:
            cur.execute('INSERT INTO doederlein_index VALUES (?,?)', (h.lower(), i + 1))

    n_xref = 0
    for m in DOED_XREF_RE.finditer(body):
        source, target = m.group(1).strip(), m.group(2).strip().lower()
        cur.execute('SELECT order_idx FROM doederlein_index WHERE word = ? LIMIT 1', (target,))
        row = cur.fetchone()
        if row:
            cur.execute('INSERT INTO doederlein_index VALUES (?,?)', (source.lower(), row[0]))
            n_xref += 1

    conn.commit()
    conn.close()
    print(f'  done: {len(matches)} articles, {n_xref} cross-reference redirects '
          f'-> {SYN_DB_PATH} (table doederlein)')


def norm_join_key(word):
    """Normalize a Latin word for joining across sources: strip length marks,
    lowercase, i-for-j, u-for-v, no spaces (res publica ~ respublica)."""
    d = unicodedata.normalize('NFD', word)
    d = ''.join(ch for ch in d if ord(ch) not in (0x0304, 0x0306))
    d = unicodedata.normalize('NFC', d).lower()
    return d.replace('j', 'i').replace('v', 'u').replace(' ', '')


def build_spinelli():
    """Spinelli-Fenzi near-synonyms (2019, CC BY) into synonyms.db."""
    print('== Spinelli near-synonyms ==')
    with open(SPINELLI_PATH, encoding='utf-8') as f:
        data = json.load(f)

    conn = sqlite3.connect(SYN_DB_PATH)
    cur = conn.cursor()
    cur.execute('DROP TABLE IF EXISTS spinelli')
    cur.execute('''CREATE TABLE spinelli (
        norm_key TEXT PRIMARY KEY, headword TEXT, synonyms TEXT)''')

    for headword, syns in data.items():
        # dedupe, drop the headword itself, keep source order
        seen = set()
        out = []
        hw_norm = norm_join_key(headword)
        for s in syns:
            word = s.split('[')[0].strip()
            key = (norm_join_key(word), s.strip())
            if key in seen or norm_join_key(word) == hw_norm:
                continue
            seen.add(key)
            out.append(s.strip())
        cur.execute('INSERT OR REPLACE INTO spinelli VALUES (?,?,?)',
                    (hw_norm, headword.strip(), json.dumps(out, ensure_ascii=False)))

    conn.commit()
    conn.close()
    print(f'  done: {len(data)} headwords -> {SYN_DB_PATH} (table spinelli)')


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    build_ls()
    build_morph()
    build_synonyms()
    build_terminations()
    build_spinelli()
    build_doederlein()
