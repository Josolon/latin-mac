"""Regression tests for src/LatinDictionary.xml.

Catches the class of bugs found by hand during development: XML validity,
missing sections on known entries, silently-broken parsers. Run after
build_xml.py, before compiling with the DDK.

Run from the repo root:  python3 scripts/test_dictionary.py
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

XML_PATH = 'src/LatinDictionary.xml'

failures = []


def check(name, condition):
    status = 'PASS' if condition else 'FAIL'
    print(f'  [{status}] {name}')
    if not condition:
        failures.append(name)


def entry_by_title(content, title):
    m = re.search(rf'<d:entry[^>]*d:title="{re.escape(title)}">.*?</d:entry>', content, re.DOTALL)
    return m.group(0) if m else None


def main():
    if not os.path.exists(XML_PATH):
        sys.exit(f'ERROR: {XML_PATH} not found - run scripts/build_xml.py first')

    print('== XML well-formedness ==')
    try:
        ET.parse(XML_PATH)
        check('XML parses without error', True)
    except ET.ParseError as e:
        check(f'XML parses without error ({e})', False)

    with open(XML_PATH, encoding='utf-8') as f:
        content = f.read()

    print('== Entry counts ==')
    n_entries = content.count('<d:entry ')
    check(f'at least 51000 d:entry elements (found {n_entries})', n_entries >= 51000)

    print('== amo (regular verb: principal parts, subjunctive, imperative, gerundive) ==')
    amo = entry_by_title(content, 'amo')
    check('amo entry exists', amo is not None)
    if amo:
        check('has Principal Parts callout', 'class="principal-parts"' in amo)
        check('Principal Parts reads amo, amare, amavi, amatus',
              'amo</b>, <b class="la-word">amare</b>, <b class="la-word">amavi</b>, '
              '<b class="la-word">amatus</b>' in amo)
        check('has Subjunctive table', 'Subjunctive (1st sg.)' in amo)
        check('present subjunctive active is amem', '<td>amem</td>' in amo)
        check('has Imperative table', 'Morphology — Imperative' in amo)
        check('has Gerundive row', '>Gerundive<' in amo and 'amandus' in amo)
        check('quoted example splits quote/trans/bibl',
              'class="cit-quote"' in amo and 'class="cit-trans"' in amo
              and 'class="cit-bibl"' in amo)
        check('has a sense-group-restart divider (Hence, amans transition)',
              'sense-group-restart' in amo)

    print('== sequor (deponent verb) ==')
    sequor = entry_by_title(content, 'sequor')
    check('sequor entry exists', sequor is not None)
    if sequor:
        check('labeled Principal Parts (deponent)', 'Principal Parts (deponent)' in sequor)
        check('citation form is sequor, sequi, secutus sum', 'secutus sum' in sequor)

    print('== abactor (word-root etymology) ==')
    abactor = entry_by_title(content, 'abactor')
    check('abactor entry exists', abactor is not None)
    if abactor:
        check('etymology bracketed as [abigo]', 'class="etym">[abigo]</span>' in abactor)

    print('== Usage/domain labels ==')
    check('at least one domain-badge rendered somewhere', 'class="domain-badge"' in content)
    check('at least one usg-style label rendered somewhere', 'class="usg-style"' in content)
    check('cross-reference markers styled (xr-ref)', 'class="xr-ref"' in content)

    print('== Synonyms (Ramshorn + Spinelli + Doederlein) ==')
    check('amo has a synonyms section', amo and 'class="syn-section"' in amo)
    check('amo cites Ramshorn (§66)', amo and 'Ramshorn §66' in amo)
    check('amo cites Doederlein (§145)', amo and 'Döderlein §145' in amo)
    iubeo = entry_by_title(content, 'jubeo')
    check('jubeo has a Spinelli near-synonym list (via iubeo, i/j-normalized)',
          iubeo and 'syn-spinelli' in iubeo)
    tempus = entry_by_title(content, 'tempus')
    check('tempus has all three synonym sources (Ramshorn + Spinelli + Doederlein)',
          tempus and 'syn-spinelli' in tempus and 'Döderlein' in tempus and 'Ramshorn' in tempus)

    print('== Grammar entries (Allen & Greenough) ==')
    ablabs = entry_by_title(content, 'Ablative Absolute')
    check('Ablative Absolute entry exists', ablabs is not None)
    if ablabs:
        check('indexed as AG 419', '<d:index d:value="AG 419"/>' in ablabs)
        check('indexed as §419', '<d:index d:value="§419"/>' in ablabs)
    check('A&G paradigm tables render as real <table> (not flattened)',
          '<table class="ag-table">' in content and '<tr><td>' in content)

    print('== Ramshorn Terminations ==')
    term_xi = entry_by_title(content, 'Latin Terminations XI')
    check('Latin Terminations XI entry exists', term_xi is not None)

    print()
    if failures:
        print(f'{len(failures)} check(s) failed:')
        for f in failures:
            print(f'  - {f}')
        sys.exit(1)
    else:
        print('All checks passed.')


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
