"""Parse Allen & Greenough's New Latin Grammar (Perseus TEI-XML) into
data/grammar.db.

Two kinds of entries come out of this:
  - "sections": one per <div type="textpart"> that carries a <head> title
    (part/section/subsection/subsubsection), containing only that div's own
    direct content (not its children's, to avoid duplicating text at every
    nesting level).
  - "rules": individual named grammar rules identified by the classic A&G
    run-in-topic pattern - a short title paragraph immediately followed by
    a numbered <milestone unit="smythp"> - e.g. "Ablative Absolute" -> §419.
    These are the phrases people actually look up (a commentary note reads
    "abl. absol." and the reader wants the rule, not the whole chapter).

Run from the repo root:  python3 scripts/build_grammar.py
"""
import html
import os
import re
import sqlite3
import xml.etree.ElementTree as ET

AG_XML_PATH = 'data/allen_greenough/ag_grammar.xml'
GRAMMAR_DB_PATH = 'data/grammar.db'

NS = '{http://www.tei-c.org/ns/1.0}'


def tag(el):
    return el.tag.split('}')[-1]


def clean(text):
    return re.sub(r'\s+', ' ', (text or '')).strip()


TITLE_WORD_STOP = {'of', 'the', 'and', 'or', 'in', 'a', 'an', 'with', 'to'}


def looks_like_rule_title(text):
    t = clean(text)
    if not t or t.endswith(('.', ':', ';')):
        return False
    words = t.split()
    if not (1 <= len(words) <= 7):
        return False
    sig = [w for w in words if w.lower() not in TITLE_WORD_STOP]
    return bool(sig) and all(w[0].isupper() for w in sig if w[0].isalpha())


# ---------------------------------------------------------------------------
# Inline/mixed-content renderer for A&G's TEI tags
# ---------------------------------------------------------------------------

def render_node(node, current_sect):
    """Render a single node (and its subtree) to HTML. Returns (html, last_sect)
    where last_sect tracks the running A&G section number for citations."""
    t = tag(node)
    parts = []

    if t == 'milestone' and node.get('unit') == 'smythp':
        n = node.get('n', '')
        if n:
            current_sect[0] = n
            parts.append(f'<span class="ag-sect-num">§{html.escape(n)}</span>')
        return '', current_sect[0]

    if t in ('pb', 'cb', 'milestone'):
        return '', current_sect[0]

    inner = []
    if node.text:
        inner.append(html.escape(clean(node.text)))
    for child in node:
        if tag(child) == 'div':
            continue  # handled separately by the section walker
        child_html, _ = render_node(child, current_sect)
        inner.append(child_html)
        if child.tail:
            inner.append(html.escape(clean(child.tail)))
    body = ' '.join(x for x in inner if x)
    body = re.sub(r'\s+([,;:.])', r'\1', body)

    if t == 'p':
        return f'<p class="ag-p">{body}</p>' if body else '', current_sect[0]
    if t == 'note':
        return f'<span class="ag-note">{body}</span>' if body else '', current_sect[0]
    if t in ('emph',):
        return f'<i>{body}</i>', current_sect[0]
    if t == 'foreign':
        return f'<b class="la-word">{body}</b>', current_sect[0]
    if t == 'gloss':
        return f'<i class="ag-gloss">{body}</i>', current_sect[0]
    if t in ('cit', 'quote', 'bibl'):
        return f'<span class="citation">{body}</span>', current_sect[0]
    if t == 'list':
        items = []
        for child in node:
            if tag(child) == 'item':
                ih, _ = render_node(child, current_sect)
                items.append(ih)
        return f'<ul class="ag-list">{"".join(items)}</ul>', current_sect[0]
    if t == 'item':
        return f'<li>{body}</li>' if body else '', current_sect[0]
    if t == 'ref':
        return f'<span class="ag-ref">{body}</span>', current_sect[0]
    if t == 'head':
        return '', current_sect[0]  # heads are consumed as titles, not body
    return body, current_sect[0]


def render_own_content(div, current_sect):
    """Render only the direct non-div children of a div (its 'own' content),
    tracking the running section number as milestones are encountered."""
    parts = []
    if div.text:
        parts.append(html.escape(clean(div.text)))
    for child in div:
        if tag(child) == 'div':
            if child.tail:
                parts.append(html.escape(clean(child.tail)))
            continue
        child_html, _ = render_node(child, current_sect)
        parts.append(child_html)
        if child.tail:
            parts.append(html.escape(clean(child.tail)))
    return ' '.join(x for x in parts if x)


# ---------------------------------------------------------------------------
# Tree walk
# ---------------------------------------------------------------------------

def walk(div, current_sect, sections, order):
    head = div.find(f'{NS}head')
    title = clean(''.join(head.itertext())) if head is not None else None
    level = div.get('subtype') or div.get('type') or 'div'

    if title:
        body_html = render_own_content(div, current_sect)
        if body_html.strip():
            order[0] += 1
            sections.append({
                'title': title, 'level': level, 'html': body_html,
                'order_idx': order[0],
            })

    for child in div:
        if tag(child) == 'div':
            walk(child, current_sect, sections, order)


def find_rules(root):
    """Scan the whole document for the run-in-topic-before-milestone pattern
    and render each as its own focused entry."""
    rules = []
    all_ps = list(root.iter(f'{NS}p'))
    milestones = {id(m): m for m in root.iter(f'{NS}milestone') if m.get('unit') == 'smythp'}

    parent_map = {c: p for p in root.iter() for c in p}

    def next_sibling_element(el):
        parent = parent_map.get(el)
        if parent is None:
            return None
        siblings = list(parent)
        idx = siblings.index(el)
        for sib in siblings[idx + 1:]:
            return sib
        return None

    order = 0
    for p in all_ps:
        text = clean(''.join(p.itertext()))
        if not looks_like_rule_title(text):
            continue
        nxt = next_sibling_element(p)
        # Allow an intervening old_Subsub milestone between the title <p> and
        # the real numbered milestone.
        hops = 0
        while nxt is not None and tag(nxt) == 'milestone' and nxt.get('unit') != 'smythp' and hops < 2:
            nxt = next_sibling_element(nxt)
            hops += 1
        if nxt is None or tag(nxt) != 'milestone' or nxt.get('unit') != 'smythp':
            continue
        sect_num = nxt.get('n', '')

        # Render forward from the milestone until the next milestone/heading
        # boundary: collect sibling elements after nxt within the same parent.
        parent = parent_map.get(nxt)
        siblings = list(parent)
        start_idx = siblings.index(nxt) + 1
        current_sect = [sect_num]
        body_parts = []
        for sib in siblings[start_idx:]:
            st = tag(sib)
            if st == 'div':
                break
            if st == 'milestone' and sib.get('unit') == 'smythp':
                break
            if st == 'p' and looks_like_rule_title(clean(''.join(sib.itertext()))):
                # a following rule-title paragraph ends this rule's content
                # only if it's itself followed by another milestone
                break
            h, _ = render_node(sib, current_sect)
            body_parts.append(h)
        body_html = ' '.join(x for x in body_parts if x)
        if not body_html.strip():
            continue
        order += 1
        rules.append({
            'title': text, 'section_num': sect_num, 'html': body_html,
            'order_idx': order,
        })
    return rules


def build():
    tree = ET.parse(AG_XML_PATH)
    root = tree.getroot()
    body = root.find(f'.//{NS}body')

    current_sect = ['']
    sections = []
    order = [0]
    for child in body:
        if tag(child) == 'div':
            walk(child, current_sect, sections, order)

    rules = find_rules(root)

    if os.path.exists(GRAMMAR_DB_PATH):
        os.remove(GRAMMAR_DB_PATH)
    conn = sqlite3.connect(GRAMMAR_DB_PATH)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE sections (
        order_idx INTEGER PRIMARY KEY, title TEXT, level TEXT, html TEXT)''')
    cur.execute('''CREATE TABLE rules (
        order_idx INTEGER PRIMARY KEY, title TEXT, section_num TEXT, html TEXT)''')
    cur.executemany('INSERT INTO sections VALUES (?,?,?,?)',
                     [(s['order_idx'], s['title'], s['level'], s['html']) for s in sections])
    cur.executemany('INSERT INTO rules VALUES (?,?,?,?)',
                     [(r['order_idx'], r['title'], r['section_num'], r['html']) for r in rules])
    conn.commit()
    conn.close()
    print(f'Grammar: {len(sections)} section entries, {len(rules)} named-rule entries '
          f'-> {GRAMMAR_DB_PATH}')


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    build()
