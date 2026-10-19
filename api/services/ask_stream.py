"""Stream the answer's prose while Claude is still writing it.

Claude's final reply is one JSON object (see ask_agent.ANSWER_SCHEMA). While it
streams, `scan_json_prefix` pulls the string values out of the incomplete JSON,
and `AnswerStreamer` turns the growing records-layer and general-knowledge text
into append-only deltas for the page.

The streamed text is a preview. It only ever shows text that already passes the
grounding checks the final answer goes through (every [[id]] was retrieved for
this question, every bill or order number belongs to a retrieved record, a
conversational reply names none). The moment the text so far would fail, the
preview stops growing, and the final, checked answer replaces it.
"""

import re

GK_PATH = ('general_knowledge', 'text')
TEXT_PATH = ('records_layer', 'text')
KIND_PATH = ('kind',)

_OPEN_MARKER_RE = re.compile(r'\[\[[^\]]*\]?$')
_HEX = frozenset('0123456789abcdefABCDEF')
_ESCAPES = {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}


def _read_string(buf, i):
    """Decode a JSON string body starting at buf[i]; returns (value, next_index, complete)."""
    out = []
    n = len(buf)
    while i < n:
        c = buf[i]
        if c == '"':
            return ''.join(out), i + 1, True
        if c != '\\':
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            break
        e = buf[i + 1]
        if e in _ESCAPES:
            out.append(_ESCAPES[e])
            i += 2
            continue
        if e != 'u':
            # Not valid JSON; keep the character rather than fail the preview.
            out.append(e)
            i += 2
            continue
        h = buf[i + 2:i + 6]
        if len(h) < 4 or not set(h) <= _HEX:
            break
        code = int(h, 16)
        if 0xD800 <= code < 0xDC00:
            # A surrogate pair: wait for the low half before emitting anything.
            low = buf[i + 6:i + 12]
            if len(low) < 6:
                break
            if low[:2] == '\\u' and set(low[2:]) <= _HEX and 0xDC00 <= int(low[2:], 16) < 0xE000:
                code = 0x10000 + ((code - 0xD800) << 10) + (int(low[2:], 16) - 0xDC00)
                out.append(chr(code))
                i += 12
                continue
            i += 6
            continue
        out.append(chr(code))
        i += 6
    return ''.join(out), n, False


def scan_json_prefix(buf):
    """String values found in a (possibly incomplete) JSON object, keyed by path.

    Returns {path: (value, complete)}; the last string may be cut off mid-way.
    Paths are tuples of object keys and array indexes, e.g. ('records_layer', 'text').
    """
    out = {}
    stack = []  # frames: [kind 'o'|'a', key or index]
    expect_key = False
    i, n = 0, len(buf)
    while i < n:
        c = buf[i]
        if c in ' \t\r\n:':
            i += 1
        elif c == ',':
            if stack and stack[-1][0] == 'a':
                stack[-1][1] += 1
            elif stack:
                expect_key = True
            i += 1
        elif c == '{':
            stack.append(['o', None])
            expect_key = True
            i += 1
        elif c == '[':
            stack.append(['a', 0])
            expect_key = False
            i += 1
        elif c in '}]':
            if stack:
                stack.pop()
            expect_key = False
            i += 1
        elif c == '"':
            value, i, complete = _read_string(buf, i + 1)
            if expect_key and stack and stack[-1][0] == 'o':
                if not complete:
                    break
                stack[-1][1] = value
                expect_key = False
            else:
                out[tuple(f[1] for f in stack)] = (value, complete)
                if not complete:
                    break
        else:
            # true, false, null or a number.
            while i < n and buf[i] not in ',}] \t\r\n':
                i += 1
    return out


def _safe_prefix(text, complete):
    """The part of streamed text that is safe to show: whole words, no half-written [[id]]."""
    if not complete:
        cut = max(text.rfind(' '), text.rfind('\n'))
        text = text[:cut] if cut >= 0 else ''
        text = _OPEN_MARKER_RE.sub('', text)
    return text.rstrip(' \t')


class AnswerStreamer:
    """Turns one model response's JSON text into checked, append-only text deltas.

    `check(kind, text)` returns True while `text` passes the grounding checks for
    a reply of that kind; `tag(text)` applies the general-knowledge display
    rules. Both come from ask_agent so the preview and the final answer share
    one set of rules.
    """

    def __init__(self, check, check_gk=None, tag=None, records_for=None, text_path=TEXT_PATH):
        self._text_path = text_path
        self._check = check
        self._check_gk = check_gk
        self._tag = tag
        self._records_for = records_for
        self.reset()

    def reset(self):
        """Forget what was streamed (a fallback model restarted the reply)."""
        self.blocks = {}
        self.sent_text = ''
        self.sent_gk = ''
        self.sent_ids = set()
        self.halted = False
        self.gk_halted = False

    @property
    def sent_any(self):
        return bool(self.sent_text or self.sent_gk)

    def feed(self, index, chunk):
        """Add a text chunk for content block `index`; returns a list of events to send."""
        buf = self.blocks.get(index, '') + chunk
        self.blocks[index] = buf
        if not buf.lstrip().startswith('{'):
            return []
        found = scan_json_prefix(buf)
        kind, kind_done = found.get(KIND_PATH, (None, False))
        if not kind_done or kind not in ('conversation', 'records'):
            return []
        events = []
        if self._text_path in found and not self.halted:
            events += self._text_events(kind, *found[self._text_path])
        if (GK_PATH in found and kind == 'records' and self._check_gk and self.sent_text
                and not self.halted and not self.gk_halted):
            events += self._gk_events(*found[GK_PATH])
        return events

    def _text_events(self, kind, value, complete):
        text = _safe_prefix(value, complete)
        if len(text) <= len(self.sent_text):
            return []
        if not text.startswith(self.sent_text) or not self._check(kind, text):
            self.halted = True
            return []
        delta = text[len(self.sent_text):]
        self.sent_text = text
        records = {}
        if kind == 'records' and self._records_for:
            new_ids = [i for i in re.findall(r'\[\[([^\[\]\s]{1,160})\]\]', text) if i not in self.sent_ids]
            if new_ids:
                self.sent_ids.update(new_ids)
                records = self._records_for(new_ids)
        return [('delta', {'text': delta, 'records': records})]

    def _gk_events(self, value, complete):
        text = _safe_prefix(value, complete)
        if not text.strip():
            return []
        if not self._check_gk(text):
            self.gk_halted = True
            return []
        shown = self._tag(text.strip())
        if len(shown) <= len(self.sent_gk):
            return []
        if not shown.startswith(self.sent_gk):
            self.gk_halted = True
            return []
        delta = shown[len(self.sent_gk):]
        self.sent_gk = shown
        return [('gk_delta', {'text': delta})]
