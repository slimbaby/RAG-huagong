import json
import subprocess

result = subprocess.run(
    ['docker', 'exec', 'langgraph_rag_backend', 'cat', '/app/logs/traces/details/38176c59.json'],
    capture_output=True
)
data = json.loads(result.stdout.decode('utf-8'))
spans = data.get('spans', [])
print(f"Total spans: {len(spans)}")
print()
for s in spans:
    name = s.get('operation_name', '')
    span_type = s.get('span_type', '')
    depth = s.get('depth', 0)
    duration = s.get('duration_ms', 0)
    inputs = s.get('inputs', {})
    indent = '  ' * depth
    # Extract useful info from inputs
    extra = ''
    if 'sub_queries' in inputs:
        extra = f" sub_queries={inputs['sub_queries']}"
    elif 'query' in inputs:
        extra = f" query='{inputs['query'][:30]}'"
    print(f"{indent}[{span_type}] {name} ({duration:.0f}ms){extra}")
