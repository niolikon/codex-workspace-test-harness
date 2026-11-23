"""Independent P7.2 gold and HTML reader. No Product or Harness imports."""
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import json
import stat

TITLES = {"complete": "Review completa", "incomplete": "Review diagnostica — input incompleti",
          "rejected": "Review diagnostica — input rifiutati"}
LABELS = {"metadata": "Dati della coppia", "input-digests": "Digest degli ingressi",
          "scope": "Scope", "provenance": "Provenienza attribuita al produttore",
          "sources": "Fonti verificate", "requirements": "Requisiti", "guidelines": "Guideline",
          "technical": "Tecnica", "coverage": "Collegamenti di copertura e lacune", "limitations": "Limiti",
          "citations": "Citazioni verificate", "validation": "Controlli del validatore"}
DIMENSIONS = ("requirements", "guidelines", "technical")

def digest(data):
    return sha256(data).hexdigest()

def require(holds, detail):
    if not holds:
        raise AssertionError(detail)

def pairs(pairs_list):
    result = dict(pairs_list)
    require(len(pairs_list) == len(result), "duplicate JSON key")
    return result

def decode(raw):
    require(raw.endswith(b"\n") and 0 < len(raw) <= 1048576, "invalid view byte framing")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)

def ordinal(text):
    # .NET ordinal compares UTF-16 units. All IDs are ASCII but reasons can contain astral characters.
    return text.encode("utf-16-be")

def expected(raw):
    review = decode(raw)
    leaves, articles, titles, links, sections, lists, terms = [], [], [], [], [], [], []
    def leaf(value, addr):
        kind = type(value)
        require(kind in (str, int, type(None)), "unexpected scalar")
        content = str(value) if value is not None else "null"
        target = None
        if "/citation_ids/" in addr:
            target = "#citation-" + content
        if addr.startswith("/citations/") and addr.endswith("/source_id"):
            target = "#source-" + content
        if addr.endswith("/result_id") and value is not None:
            target = "#result-" + content
        leaves.append([addr, content, value is None])
        if target is not None:
            links.append([addr, target])

    def mapping(obj, addr):
        for name, value in sorted(obj.items()):
            terms.append(name)
            walk(value, f"{addr}/{name}")

    def walk(obj, addr):
        if isinstance(obj, dict):
            mapping(obj, addr)
        elif isinstance(obj, list):
            lists.append(addr)
            if all(isinstance(x, str) for x in obj):
                ordered = sorted(obj, key=ordinal)
            else:
                ordered = sorted(obj, key=lambda value: (
                    ordinal(value["code"]), ordinal(value["reference"]), ordinal(value["reason"]),
                    sorted(value["criterion_ids"], key=ordinal)))
            for index, item in enumerate(ordered):
                walk(item, f"{addr}/{index}")
        else:
            leaf(obj, addr)

    def section(name):
        sections.append("section-" + name)
        titles.append(["h2", LABELS[name]])

    def rows(values, field, prefix, addr):
        for row in sorted(values, key=lambda row: row[field]):
            identity = row[field]
            articles.append([sections[-1], prefix + "-" + identity, identity])
            titles.append(["h3", identity])
            mapping(row, f"{addr}/{identity}")

    section("metadata")
    header = {key: review[key] for key in ("schema_version", "state", "review_id") if key in review}
    header.update(renderer_version="1.0.0", review_sha256=digest(raw))
    mapping(header, "")
    if "input_digests" in review:
        section("input-digests")
        mapping(review["input_digests"], "/input_digests")
    if "scope" in review:
        for name in ("scope", "provenance"):
            section(name)
            mapping(review[name], "/" + name)
        section("sources")
        rows(review["sources"], "id", "source", "/sources")
        for group in DIMENSIONS:
            section(group)
            rows(review["results"][group], "id", "result", "/results/" + group)
        section("coverage")
        rows(review["results"]["coverage"], "criterion_id", "coverage", "/results/coverage")
        section("limitations")
        walk(review["limitations"], "/limitations")
        section("citations")
        sources = {row["id"]: row for row in review["sources"]}
        quotes = [dict(row, source_revision=sources[row["source_id"]]["revision"])
                  for row in review["citations"]]
        rows(quotes, "id", "citation", "/citations")
    section("validation")
    terms.append("validator_version")
    leaf(review["validation"]["validator_version"], "/validation/validator_version")
    rows(review["validation"]["checks"], "id", "check", "/validation/checks")
    heading = TITLES[review["state"]]
    return {"leaves": leaves, "articles": articles,
            "titles": [["title", heading], ["h1", heading], *titles], "links": links,
            "sections": sections, "lists": lists, "terms": terms,
            "notice": review["state"] == "incomplete" and "scope" in review}

class Markup(HTMLParser):
    TAGS = set("html head meta title style body main h1 h2 h3 section article dl dt dd span a ol li p".split())
    PARENTS = {"html": {None}, "head": {"html"}, "meta": {"head"}, "title": {"head"},
               "style": {"head"}, "body": {"html"}, "main": {"body"}, "h1": {"main"},
               "section": {"main"}, "h2": {"section"}, "article": {"section"}, "h3": {"article"},
               "dl": {"section", "article", "dd", "li"}, "dt": {"dl"}, "dd": {"dl"},
               "span": {"dd", "li"}, "a": {"dd", "li"}, "p": {"main"}, "ol": {"dd", "section"},
               "li": {"ol"}}
    REQUIRED = {"html": {"lang"}, "meta": {"charset"}, "section": {"id"},
                "article": {"id", "data-row"}, "span": {"data-path"}, "a": {"data-path", "href"},
                "ol": {"data-path"}, "p": {"class"}}
    TEXT = {"title", "style", "h1", "h2", "h3", "dt", "span", "a", "p"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.ids = set()
        self.declarations = []
        self.tags = []
        self.current_section = None
        self.result = {k: [] for k in ("leaves", "articles", "titles", "links", "sections", "lists", "terms")}
        self.result["notice"] = False
        self.css = None

    def handle_decl(self, decl):
        require(not self.tags, "doctype placement")
        self.declarations.append(decl)

    def handle_startendtag(self, tag, attrs):
        raise AssertionError("unexpected self-closing tag")

    def handle_comment(self, data):
        raise AssertionError("unexpected comment")

    def handle_pi(self, data):
        raise AssertionError("unexpected processing instruction")

    def handle_starttag(self, tag, attrs):
        require(tag in self.TAGS, f"unexpected element {tag}")
        parent = self.stack[-1][0] if self.stack else None
        require(parent in self.PARENTS[tag], f"misnested {tag}")
        properties = dict(attrs)
        required = self.REQUIRED.get(tag, set())
        permitted = required | ({"data-null"} if tag == "span" else set())
        require(len(attrs) == len(properties) and required <= properties.keys() <= permitted, f"attributes on {tag}")
        if "data-null" in properties:
            require(properties["data-null"] == "true", "null marker")
        if "id" in properties:
            require(properties["id"] not in self.ids, "duplicate anchor")
            self.ids.add(properties["id"])
        self.tags.append([tag, sorted(properties)])
        if tag == "html":
            require(properties == {"lang": "it"}, "document language")
        if tag == "meta":
            require(properties == {"charset": "utf-8"}, "charset")
            return
        if tag == "section":
            self.current_section = properties["id"]
            self.result["sections"].append(self.current_section)
        elif tag == "article":
            self.result["articles"].append([self.current_section, properties["id"], properties["data-row"]])
        elif tag == "ol":
            self.result["lists"].append(properties["data-path"])
        elif tag == "p":
            require(properties == {"class": "notice"} and not self.result["notice"], "notice placement")
            self.result["notice"] = True
        self.stack.append((tag, properties, []))

    def handle_data(self, text):
        if self.stack and self.stack[-1][0] in self.TEXT:
            self.stack[-1][2].append(text)
        else:
            require(not text.strip(), "unmarked visible text")

    def handle_endtag(self, tag):
        require(self.stack and self.stack[-1][0] == tag, "unbalanced HTML")
        _, props, parts = self.stack.pop()
        value = "".join(parts)
        if tag in ("span", "a"):
            self.result["leaves"].append([props["data-path"], value, "data-null" in props])
        if tag == "a":
            self.result["links"].append([props["data-path"], props["href"]])
        if tag in ("h1", "h2", "h3", "title"):
            self.result["titles"].append([tag, value])
        if tag == "dt":
            self.result["terms"].append(value)
        if tag == "p":
            require("input mancanti" in value and "unverified" in value, "missing explanation")
        if tag == "style":
            require(self.css is None and "url(" not in value.lower() and "@import" not in value.lower(), "CSS resources")
            self.css = value

    def complete(self):
        require(not self.stack and self.declarations == ["doctype html"] and self.css is not None, "document envelope")
        tags = [tag for tag, _ in self.tags]
        for singleton in ("html", "head", "meta", "title", "style", "body", "main", "h1"):
            require(tags.count(singleton) == 1, f"cardinality of {singleton}")
        require(all(target.startswith("#") and target[1:] in self.ids
                    for _, target in self.result["links"]), "unresolved or external anchor")

def extract(html):
    require(html.startswith(b"<!doctype html>\n") and html.endswith(b"\n") and b"\r" not in html,
            "HTML UTF-8 and LF framing")
    parser = Markup()
    parser.feed(html.decode("utf-8"))
    parser.close()
    parser.complete()
    return parser.result, parser.tags

def pair(path, raw, gold, detached=False):
    items = {entry.name: entry for entry in path.iterdir()}
    require(set(items) == {"review.json", "report.html"}, "physical pair inventory")
    require(not path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse directory")
    if not detached:
        import re
        require(re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}--\d{8}T\d{13}Z", path.name),
                "not a final publication directory")
        require(path.parent.name == "reviews" and path.parent.parent.name == "session_data", "publish topology")
        require(str(path) == str(path.resolve(strict=True)), "directory spelling")
    for item in items.values():
        info = item.lstat()
        require(stat.S_ISREG(info.st_mode) and not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                and info.st_nlink == 1, "nonregular file")
    content = items["review.json"].read_bytes()
    html = items["report.html"].read_bytes()
    require(len(content) + len(html) <= 2097152, "pair over budget")
    require(content == raw and decode(content) == decode(raw), "review bytes changed")
    projection, tags = extract(html)
    require(projection == gold, "DOM projection does not match independent gold")
    require(["/review_sha256", digest(content), False] in projection["leaves"], "digest mismatch")
    return html, projection, tags
