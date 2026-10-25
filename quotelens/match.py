"""Map a free-text supplier description to a catalog material (TF-IDF over words and character n-grams)."""
import re

from sklearn.feature_extraction.text import TfidfVectorizer

from .catalog import CATALOG

MM = re.compile(r"(\d{1,2})\s*mm\b")


def clean(text: str) -> str:
    t = text.lower().replace("&", " and ")
    t = re.sub(r"(\d+)\s*x\s*(?=\d)", r"\1x", t)   # "600 x 200" -> "600x200"
    t = re.sub(r"[-_/(),.:;]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class Matcher:
    def __init__(self, catalog=CATALOG, threshold=0.35):
        self.catalog, self.threshold = list(catalog), threshold
        self.docs = [clean(" ".join((m.name,) + m.aliases)) for m in self.catalog]
        self.word = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), token_pattern=r"[a-z0-9]+")
        self.char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
        self.wm = self.word.fit_transform(self.docs)
        self.cm = self.char.fit_transform(self.docs)

    def rank(self, desc: str, k=3):
        q = clean(desc)
        if not q:
            return []
        ws = (self.wm @ self.word.transform([q]).T).toarray().ravel()
        cs = (self.cm @ self.char.transform([q]).T).toarray().ravel()
        nums = set(MM.findall(q))
        scored = []
        for m, w, c in zip(self.catalog, ws, cs):
            s = 0.5 * w + 0.5 * c
            if m.mm and nums and m.mm not in nums:
                s *= 0.4          # 12mm bar must not match a 16mm description
            scored.append((m.id, float(s)))
        return sorted(scored, key=lambda p: -p[1])[:k]

    def match(self, desc: str):
        """Returns (catalog_id or None, score)."""
        top = self.rank(desc, 1)
        if not top or top[0][1] < self.threshold:
            return None, top[0][1] if top else 0.0
        return top[0]
