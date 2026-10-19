import os

from flask import Flask
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from services.privacy import configure_logging

configure_logging()

from routes.ask import ask_bp  # noqa: E402
from models.database import get_db  # noqa: E402

app = Flask(__name__)

# Behind the host's proxy, remote_addr is the proxy; trust one X-Forwarded-For
# hop so the per-IP limit applies to visitors. Set TRUSTED_PROXIES=0 when the
# app is reached directly.
_proxies = int(os.environ.get('TRUSTED_PROXIES', '1') or 0)
if _proxies:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_proxies)

is_dev = os.environ.get('FLASK_ENV') == 'development'
if is_dev:
    CORS(app)
else:
    CORS(app, origins=['https://edictai.org', 'https://www.edictai.org'])

app.register_blueprint(ask_bp)


@app.route('/api/health')
def health():
    db = get_db()
    try:
        records = dict(db.execute(
            "SELECT record_type, COUNT(*) FROM records WHERE record_type IN "
            "('legislation', 'document', 'executive-action', 'research') GROUP BY record_type").fetchall())
    finally:
        db.close()
    return {'status': 'ok', 'records': records}


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=is_dev, port=port)
