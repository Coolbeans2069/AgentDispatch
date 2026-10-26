"""the campaign layer: gates, dependencies, governance fields, sealed evidence, playbooks, job templates, tool engineering. Built once per App (see build)."""
import types

from .govern import Governor
from .playbooks import Playbooks
from .seal import Evidence
from .state import CampaignState


def build(app):
    """-> namespace(state, evidence, playbooks, governor). The governor registers itself as a listener on every job state change."""
    from ..agency import policy
    state, ev, pb = CampaignState(app.data), Evidence(app.data), Playbooks(app.data)
    gov = Governor(app.board, app.jobs, app.registry, app.localtools, state, ev, pb, policy.load(app.data)["work_dir"], app.data)
    return types.SimpleNamespace(state=state, evidence=ev, playbooks=pb, governor=gov)
