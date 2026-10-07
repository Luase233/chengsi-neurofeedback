"""Durations over accepted source-time coverage, with explicit discontinuities."""
import math


def peak_metrics(trajectory, margin=5.0):
    """Integrate linear crossings only inside continuous, accepted intervals.

    A first score in each segment represents its own accepted window contribution.
    Later scores interpolate from the preceding endpoint. Invalid windows and phase
    boundaries break a run; no sample or UI-held score fills a missing interval.
    """
    def accepted(point):
        return bool(point.get("valid")) and all(isinstance(point.get(key), (int, float)) and math.isfinite(point[key])
            for key in ("score", "time", "valid_duration", "contribution_start", "contribution_end")) and point["valid_duration"] > 0
    valid = [p for p in trajectory if accepted(p)]
    if not valid:
        return {"peak_value": None, "peak_time": None, "threshold": None,
                "longest_peak_duration": 0.0, "total_peak_duration": 0.0, "n_peaks": 0}
    peak = max(valid, key=lambda p: p["score"])
    threshold = peak["score"] - margin
    intervals = []
    previous = None
    for point in trajectory:
        if not point.get("valid") or point.get("score") is None:
            previous = None
            continue
        duration = point.get("valid_duration", 0)
        if duration == 0:
            continue
        if not accepted(point):
            previous = None
            continue
        a, b = point["contribution_start"], point["contribution_end"]
        connected = (previous is not None and previous.get("segment") == point.get("segment")
                     and abs(previous["contribution_end"] - a) < 1e-6)
        y0, y1 = (previous["score"] if connected else point["score"]), point["score"]
        left, right = a, b
        if y0 < threshold and y1 < threshold:
            previous = point
            continue
        if y0 < threshold <= y1:
            left = a + (b - a) * (threshold - y0) / (y1 - y0)
        elif y1 < threshold <= y0:
            right = a + (b - a) * (threshold - y0) / (y1 - y0)
        if right > left:
            segment = point.get("segment")
            if intervals and connected and intervals[-1][2] == segment and abs(intervals[-1][1] - left) < 1e-6:
                intervals[-1][1] = right
            else:
                intervals.append([left, right, segment])
        previous = point
    durations = [b - a for a, b, _ in intervals]
    return {"peak_value": round(peak["score"], 1), "peak_time": round(peak["time"], 1),
            "threshold": round(threshold, 1), "longest_peak_duration": round(max(durations, default=0), 1),
            "total_peak_duration": round(sum(durations), 1), "n_peaks": len(durations),
            "method": "source-time accepted coverage; piecewise-linear crossings; invalid/phase gaps split runs",
            "margin": margin}
