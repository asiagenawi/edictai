"""Logging set-up that keeps question text out of every log.

Questions are never passed to a logger by this service. As a second line of
defence, the text of the question being answered is held in a context variable
and scrubbed from any log record emitted while it is set, and the HTTP client
libraries, which can log request bodies at debug level, are held at WARNING.
"""

import contextvars
import logging

_current_question = contextvars.ContextVar('current_question', default=None)

REDACTED = '[redacted]'
QUIET_LOGGERS = ('anthropic', 'httpx', 'httpx2', 'httpcore', 'urllib3')


def set_question(text):
    return _current_question.set(text)


def clear_question(token):
    try:
        _current_question.reset(token)
    except ValueError:
        _current_question.set(None)


class RedactQuestionFilter(logging.Filter):
    def filter(self, record):
        q = _current_question.get()
        if q and len(q) >= 4:
            msg = record.getMessage()
            if q in msg:
                record.msg = msg.replace(q, REDACTED)
                record.args = None
            if record.exc_text and q in record.exc_text:
                record.exc_text = record.exc_text.replace(q, REDACTED)
        return True


_installed = False


def configure_logging(level=logging.INFO):
    global _installed
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    if not _installed:
        flt = RedactQuestionFilter()
        for handler in root.handlers:
            handler.addFilter(flt)
        for name in ('werkzeug', 'gunicorn.error', 'gunicorn.access'):
            for handler in logging.getLogger(name).handlers:
                handler.addFilter(flt)
        _installed = True
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
