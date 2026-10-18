from flask import Blueprint, request
from models.queries import get_timeline_data, get_topic_counts, get_status_breakdown, get_level_breakdown

trends_bp = Blueprint('trends', __name__)


@trends_bp.route('/api/trends/timeline')
def timeline():
    state = request.args.get('state')
    topic_id = request.args.get('topic_id', type=int)
    policy_type = request.args.get('policy_type')
    return get_timeline_data(state_code=state, topic_id=topic_id, policy_type=policy_type)


@trends_bp.route('/api/trends/topics')
def topics():
    state = request.args.get('state')
    policy_type = request.args.get('policy_type')
    return get_topic_counts(state_code=state, policy_type=policy_type)


@trends_bp.route('/api/trends/status')
def status():
    return get_status_breakdown()


@trends_bp.route('/api/trends/level')
def level():
    return get_level_breakdown()
