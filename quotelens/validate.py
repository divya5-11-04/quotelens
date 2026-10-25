"""Checks on a parsed line item: arithmetic, units, and price against history."""
import statistics

from .catalog import BY_ID, RATE_FACTOR

MIN_HISTORY = 8
SEVERITY = {"arithmetic_mismatch": "error", "ocr_uncertain": "warn", "ocr_repaired": "info", "unit_suspect": "error", "unit_mismatch": "error",
            "price_high": "warn", "price_low": "warn", "unmatched": "warn",
            "unit_converted": "info", "no_history": "info"}
MESSAGES = {
    "arithmetic_mismatch": "qty x rate does not equal the stated amount",
    "ocr_uncertain": "qty x rate does not equal the amount; in a scan an OCR misread is more likely than a supplier error",
    "ocr_repaired": "unclear characters were resolved using qty x rate = amount",
    "unit_suspect": "rate is about 1000x away from history; the unit may be wrong (kg vs tonne)",
    "unit_mismatch": "unit cannot be converted to the catalog unit",
    "price_high": "rate is well above the historical median",
    "price_low": "rate is well below the historical median",
    "unmatched": "description did not match any catalog material",
    "unit_converted": "rate converted to the catalog unit for comparison",
    "no_history": "not enough price history to check this rate",
}


def flag(code, detail=""):
    return {"code": code, "severity": SEVERITY[code], "message": MESSAGES[code] + (f" ({detail})" if detail else "")}


def robust_z(value, history):
    med = statistics.median(history)
    mad = statistics.median(abs(x - med) for x in history)
    scale = max(1.4826 * mad, 0.02 * med)   # floor so a very tight history does not make z explode
    return (value - med) / scale, med


def check_item(qty, unit, rate, amount, catalog_id, history, ocr=False, repaired=False):
    """Returns (rate_norm or None, flags). rate_norm is the rate in the catalog's unit."""
    flags = []
    if abs(qty * rate - amount) > max(1.0, 0.01 * amount):
        flags.append(flag("ocr_uncertain" if ocr else "arithmetic_mismatch",
                          f"{qty:g} x {rate:g} = {qty * rate:,.2f}, read {amount:,.2f}"))
    if repaired:
        flags.append(flag("ocr_repaired"))
    mat = BY_ID.get(catalog_id)
    if mat is None:
        flags.append(flag("unmatched"))
        return None, flags
    rate_norm = rate
    if unit != mat.unit:
        factor = RATE_FACTOR.get((unit, mat.unit))
        if factor is None:
            flags.append(flag("unit_mismatch", f"{unit} vs {mat.unit}"))
            return None, flags
        rate_norm = rate * factor
        flags.append(flag("unit_converted", f"{unit} -> {mat.unit}"))
    if len(history) < MIN_HISTORY:
        flags.append(flag("no_history"))
        return rate_norm, flags
    z, med = robust_z(rate_norm, history)
    ratio = rate_norm / med
    if 500 <= ratio <= 2000 or 1 / 2000 <= ratio <= 1 / 500:
        flags.append(flag("unit_suspect", f"{ratio:.0f}x the median {med:,.2f}/{mat.unit}"))
    elif ratio > 1.3 and z > 3.5:
        flags.append(flag("price_high", f"{rate_norm:,.2f} vs median {med:,.2f} per {mat.unit}"))
    elif ratio < 0.7 and z < -3.5:
        flags.append(flag("price_low", f"{rate_norm:,.2f} vs median {med:,.2f} per {mat.unit}"))
    return rate_norm, flags
