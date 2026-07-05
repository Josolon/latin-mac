# 🏛️ macOS Latin Dictionary

A custom `.dictionary` plugin for the native macOS Dictionary app and system-wide "Look Up" feature. This dictionary combines the **complete Lewis & Short Latin Dictionary** (51,636 unabridged entries) with a **Synonyms & Near-Synonyms section** from Ramshorn's *Dictionary of Latin Synonymes* and **always-visible morphology tables** (declension grids and verb conjugation forms) from the Perseus/Morpheus full-form analyses.

**v1.0.0** — Full unabridged Lewis & Short with sense hierarchy, overview boxes, Ramshorn synonym articles inline, and inflected-form lookup (looking up *amabant* lands on *amo*).

## ✨ Features

* **51k Unabridged Lewis & Short Entries:** The complete 1879 Harpers'/Oxford *A Latin Dictionary* compiled from Perseus TEI-XML into the macOS `.dictionary` format.
* **Synonyms & Near-Synonyms:** 1,015 articles from Ramshorn's *Dictionary of Latin Synonymes* (tr. Lieber, 1841) attached to the relevant entries — e.g. *amo* shows the Amare/Diligere/Amicus/Caritas discussion distinguishing the shades of meaning.
* **Morphology Tables:** Nouns and adjectives get a Case × Number declension grid; verbs get an indicative (1st sg.) table across all six tenses in both voices, plus infinitives — built from the Perseus/Morpheus full-form analyses (392k forms).
* **Inflected-Form Lookup:** Every attested inflected form is indexed, so macOS "Look Up" (Force Click / Three-Finger Tap) works from any word in a real Latin text, not just dictionary headwords. Orthographic variants (i/j, u/v) are indexed too.
* **System Integration:** Works natively with Dictionary.app and system-wide Look Up.

## 📦 Installation (For End Users)

1. Download the latest release from the [Releases](https://github.com/Josolon/latin-mac/releases) page.
2. Unzip and drag `LatinDictionary.dictionary` into `~/Library/Dictionaries/`.
3. Open the macOS **Dictionary app**, go to **Settings**, and enable "Latin (Lewis & Short)".

## 🛠️ Building from Source

### Prerequisites

* Python 3.x (standard library only — no packages needed)
* [Dictionary Development Kit](https://developer.apple.com/download/all/) (in Apple's "Additional Tools for Xcode"), expected at `/Applications/XcodeAdditionalTools/Utilities/DictionaryDevelopmentKit` (rename `/Applications/Additional Tools for Xcode` — no spaces in the path)
* macOS with Xcode command-line tools

### Step 1 — Download the source data

None of this is committed to the repo (see `.gitignore`):

1. **Lewis & Short TEI-XML (CC BY-SA 4.0)** — download into `data/lewis_short/`:
   ```bash
   mkdir -p data/lewis_short
   curl -L -o data/lewis_short/lat.ls.perseus-eng2.xml \
     https://raw.githubusercontent.com/PerseusDL/lexica/master/CTS_XML_TEI/perseus/pdllex/lat/ls/lat.ls.perseus-eng2.xml
   ```

2. **Morphology (Perseus/Morpheus full-form analyses, via the Diogenes prebuilt data)** — extract `latin-lemmata.txt` into `data/analyses/`:
   ```bash
   curl -L -o /tmp/prebuilt-data.tar.xz \
     https://github.com/pjheslin/diogenes-prebuilt-data/raw/master/prebuilt-data.tar.xz
   mkdir -p data/analyses
   tar -xf /tmp/prebuilt-data.tar.xz -C /tmp dependencies/data/latin-lemmata.txt
   mv /tmp/dependencies/data/latin-lemmata.txt data/analyses/
   ```

3. **Ramshorn synonyms (public domain)** — OCR text from archive.org into `data/ramshorn/`:
   ```bash
   mkdir -p data/ramshorn
   curl -L -o data/ramshorn/ramshorn_1841_djvu.txt \
     "https://archive.org/download/ramshorn-lewis-dictionary-of-latin-synonymes-1841/RAMSHORN%2C%20Lewis%20-%20Dictionary%20Of%20Latin%20Synonymes%20%5B1841%5D_djvu.txt"
   ```

### Step 2 — Build the databases and dictionary XML

```bash
python3 scripts/build_dbs.py    # TEI-XML / analyses / OCR -> SQLite (ls.db, morph.db, synonyms.db)
python3 scripts/build_xml.py    # SQLite -> src/LatinDictionary.xml (~106 MB)
```

### Step 3 — Compile and install

```bash
cd src
make install
```

Then open **Dictionary.app → Settings** and enable "Latin (Lewis & Short)".

(If the DDK errors with `unable to parse objects/dict.plist`, simply re-run `make` — it is a transient failure of the kit's xsltproc step.)

## 📁 Project Structure

```
latin-mac/
├── data/
│   ├── lewis_short/           # Perseus L&S TEI-XML [gitignored]
│   ├── analyses/              # Morpheus full-form data via Diogenes [gitignored]
│   ├── ramshorn/              # Ramshorn 1841 OCR text [gitignored]
│   ├── morpheus/              # Morpheus engine checkout, optional [gitignored]
│   ├── ls.db                  # SQLite L&S entries [gitignored]
│   ├── morph.db               # SQLite full-form morphology [gitignored]
│   └── synonyms.db            # SQLite Ramshorn articles + index [gitignored]
├── scripts/
│   ├── build_dbs.py           # Raw sources -> SQLite
│   ├── build_xml.py           # SQLite -> Apple Dictionary XML
│   └── install_dictionary.sh  # One-command build & install
├── src/
│   ├── LatinDictionary.xml    # Generated dictionary source [gitignored]
│   ├── LatinDictionary.css    # Dictionary styling
│   ├── LatinDictionary.plist  # Apple Dictionary metadata
│   ├── Makefile               # Build rules
│   └── objects/               # Build artifacts [gitignored]
└── README.md
```

## 📚 Data Sources

* **Lewis & Short:** *A Latin Dictionary* (1879), TEI-XML from the [Perseus Digital Library](https://github.com/PerseusDL/lexica) — CC BY-SA 4.0, with funding from The National Endowment for the Humanities.
* **Morphology:** Full-form analyses generated by the Perseus [Morpheus](https://github.com/perseids-tools/morpheus) analyzer, as packaged by [Diogenes](https://github.com/pjheslin/diogenes-prebuilt-data).
* **Synonyms:** Ramshorn, *Dictionary of Latin Synonymes, for the use of schools and private students*, translated by Francis Lieber (Boston, 1841) — public domain, OCR text from the [Internet Archive](https://archive.org/details/ramshorn-lewis-dictionary-of-latin-synonymes-1841).

## 🤝 Contributing

Contributions are welcome. The most valuable are:

* **Weird/broken entries:** With 51,636 entries auto-generated from TEI-XML and an 1841 OCR text, edge cases slip through (mangled preambles, mis-parsed synonym articles, garbled OCR in the synonyms sections). Open an issue with the headword and a screenshot, or trace it to `scripts/build_xml.py` / `scripts/build_dbs.py` and send a PR.
* **Ramshorn OCR cleanup:** The synonym article text comes from OCR and retains scanning errors. Corrections to the parsing heuristics in `build_dbs.py` (or a cleaner source text) would improve every affected entry.
* **Styling:** Enhance `src/LatinDictionary.css`.

## 📄 License

* **Code** (Python scripts, CSS, Makefile): [MIT License](LICENSE)
* **Lewis & Short text**: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (per Perseus; see their availability statement)
* **Ramshorn synonyms**: public domain
* **Morphology data**: Perseus/Morpheus, CC BY-SA 4.0

When distributing this dictionary, the Perseus attribution must remain intact:

> Text provided under a CC BY-SA license by Perseus Digital Library, http://www.perseus.tufts.edu, with funding from The National Endowment for the Humanities. Data accessed from https://github.com/PerseusDL/lexica/.

## 🙏 Acknowledgments

* **Charlton T. Lewis & Charles Short**, and the **Perseus Digital Library** for the digitized text.
* **Ludwig Ramshorn & Francis Lieber** for the synonyms handbook; the **Internet Archive** for the scan.
* **Peter Heslin (Diogenes)** for the prebuilt Morpheus analyses.
* **Apple Dictionary Development Kit** for the `.dictionary` format tooling.
