"""Offline structure validation; integration checks require Docker."""
from pathlib import Path
import ast, json, yaml
root = Path(__file__).resolve().parents[1]
count = 0
for p in root.rglob('*'):
    if not p.is_file() or '.git' in p.parts: continue
    if p.suffix in {'.yml', '.yaml'}: yaml.safe_load(p.read_text(encoding='utf-8')); count += 1
    elif p.suffix == '.json': json.loads(p.read_text(encoding='utf-8')); count += 1
    elif p.suffix == '.py': ast.parse(p.read_text(encoding='utf-8')); count += 1
print(f'Validated {count} YAML, JSON and Python files')
