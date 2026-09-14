"""
Application-wide state: the feature stack and trained model are loaded once at API
startup (not per-request, not retrained per request) and held here for every route to use.
"""

import shap

from app.feature_stack import build_feature_stack
from app.model import load_trained_model

_state = None


class AppState:
    def __init__(self, feature_arrays, meta, model, model_metadata, shap_explainer):
        self.feature_arrays = feature_arrays
        self.meta = meta
        self.model = model
        self.model_metadata = model_metadata
        self.shap_explainer = shap_explainer


def initialize_app_state():
    global _state
    feature_arrays, meta = build_feature_stack(force_rebuild=False)
    model, model_metadata = load_trained_model()
    # Building the explainer inspects the model's tree structure once; reusing it across
    # requests instead of reconstructing it per-request was worth ~1-1.5s per analyze call.
    shap_explainer = shap.TreeExplainer(model)
    _state = AppState(feature_arrays, meta, model, model_metadata, shap_explainer)
    return _state


def get_app_state():
    if _state is None:
        raise RuntimeError("App state not initialized. initialize_app_state() must run at startup.")
    return _state
