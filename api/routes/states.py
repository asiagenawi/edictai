from flask import Blueprint
from models.queries import get_all_states, get_state_policies

states_bp = Blueprint('states', __name__)


@states_bp.route('/api/states')
def list_states():
    return get_all_states()


@states_bp.route('/api/states/<code>/policies')
def state_policies(code):
    return get_state_policies(code)
