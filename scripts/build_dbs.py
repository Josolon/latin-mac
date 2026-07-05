"""Build the three SQLite databases for the Latin dictionary from raw sources.

  data/ls.db        <- data/lewis_short/lat.ls.perseus-eng2.xml  (Lewis & Short TEI-XML)
  data/morph.db     <- data/analyses/latin-lemmata.txt           (Morpheus full-form data via Diogenes)
  data/synonyms.db  <- data/ramshorn/ramshorn_1841_djvu.txt      (Ramshorn 1841, OCR)

Run from the repo root:  python3 scripts/build_dbs.py
"""
import os
import re
import sqlite3
import sys

LS_XML_PATH = 'data/lewis_short/lat.ls.perseus-eng2.xml'
LEMMATA_PATH = 'data/analyses/latin-lemmata.txt'
RAMSHORN_PATH = 'data/ramshorn/ramshorn_1841_djvu.txt'

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


def _clean_body_lines(lines):
    """Drop scanner noise / page furniture, then de-hyphenate OCR line breaks."""
    kept = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if NOISE_RE.match(s) or RUNNING_HEAD_RE.match(s):
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


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    build_ls()
    build_morph()
    build_synonyms()
