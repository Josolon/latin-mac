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

    print('== Diacritic-spelling search (bŏnus as well as bonus) ==')
    bonus_check = entry_by_title(content, 'bonus')
    check('bonus entry is indexed under its plain spelling',
          bonus_check and 'd:index d:value="bonus"' in bonus_check)
    check('bonus entry is ALSO indexed under its macron/breve spelling (bŏnus)',
          bonus_check and 'd:index d:value="bŏnus"' in bonus_check)

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

    print('== equus (noun morphology via numbered-lemma rescue) ==')
    equus = entry_by_title(content, 'equus')
    check('equus entry exists', equus is not None)
    check("equus has a Morphology section (Morpheus's lemma is 'equus1', "
          "not 'equus' - regression test for the numbered-lemma join)",
          equus and 'class="morph-section"' in equus)

    print('== semi-deponent verbs (audeo, gaudeo, soleo) ==')
    audeo = entry_by_title(content, 'audeo')
    check('audeo entry exists', audeo is not None)
    if audeo:
        check('labeled Principal Parts (semi-deponent)',
              'Principal Parts (semi-deponent)' in audeo)
        check('citation form is audeo, audere, ausus sum', 'ausus sum' in audeo)
    gaudeo = entry_by_title(content, 'gaudeo')
    check('gaudeo cites gavisus sum (irregular perfect participle)',
          gaudeo and 'gavisus sum' in gaudeo)
    soleo = entry_by_title(content, 'soleo')
    check('soleo cites solitus sum', soleo and 'solitus sum' in soleo)

    print('== Reconstructed pronunciation (Vox Latina) ==')
    check('equus shows IPA [ˈɛ.kʷʊs] (qu -> kʷ, disyllabic -> stress first syllable)',
          equus and 'ˈɛ.kʷʊs' in equus)
    check('bonus shows IPA [ˈbɔ.nʊs] (breve-marked o -> short ɔ)',
          bonus_check and 'ˈbɔ.nʊs' in bonus_check)
    dominus = entry_by_title(content, 'dominus')
    check('dominus shows IPA with antepenult stress (light penult -> ˈdɔ.mɪ.nʊs)',
          dominus and 'ˈdɔ.mɪ.nʊs' in dominus)

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
    check('amo cites Doederlein', amo and '(Döderlein)' in amo)
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

    print('== Adjective degree splitting (bonus/melior/optimus) ==')
    bonus_entries = re.findall(r'<d:entry[^>]*d:title="bonus">.*?</d:entry>', content, re.DOTALL)
    check('exactly one entry titled bonus', len(bonus_entries) == 1)
    bonus = bonus_entries[0] if bonus_entries else None
    check('bonus has a degree summary line', bonus and 'entry-degree-forms' in bonus)
    check('bonus degree summary cites melior and optimus (not the rare bonior/bonissimus)',
          bonus and 'melior' in bonus and 'optimus' in bonus)
    check('bonus positive declension is gender-split (masc/fem/neut)',
          bonus and 'Masculine' in bonus and 'Feminine' in bonus and 'Neuter' in bonus)
    check('bonus search index does not include comparative/superlative forms',
          bonus and 'd:index d:value="melior"' not in bonus
          and 'd:index d:value="optima"' not in bonus)
    melior = entry_by_title(content, 'melior')
    check('melior has its own synthetic entry', melior is not None)
    check('melior entry backlinks to its positive degree',
          melior and 'Comparative degree of' in melior)
    optimus = entry_by_title(content, 'optimus')
    check('optimus has its own synthetic entry (suppletive superlative)', optimus is not None)
    check('optimus entry backlinks to its positive degree',
          optimus and 'Superlative degree of' in optimus)
    rex = entry_by_title(content, 'rex')
    check('plain noun (rex) still renders a flat, non-gender-split declension table',
          rex and 'gender-label' not in rex)

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
