"""Informational microbenchmarks (not CI regression thresholds)."""
import json
import os
import platform
from pathlib import Path
from statistics import median
from time import perf_counter_ns

def measure(label, work, repeats=200):
    work()
    observations = []
    for _ in range(5):
        started = perf_counter_ns()
        for unused in range(repeats):
            work()
        observations.append((perf_counter_ns() - started) / repeats / 1_000_000)
    return {"name": label, "median_ms_per_operation": round(median(observations), 6),
            "samples_ms": [round(t, 6) for t in observations]}

def emit(metrics):
    payload = {"schema": "stage3-benchmarks-v1", "repository": os.getenv("GITHUB_REPOSITORY", "local"),
               "sha": os.getenv("GITHUB_SHA", ""), "python": platform.python_version(),
               "os": platform.system(), "metrics": metrics,
               "note": "Observational only: runner noise prevents comparing isolated runs as a strict CI gate."}
    Path("benchmark-results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Stage 3 benchmark (informational)\n\n")
            f.write("| Test | median ms/op |\n| --- | ---: |\n")
            for metric in metrics:
                f.write(f"| {metric['name']} | {metric['median_ms_per_operation']} |\n")


from src import pc_tools as pc
SYNTHETIC = {str(i): {"class_type": "SyntheticNode", "inputs": {"value": i}} for i in range(120)}

if __name__ == "__main__":
    metrics = [
        measure("workflow_graph_120_nodes", lambda: pc.graph(SYNTHETIC), 100),
        measure("workflow_digest_120_nodes", lambda: pc.digest(SYNTHETIC), 250),
    ]
    emit(metrics)
