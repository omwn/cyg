# cyg

A small, dependency-free Python library to query the [Cygnet](https://github.com/omwn/cygnet) multilingual wordnet database.

Cygnet is a merged multilingual wordnet covering 40+ languages and ~180,000 concepts. It combines Open English WordNet, OMW Data, TUFS, OdeNet, DanNet, and several other wordnets into a single SQLite database, indexed by the Collaborative Interlingual Index (CILI). 

## Installation

```bash
pip install cyg
```

Requires Python 3.9+.

## Quick Start

```python
from cyg import Cygnet

cyg = Cygnet() 

```
Note : Cygnet() automatically installs the the latest version of the database the first time you use it. 


## Use Cases

### 1/ Working with concepts

**Counting all concepts**

```python
c = cyg.concepts()
print(len(c))
```

**Filtering concepts by POS**

```python
c = cyg.concepts(pos="noun")
```

**Filtering concepts by language(s)**

```python
c = cyg.concepts(langs="it")
```

**Filtering concepts by form (exact match)**

```python
c = cyg.concepts(form="bal")
```

**Filtering concepts by form (partial match)**

```python
c = cyg.concepts(contains="bal")
c = cyg.concepts(startswith="ball")
```


**Filtering concepts by POS and language(s)**
```python
c = cyg.concepts(pos="adv", langs="it")
```

**Filtering concepts by form and language(s)**
```python
c = cyg.concepts(form="bal", langs="nl")
```

**Filtering concepts by form, POS and language(s)**

```python
c = cyg.concepts(form="bal", pos="noun", langs="fr")
```


**Displaying the POS of a specific concept**

```python
c = cyg.concepts(form="bal")
for a in c:
    print(a.pos())
```

**Displaying the index of a specific concept**

```python
c = cyg.concepts(form="bal")
for a in c:
    print(a.index())
```

**Displaying available definitions of a concept**

```python
c = cyg.concepts(form="bal")
for item in c:
    f = item.definition("en")
    print(f.text())
```


**Finding specific related concepts**

```python
c = cyg.concepts(form="cheek", langs="en")
for a in c:
    rel = a.hypernyms()
    print(rel)
```

Available relationships:
- hypernymy: `a.hypernyms()`
- hyponymy: `a.hyponyms()`
- meronymy: `a.meronyms()`
- holonymy: `a.holonyms()`


**Finding all related concepts**
```python
c = cyg.concepts(form="leave", langs="en")
for a in c:
    all_related = a.get_related()
    print(all_related)
```

One or more relationships can also be specified to restrict the results.


```python
c = cyg.concepts(form="book", langs="en")
for a in c:     
     g= a.get_related(['mero_member','mero_part'])
     print (g)
```


**Retrieving senses from concepts**

```python
c = cyg.concepts(form="cheek", langs="en")
for a in c:
    senses = a.senses("en")
    print(a, senses)
```

**Retrieving lexemes from concepts**

```python
c = cyg.concepts(form="repair", langs="en")
for a in c:
    words = a.lexemes("en")
    print(a, words)
```

---

### 2/ Working with lexemes

**Counting all lexemes**

```python
c = cyg.lexemes()
print(len(c))
```

**Filtering lexemes by form (exact match)**

```python
c = cyg.lexemes(form="but")
print(len(c))
```

**Filtering lexemes by form (partial match)**

```python
c = cyg.lexemes(contains="but")
d = cyg.lexemes(startswith="but")
print(len(c), len(d))
```

**Filtering lexemes by language(s)**

```python
c = cyg.lexemes(langs=["en", "fr"])
print(len(c))
```

**Filtering lexemes by form and language(s)**

```python
c = cyg.lexemes(form="but",langs=["en", "fr"])
print(c)
```

**Finding the index of a lexeme**

```python
c = cyg.lexemes(form="cheek", langs="en")
for w in c:
    print (w.index())
```

**Finding the language of a lexeme**

```python
c = cyg.lexemes(form="sudden")
for w in c:
    print (w.lang())
```

**Displaying the lemma of a lexeme**

```python
c = cyg.lexemes(form="been")
for w in c:
    print(w.lemma())
```

**Finding all forms of a lexeme**

```python
c = cyg.lexemes(form="been")
for w in c:
    print(w.all_forms())
```

**Retrieving the concepts related to a lexeme**

```python
c = cyg.lexemes(form="pray")
for w in c:
    print(w.concepts())
```

**Retrieving the senses related to a lexeme**

```python
c = cyg.lexemes(form="pray")
for w in c:
    print(w.senses())
```

---

### 3/ Working with senses

**Counting all senses**
```python
c = cyg.senses()
print(len(c))
```

**Filtering senses by form (exact match)**

```python
c = cyg.senses(form="chaos")
print(c)
```

**Filtering senses by form (partial match)**

```python
c = cyg.senses(contains="chaos")
d = cyg.senses(startswith="chaos")
print(c, d)
```


**Filter senses by language(s)**

```python
c = cyg.senses(langs=["en", "fr"])
print(len(c))
```

**Filter senses by form and language(s)**

```python
c = cyg.senses(form="chaos", langs=["en", "fr"])
print(c)
```


**Displaying the index of a sense**

```python
c = cyg.senses(form="chaos", langs=["en", "fr"])
for b in c:
    print(b, b.index())
```

**Displaying available examples and offsets of a sense**

```python
c = cyg.senses(form="plain")

for sense in c:
    for example in sense.examples():
        print(example.text(), example.sense_offsets())
```

**Retrieving the concept related to a sense**

```python
c = cyg.senses(form="chaos", langs=["en"])
for b in c:
    print(b, b.concept())
```

**Retrieving the lexeme related to a sense**

```python
c = cyg.senses(form="chaos")
for b in c:
    print(b, b.lexeme())
```

### 4/ Exploring languages

**Displaying all languages included in the database**

```python
c = cyg.langs()

print (c)
```

---

## Configuration

The database is resolved in this order:

1. Explicit `db_path` argument to `Cygnet(db_path="/path/to/cygnet.db")`
2. `CYG_DB` environment variable
3. Platform cache directory:
   - Linux: `~/.cache/cyg/cygnet.db`
   - macOS: `~/Library/Caches/cyg/cygnet.db`
   - Windows: `%LOCALAPPDATA%\cyg\cygnet.db`

The database (~160 MB compressed) is downloaded automatically on first use from the [latest GitHub release](https://github.com/omwn/cygnet/releases/latest/download/cygnet.db.gz).

To disable auto-download and require an existing file:
```python
cyg = Cygnet(download=False)  # Raises DatabaseNotFoundError if missing
```

## Version Management

Cygnet can install and manage multiple releases side by side. By default, all downloads are stored in a versioned directory layout (`<cache>/cyg/<tag>/cygnet.db`), so switching between releases never re-downloads the same data.

**Installing a specific version:**

```python
from cyg import Cygnet

cyg = Cygnet(version="2026.05.12")
print(cyg.current_version())  # "2026.05.12"
```

**Listing available releases:**

```python
from cyg import Cygnet

cyg = Cygnet(version="2026.05.12")
for tag in cyg.releases():
    print(tag)  # "2026.05.12", "2026.03.01", ...
```

**Upgrading:**

```python
# Upgrade to latest
cyg.upgrade()

# Upgrade to a specific version
cyg.upgrade(version="2026.05.12")
```

When `version=` is passed to `Cygnet()`, the database is stored in a versioned subdirectory (`<cache>/cyg/<tag>/cygnet.db`) so that multiple releases coexist. When `upgrade()` is called, the instance is repointed to the new versioned directory; the old version remains on disk and can be reused by constructing `Cygnet(version="old_version")`.

If no `version=` is passed and no database exists yet, `upgrade()` installs the latest release.


### Cygnet

Main entry point for querying the database.

```python
cyg = Cygnet(db_path: str | None = None, *, download: bool = True, version: str | None = None)
```

| Method | Description |
|--------|-------------|
| `concepts(form, langs, pos, startswith, contains)` | Find concepts by word form, language, POS, prefix, or substring |
| `concept(ili)` | Get a single concept by its ILI identifier (e.g., "i46360") |
| `senses(form, langs, startswith, contains)` | Find senses by word form, language, prefix, or substring |
| `lexemes(form, langs, startswith, contains)` | Find lexemes by word form, language, prefix, or substring |
| `langs()` | Return sorted list of all language codes in the database |
| `releases()` | List all available release tags (newest first) |
| `latest_version()` | Get the latest release tag from GitHub |
| `current_version()` | Get the release tag of the installed database |
| `upgrade(version=None, timeout=300)` | Upgrade to a specific version or the latest |

**Filter parameters:**
- `form`: exact match
- `langs`: Single language code (e.g., "en") or list (e.g., ["en", "fr"])
- `pos`: Part of speech ("noun", "verb", "adj", "adv", "adp", "unk", "conj", "nref")
- `startswith`: Prefix match on normalized form
- `contains`: Substring match on normalized form

All query methods return lists of model objects (never `None` for collections).

### Concept

A language-independent concept (synset).

| Method | Returns | Description |
|--------|---------|-------------|
| `index()` | `str` | ILI identifier (e.g., "i46360") |
| `pos()` | `POS` | Part of speech |
| `definition(lang="en")` | `AnnotatedString \| None` | Definition in given language |
| `senses(lang=None)` | `list[Sense]` | All senses, optionally filtered by language |
| `lexemes(lang=None)` | `list[Lexeme]` | All lexemes, optionally filtered by language |
| `hypernyms()` | `list[Concept]` | More general concepts |
| `hyponyms()` | `list[Concept]` | More specific concepts |
| `meronyms()` | `list[Concept]` | Part-of relations |
| `holonyms()` | `list[Concept]` | Whole-of relations |

### Sense

A pairing of a lexeme with a concept (one meaning of a word).

| Method | Returns | Description |
|--------|---------|-------------|
| `index()` | `str` | Internal sense ID |
| `lang()` | `str` | Language code of the lexeme |
| `examples()` | `list[AnnotatedString]` | Usage examples with annotations |
| `concept()` | `Concept` | The concept (meaning) |
| `lexeme()` | `Lexeme` | The lexeme (word form) |

### Lexeme

A word in a specific language with all its inflected forms.

| Method | Returns | Description |
|--------|---------|-------------|
| `index()` | `str` | Internal lexeme ID |
| `lang()` | `str` | Language code |
| `lemma()` | `str` | Canonical dictionary form |
| `all_forms()` | `list[str]` | All inflected forms including lemma |
| `senses()` | `list[Sense]` | All senses for this lexeme |
| `concepts()` | `list[Concept]` | All concepts linked via senses |

### AnnotatedString

A text string (definition or example) with optional sense annotations.

| Method | Returns | Description |
|--------|---------|-------------|
| `text()` | `str` | Plain text content |
| `lang()` | `str` | Language code |
| `sense_offsets()` | `list[(Sense, int, int)]` | Annotated spans as (sense, start, end) |

---

## License

MIT License - see [LICENSE](LICENSE) for details.

## Upstream

- Online repository: https://github.com/omwn/cyg
- Cygnet database: https://github.com/omwn/cygnet
- Online interface: https://cygnet.maudslay.eu/

## Citation

Maudslay, R. H., & Bond, F. (2026). Cygnet: Refactoring the Open Multilingual Wordnet. In Proceedings of the Fifteenth Language Resources and Evaluation Conference (LREC 2026) (pp. 7905–7917). European Language Resources Association (ELRA). https://doi.org/10.63317/2v96snyewr2h.
