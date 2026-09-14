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

MODELS_DIR = "D:\\Projects\\UC\\backend\\models"
MODEL_PATH = os.path.join(MODELS_DIR, "heat_vulnerability_native10m.cbm")
MODEL_METADATA_PATH = os.path.join(MODELS_DIR, "heat_vulnerability_native10m_metadata.npz")

CORRELATION_DOWNWEIGHT_THRESHOLD = 0.5
STRUCTURAL_COLUMNS = ["built_up_pct_mean", "building_density_per_km2", "road_density_km_per_km2"]
COOLING_COLUMNS = ["ndvi_mean", "ndwi_mean", "albedo_mean"]

TRAINING_BATCH_SIZE = 500000
CATBOOST_THREAD_COUNT = 6
CATBOOST_ITERATIONS_PER_BATCH = 150
CATBOOST_LEARNING_RATE = 0.05
CATBOOST_DEPTH = 6

TEST_SET_FRACTION = 0.2
RANDOM_STATE = 42


def log(msg):
    print(f"[model] {msg}", flush=True)


def normalize_0_1(array_values):
    lo = np.nanmin(array_values)
    hi = np.nanmax(array_values)
    if hi - lo < 1e-9:
        return np.zeros_like(array_values)
    return (array_values - lo) / (hi - lo)


def flatten_feature_stack_to_dataframe(feature_arrays):
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
    n = len(columns)
    sub = corr_matrix.loc[columns, columns]
    total_abs_off_diagonal = np.abs(sub.values).sum() - n
    return total_abs_off_diagonal / (n * (n - 1))


def compute_correlations_and_weights(df):
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
    normalized = pd.DataFrame({col: normalize_0_1(df[col].values) for col in weights.keys()})
    raw_index = sum(normalized[col] * weight for col, weight in weights.items())
    raw_index = raw_index - raw_index.min()
    raw_index = raw_index / raw_index.max() * 100.0
    return raw_index


def train_catboost_in_batches(X_train, y_train):
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
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"No trained model found at {MODEL_PATH}. Run train_native_10m_model() first.")
    model = CatBoostRegressor()
    model.load_model(MODEL_PATH)
    metadata = np.load(MODEL_METADATA_PATH, allow_pickle=True)["metadata"][0]
    return model, metadata
