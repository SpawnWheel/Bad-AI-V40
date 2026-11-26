"""Softens shouted ALL-CAPS words before they reach the TTS model.

The scripts shout in capitals ("OH! Bill's tagged Payne—BOTH IN THE GRAVEL!", "Do NOT box me!"), and the model
stumbles over them: a short capitalised word such as "OH" or "NO" can come out spelled as letters. Shouted words go
back to normal case, and the exclamation marks keep the energy. Real acronyms and names written in capitals
(BMW, MTG, ATI Boy, STW) are left alone.

A capitalised word is a shouted word when:
* the same word is written in normal case somewhere else in the script ("Oh", "through", "Payne's"), or
* it is a common short exclamation or word ("OH", "NO", "YES"), or
* it has 4 or more letters and a vowel ("DISQUALIFIED", "HAHAHA"); short or vowel-less ones look like acronyms.
"""
import re
from collections import Counter

# A word with at least two capital letters and no lower-case ones (apostrophes allowed: HE'S, I'M, PAYNE'S)
CAPS_WORD_RE = re.compile(r"(?<![\w'’])([A-Z][A-Z'’]*[A-Z])(?![\w'’])")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’]*")

# Short words that are never acronyms in a race script, whether or not the script also writes them in normal case
SHOUT_WORDS = {
    "OH", "OOH", "AH", "AHH", "NO", "NOO", "YES", "YEAH", "YEP", "WOW", "WHOA", "GO", "OI", "OK", "OKAY", "HEY",
    "UGH", "ARGH", "AGH", "DAMN", "HA", "HAH", "WHAT", "WHY", "HOW", "WHO", "NOW", "NOT", "AND", "BUT", "THE",
    "HE", "HE'S", "SHE", "IT", "IT'S", "IS", "WAS", "ARE", "YOU", "ME", "MY", "WE", "US", "ON", "IN", "AT", "UP",
    "OFF", "OUT", "BOX", "TWO", "ONE", "SIX", "TEN", "BIG", "GET", "GOT", "HIT", "WIN", "WON", "DID", "CAN", "DO",
    "SO", "TO", "OF", "OR", "BY", "ALL", "OUR", "HIS", "HER", "I'M", "I'LL", "I'VE", "I'D", "DON'T", "CAN'T",
}

VOWELS = set("AEIOU")


def _key(word):
    return word.upper().replace("’", "'")


def seen_forms(texts):
    """{WORD: the way the script writes it in normal case} from every line of a script.

    Lower case wins when the word is ever written that way ("through", "oh"), so a capital at the start of a
    sentence doesn't stick; otherwise the most common form is kept, which keeps names capitalised ("Payne's").
    """
    forms = {}
    for text in texts:
        for word in WORD_RE.findall(text):
            if word.upper() != word:
                forms.setdefault(_key(word), Counter())[word] += 1
    result = {}
    for key, counts in forms.items():
        lower = [w for w in counts if w == w.lower()]
        result[key] = lower[0] if lower else counts.most_common(1)[0][0]
    return result


def _is_shouted(word, forms):
    key = _key(word)
    if key in forms or key in SHOUT_WORDS:
        return True
    letters = [c for c in key if c.isalpha()]
    return len(letters) >= 4 and any(c in VOWELS for c in letters)


def _sentence_start(text, start):
    before = text[:start].rstrip(" \t\"'“”‘’([")
    return not before or before[-1] in ".!?…"


def soften_caps(text, forms=None):
    """The text with its shouted ALL-CAPS words in normal case (see the module notes)."""
    forms = forms or {}

    def fix(m):
        word = m.group(1)
        if not _is_shouted(word, forms):
            return word
        out = forms.get(_key(word))
        if out is None:
            out = word.lower()
            # "I'M" -> "I'm", never "i'm"
            if re.match(r"i['’]", out):
                out = "I" + out[1:]
        else:
            # Keep the script's apostrophe style for the word in front of us
            out = "".join(o if o.isalpha() else w for o, w in zip(out, word)) if len(out) == len(word) else out
        if _sentence_start(m.string, m.start()):
            out = out[0].upper() + out[1:]
        return out

    return CAPS_WORD_RE.sub(fix, text)
