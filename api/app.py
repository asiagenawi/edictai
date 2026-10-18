import os

from flask import Flask
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from services.privacy import configure_logging

configure_logging()

from routes.states import states_bp  # noqa: E402
from routes.policies import policies_bp  # noqa: E402
from routes.trends import trends_bp  # noqa: E402
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
    CORS(app, origins=['https://asiagenawi.github.io'])

app.register_blueprint(states_bp)
app.register_blueprint(policies_bp)
app.register_blueprint(trends_bp)
app.register_blueprint(ask_bp)


@app.route('/api/health')
def health():
    db = get_db()
    count = db.execute('SELECT COUNT(*) FROM v_policies_legacy').fetchone()[0]
    db.close()
    return {'status': 'ok', 'policy_count': count}


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=is_dev, port=port)
