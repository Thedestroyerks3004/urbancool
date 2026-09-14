"""
Broadband surface albedo, derived (not measured) from Sentinel-2 reflectance.

Method: Liang (2001) empirical narrowband-to-broadband conversion, originally
defined for five Landsat TM/ETM+ bands (blue, red, NIR, SWIR1, SWIR2):

    albedo = 0.356*blue + 0.130*red + 0.373*nir + 0.085*swir1 + 0.072*swir2 - 0.0018

This project's resolution-honesty policy (see D:\\Projects\\UC\\DATA_REPORT.md, section 4)
already excludes Sentinel-2's SWIR bands (B11/B12) because they are natively 20m, not the
10m floor this project requires. Without SWIR1/SWIR2, we use only the blue/red/NIR terms,
renormalized so the remaining coefficients still sum to 1 (0.356+0.130+0.373 = 0.859):

    albedo_approx = (0.356*blue + 0.130*red + 0.373*nir) / 0.859

This is a real, named, disclosed approximation, not the full 5-band Liang formula. It will
diverge from true broadband albedo most over materials with strong SWIR reflectance
contrast (e.g. certain roofing, bare/dry soil). Every value this module produces is
returned with is_modeled=True in its metadata so downstream API responses can flag it as
estimated, not measured.
"""

import numpy as np

# Published Liang (2001) coefficients -- these are not tunable parameters, they're a
# citation. Changing them means computing a different, undocumented formula; if SWIR
# bands ever become usable (see the module docstring above), the correct fix is adding
# their terms back in with published coefficients, not editing these three numbers.
LIANG_2001_BLUE_COEFFICIENT = 0.356
LIANG_2001_RED_COEFFICIENT = 0.130
LIANG_2001_NIR_COEFFICIENT = 0.373
# Recomputed automatically from the three coefficients above -- always keep this a
# derived value, never hardcode it, or it will silently stop matching them if one changes.
RENORMALIZATION_DIVISOR = LIANG_2001_BLUE_COEFFICIENT + LIANG_2001_RED_COEFFICIENT + LIANG_2001_NIR_COEFFICIENT

# Sentinel-2 stores reflectance as an integer 0-10000 (not a 0-1 float) to save space.
# This is a sensor/format constant, not a tunable setting -- it must match how the
# fetching scripts wrote the band files, or every albedo value comes out 10000x too small.
SENTINEL2_REFLECTANCE_SCALE_FACTOR = 10000.0

# Physical bounds on albedo (it's a reflectance fraction, can't be negative or over 1).
# Values outside this range come from the partial 3-band formula's approximation error,
# not real physics -- clipped here rather than left to propagate into the model as
# nonsensical inputs.
ALBEDO_VALID_MINIMUM = 0.0
ALBEDO_VALID_MAXIMUM = 1.0


def compute_albedo_from_raw_bands(blue_band_raw, red_band_raw, nir_band_raw):
    """
    blue_band_raw, red_band_raw, nir_band_raw: numpy arrays of raw Sentinel-2 digital
    numbers (surface reflectance scaled by 10000, as stored in this project's validated
    monthly band rasters). Returns broadband albedo in [0, 1], with out-of-range pixels
    clipped and counted so the caller can report how much of the scene needed clipping.
    """
    blue_reflectance = blue_band_raw.astype(np.float64) / SENTINEL2_REFLECTANCE_SCALE_FACTOR
    red_reflectance = red_band_raw.astype(np.float64) / SENTINEL2_REFLECTANCE_SCALE_FACTOR
    nir_reflectance = nir_band_raw.astype(np.float64) / SENTINEL2_REFLECTANCE_SCALE_FACTOR

    raw_albedo = (
        LIANG_2001_BLUE_COEFFICIENT * blue_reflectance
        + LIANG_2001_RED_COEFFICIENT * red_reflectance
        + LIANG_2001_NIR_COEFFICIENT * nir_reflectance
    ) / RENORMALIZATION_DIVISOR

    number_of_pixels_below_range = int(np.sum(raw_albedo < ALBEDO_VALID_MINIMUM))
    number_of_pixels_above_range = int(np.sum(raw_albedo > ALBEDO_VALID_MAXIMUM))

    clipped_albedo = np.clip(raw_albedo, ALBEDO_VALID_MINIMUM, ALBEDO_VALID_MAXIMUM)

    validation_report = {
        "pixels_clipped_below_zero": number_of_pixels_below_range,
        "pixels_clipped_above_one": number_of_pixels_above_range,
        "total_pixels": int(raw_albedo.size),
    }

    return clipped_albedo, validation_report


ALBEDO_METHOD_METADATA = {
    "method": "Liang (2001) narrowband-to-broadband conversion, renormalized to blue+red+NIR only",
    "is_modeled": True,
    "is_measured": False,
    "excluded_bands": ["SWIR1 (Sentinel-2 B11)", "SWIR2 (Sentinel-2 B12)"],
    "exclusion_reason": "Both SWIR bands are natively 20m, disqualified under this project's 10m-or-finer resolution policy.",
    "renormalization_divisor": RENORMALIZATION_DIVISOR,
    "coefficients_used": {
        "blue": LIANG_2001_BLUE_COEFFICIENT,
        "red": LIANG_2001_RED_COEFFICIENT,
        "nir": LIANG_2001_NIR_COEFFICIENT,
    },
    "caveat": "Broadband albedo estimated from a partial (3-band) empirical formula; expect systematic divergence from true albedo over high-SWIR-contrast surfaces.",
}
