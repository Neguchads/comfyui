"""Fuzz pure workflow graph and hashing functions; no ComfyUI runtime/GPU."""
import json
from hypothesis import given, settings, strategies as st
from src import pc_tools as pc

CI = settings(max_examples=150, deadline=None, derandomize=True)
ASCII = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=80)

@CI
@given(st.dictionaries(ASCII, st.integers(-100000, 100000), max_size=30))
def fuzz_compact_json(data):
    assert json.loads(pc.encoded(data)) == data
    assert pc.digest(data) == pc.digest(dict(reversed(list(data.items()))))

@CI
@given(st.lists(ASCII, max_size=20))
def fuzz_api_graph(labels):
    data = {str(i): {"class_type": "SyntheticNode", "inputs": {"text": label}}
            for i, label in enumerate(labels)}
    graph = pc.graph(data)
    assert len(graph["nodes"]) == len(data)
    report = pc.validate_graph(graph)
    assert report["status"] in ("PASS", "INCOMPLETE", "FAIL")

if __name__ == "__main__":
    fuzz_compact_json()
    fuzz_api_graph()
    print("PASS: ComfyUI pure workflow properties")
