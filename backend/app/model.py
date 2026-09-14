"""
Native-10m Heat Vulnerability Index construction and CatBoost training.

Training strategy (explicit, per project decision): train on every valid pixel in the
full AOI (~5.5 million), not a subsample -- but do it in sequential batches using
CatBoost's init_model continuation, and cap the thread count below the machine's full
core count, so the machine isn't pinned at 100% for the whole run ("reduce processing
heat" -- a deliberate, disclosed trade-off, not a shortcut on the final model).
"""

import os
import time
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_absolute_error
from catboost import CatBoostRegressor, Pool

from app.feature_stack import FEATURE_NAMES, build_feature_stack

# Where the trained model is saved/loaded. backend/app/state.py reads from here at
# startup -- retraining (train_native_10m_model, at the bottom of this file) overwrites
# both files, and the running server must be restarted to pick up a newly trained model
# (it's only loaded once, not watched for changes).
MODELS_DIR = "D:\\Projects\\UC\\backend\\models"
MODEL_PATH = os.path.join(MODELS_DIR, "heat_vulnerability_native10m.cbm")
MODEL_METADATA_PATH = os.path.join(MODELS_DIR, "heat_vulnerability_native10m_metadata.npz")

# If a group's mean pairwise |correlation| exceeds this, the whole group shares a combined
# 0.5 weight instead of each member counting in full (see compute_correlations_and_weights
# below). Lowering this makes down-weighting trigger more easily (more groups get
# treated as redundant); raising it toward 1.0 makes every feature count close to fully
# regardless of correlation. This directly changes the label every pixel is trained
# against, so changing it requires retraining (train_native_10m_model) -- it does nothing
# to an already-trained model.
CORRELATION_DOWNWEIGHT_THRESHOLD = 0.5
# Which of the 14 features count as "structural" (urban form) vs "cooling" (vegetation/
# water/reflectivity) for that correlation check. Adding a feature here without adding it
# to feature_stack.py's FEATURE_NAMES first will raise a KeyError; adding one that exists
# but was meant to stay neutral will pull it into the weighted label unintentionally.
STRUCTURAL_COLUMNS = ["built_up_pct_mean", "building_density_per_km2", "road_density_km_per_km2"]
COOLING_COLUMNS = ["ndvi_mean", "ndwi_mean", "albedo_mean"]

# How many rows CatBoost sees per training batch (see train_catboost_in_batches). Raising
# this uses more RAM/CPU per batch but fewer batches overall; lowering it is gentler on
# the machine but takes more wall-clock time for the same total data. Does not change the
# final model's accuracy in any meaningful way -- it's a resource/time tradeoff, not a
# modeling choice.
TRAINING_BATCH_SIZE = 500000
# Deliberately less than the machine's full core count so training doesn't pin the CPU at
# 100% for the whole run. Raising this speeds up training at the cost of leaving less CPU
# for anything else running at the same time.
CATBOOST_THREAD_COUNT = 6
# Real CatBoost hyperparameters -- these DO change model accuracy and must be re-evaluated
# (check the held-out R²/MAE this function prints) if changed, not assumed safe:
#   iterations: more trees per batch = more capacity, slower training, risk of overfitting
#   learning_rate: smaller = more stable but needs more iterations to converge
#   depth: deeper trees = more capacity but slower and more prone to overfitting
CATBOOST_ITERATIONS_PER_BATCH = 150
CATBOOST_LEARNING_RATE = 0.05
CATBOOST_DEPTH = 6

# Fraction of pixels held out for the reported R²/MAE, and the seed that makes the
# train/test split (and every other random draw in this file) reproducible. Changing
# RANDOM_STATE gives a different-but-equally-valid split; changing TEST_SET_FRACTION
# trades off how many pixels train the model vs. how confidently accuracy is measured.
TEST_SET_FRACTION = 0.2
RANDOM_STATE = 42


def log(msg):
    print(f"[model] {msg}", flush=True)


def normalize_0_1(array_values):
    """Rescale an array to the [0, 1] range so features with different units (a percent,
    a count, an index) can be combined with simple weights below."""
    lo = np.nanmin(array_values)
    hi = np.nanmax(array_values)
    if hi - lo < 1e-9:
        return np.zeros_like(array_values)
    return (array_values - lo) / (hi - lo)


def flatten_feature_stack_to_dataframe(feature_arrays):
    """Turn the 2D per-pixel feature rasters into one row-per-pixel table, dropping
    pixels with no real data and filling any remaining gaps with the column median."""
    log("Flattening the native-10m feature stack to a per-pixel table ...")
    flat = {name: feature_arrays[name].ravel() for name in FEATURE_NAMES}
    df = pd.DataFrame(flat)

    valid_mask = df[["ndvi_mean", "built_up_pct_mean", "albedo_mean"]].notna().all(axis=1)
    log(f"Valid pixels (real NDVI and LULC data present): {int(valid_mask.sum())} of {len(df)} total ({valid_mask.sum()/len(df)*100:.1f}%)")

    df = df.loc[valid_mask].reset_index(drop=True)
    for col in FEATURE_NAMES:
        df[col] = df[col].fillna(df[col].median())

    return df


def mean_pairwise_abs_correlation(corr_matrix, columns):
    """Average |correlation| between every pair of columns in this group. Used to check
    whether a group of features (e.g. built-up %, building density, road density) is
    telling the model the same thing multiple times, which would overweight it."""
    n = len(columns)
    sub = corr_matrix.loc[columns, columns]
    total_abs_off_diagonal = np.abs(sub.values).sum() - n
    return total_abs_off_diagonal / (n * (n - 1))


def compute_correlations_and_weights(df):
    """Decide how much each feature counts toward the heat vulnerability index. Features
    in the same correlated group (structural: built-up/building/road density, or cooling:
    NDVI/NDWI/albedo) share a combined 0.5 weight if they're redundant with each other
    (mean pairwise |correlation| > 0.5), instead of each counting in full."""
    log("Computing the fresh correlation matrix at native 10m pixel resolution (not assumed from any zonal run) ...")
    corr_cols = STRUCTURAL_COLUMNS + COOLING_COLUMNS
    corr_matrix = df[corr_cols].corr()
    log("Real native-10m correlation matrix:")
    log("\n" + corr_matrix.round(3).to_string())

    mean_struct_corr = mean_pairwise_abs_correlation(corr_matrix, STRUCTURAL_COLUMNS)
    mean_cooling_corr = mean_pairwise_abs_correlation(corr_matrix, COOLING_COLUMNS)

    log(f"Mean pairwise |correlation| among structural variables at native 10m: {mean_struct_corr:.3f} (threshold {CORRELATION_DOWNWEIGHT_THRESHOLD})")
    log(f"Mean pairwise |correlation| among cooling variables (NDVI, NDWI, albedo) at native 10m: {mean_cooling_corr:.3f}")

    n_struct = len(STRUCTURAL_COLUMNS)
    n_cooling = len(COOLING_COLUMNS)
    struct_weight_each = (0.5 / n_struct) if mean_struct_corr > CORRELATION_DOWNWEIGHT_THRESHOLD else (1.0 / n_struct)
    cooling_weight_each = (0.5 / n_cooling) if mean_cooling_corr > CORRELATION_DOWNWEIGHT_THRESHOLD else (1.0 / n_cooling)

    if mean_struct_corr > CORRELATION_DOWNWEIGHT_THRESHOLD:
        log(f"Structural variables are correlated -> down-weighted to a combined 0.5 (each gets {struct_weight_each:.3f})")
    else:
        log(f"Structural variables are not strongly correlated -> each gets close to full weight ({struct_weight_each:.3f})")

    if mean_cooling_corr > CORRELATION_DOWNWEIGHT_THRESHOLD:
        log(f"Cooling variables (NDVI/NDWI/albedo) are correlated -> down-weighted to a combined 0.5 (each gets {cooling_weight_each:.3f})")
    else:
        log(f"Cooling variables are not strongly correlated -> each gets close to full weight ({cooling_weight_each:.3f})")

    weights = {
        "built_up_pct_mean": struct_weight_each,
        "building_density_per_km2": struct_weight_each,
        "road_density_km_per_km2": struct_weight_each,
        "ndvi_mean": -cooling_weight_each,
        "ndwi_mean": -cooling_weight_each,
        "albedo_mean": -cooling_weight_each,
    }
    log(f"Final weights at native 10m: {weights}")

    return weights, corr_matrix, mean_struct_corr, mean_cooling_corr


def build_heat_vulnerability_index(df, weights):
    """The label CatBoost is trained to predict: a weighted sum of normalized features,
    rescaled to 0-100. This is a deterministic formula, not a measurement -- see the
    self_consistency_caveat saved with the model metadata below."""
    normalized = pd.DataFrame({col: normalize_0_1(df[col].values) for col in weights.keys()})
    raw_index = sum(normalized[col] * weight for col, weight in weights.items())
    raw_index = raw_index - raw_index.min()
    raw_index = raw_index / raw_index.max() * 100.0
    return raw_index


def train_catboost_in_batches(X_train, y_train):
    """Train on every pixel, not a subsample, but split into batches and continue each
    new CatBoost model from the previous one (init_model) so the whole ~5.5M-row training
    set is never fit in a single call and the machine's CPU isn't pinned for the whole run."""
    log(f"Training CatBoost on {len(X_train)} real pixels in batches of {TRAINING_BATCH_SIZE} (thread_count={CATBOOST_THREAD_COUNT} of the machine's available cores, to avoid pinning the CPU) ...")

    n_batches = int(np.ceil(len(X_train) / TRAINING_BATCH_SIZE))
    model = None
    start_time = time.time()

    for batch_index in range(n_batches):
        batch_start = batch_index * TRAINING_BATCH_SIZE
        batch_end = min(batch_start + TRAINING_BATCH_SIZE, len(X_train))
        X_batch = X_train.iloc[batch_start:batch_end]
        y_batch = y_train.iloc[batch_start:batch_end]

        new_model = CatBoostRegressor(
            iterations=CATBOOST_ITERATIONS_PER_BATCH,
            learning_rate=CATBOOST_LEARNING_RATE,
            depth=CATBOOST_DEPTH,
            loss_function="RMSE",
            thread_count=CATBOOST_THREAD_COUNT,
            verbose=False,
            random_state=RANDOM_STATE,
        )

        if model is None:
            new_model.fit(X_batch, y_batch)
        else:
            new_model.fit(X_batch, y_batch, init_model=model)

        model = new_model
        elapsed = time.time() - start_time
        log(f"Batch {batch_index + 1} of {n_batches} done ({batch_end - batch_start} rows) -- {elapsed:.1f}s elapsed total")

    return model


def train_native_10m_model(force_rebuild_features=False):
    """End-to-end training entry point: build/load the feature stack, construct the
    label, split train/test, train CatBoost, evaluate, and save the model + metadata to
    disk. This is run offline, once -- the API only ever loads the saved result
    (see load_trained_model / app/state.py), it never retrains per request."""
    log("=====================================================")
    log("TRAINING NATIVE-10m HEAT VULNERABILITY MODEL (full-pixel, batched)")
    log("=====================================================")

    feature_arrays, meta = build_feature_stack(force_rebuild=force_rebuild_features)
    df = flatten_feature_stack_to_dataframe(feature_arrays)

    weights, corr_matrix, mean_struct_corr, mean_cooling_corr = compute_correlations_and_weights(df)
    df["heat_vulnerability_index"] = build_heat_vulnerability_index(df, weights)

    X = df[FEATURE_NAMES]
    y = df["heat_vulnerability_index"]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=TEST_SET_FRACTION, random_state=RANDOM_STATE)
    log(f"Train size: {len(X_train)}, test size: {len(X_test)}")

    model = train_catboost_in_batches(X_train, y_train)

    predictions = model.predict(X_test)
    r_squared = r2_score(y_test, predictions)
    mean_absolute_err = mean_absolute_error(y_test, predictions)
    log(f"Held-out test R-squared: {r_squared:.4f}")
    log(f"Held-out test MAE: {mean_absolute_err:.4f} (index scaled 0-100)")

    importances = model.get_feature_importance()
    importance_df = pd.DataFrame({"feature": FEATURE_NAMES, "importance": importances}).sort_values("importance", ascending=False)
    log("Feature importance:\n" + importance_df.to_string(index=False))

    os.makedirs(MODELS_DIR, exist_ok=True)
    model.save_model(MODEL_PATH)
    log(f"Saved model artifact to {MODEL_PATH}")

    metadata = {
        "r_squared": r_squared,
        "mae": mean_absolute_err,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "weights": weights,
        "mean_struct_corr": mean_struct_corr,
        "mean_cooling_corr": mean_cooling_corr,
        "feature_names": FEATURE_NAMES,
        "importance": importance_df.to_dict(orient="records"),
        "self_consistency_caveat": (
            "The target is a deterministic formula built from features this model also trains on. "
            "A high R-squared confirms correct formula reconstruction, not external predictive skill on unseen ground truth."
        ),
    }
    np.savez(MODEL_METADATA_PATH, metadata=np.array([metadata], dtype=object))
    log(f"Saved model metadata to {MODEL_METADATA_PATH}")

    return model, metadata


def load_trained_model():
    """Load the already-trained model artifact from disk. Called once at API startup
    (app/state.py) so every request reuses the same in-memory model."""
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"No trained model found at {MODEL_PATH}. Run train_native_10m_model() first.")
    model = CatBoostRegressor()
    model.load_model(MODEL_PATH)
    metadata = np.load(MODEL_METADATA_PATH, allow_pickle=True)["metadata"][0]
    return model, metadata
