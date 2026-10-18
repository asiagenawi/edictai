from flask import Blueprint, request
from models.queries import get_all_policies, get_policy_by_id, get_all_topics

policies_bp = Blueprint('policies', __name__)


@policies_bp.route('/api/policies')
def list_policies():
    level = request.args.get('level')
    status = request.args.get('status')
    topic_id = request.args.get('topic_id', type=int)
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    return get_all_policies(level=level, status=status, topic_id=topic_id, limit=limit, offset=offset)


@policies_bp.route('/api/policies/<int:policy_id>')
def policy_detail(policy_id):
    policy = get_policy_by_id(policy_id)
    if not policy:
        return {'error': 'Policy not found'}, 404
    return policy


@policies_bp.route('/api/topics')
def list_topics():
    return get_all_topics()
