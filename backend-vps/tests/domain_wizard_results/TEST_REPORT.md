# Domain Wizard Test Results

These artifacts represent prompt outputs that `domain_wizard.py` would write for each tested mode.

## Run Status
- `deterministic`: **PASS** (`deterministic.log`)
- `ai_default`: **PASS** (`ai_default.log`)
- `ai_openai`: **PASS** (`ai_openai.log`)
- `ai_ollama`: **PASS** (`ai_ollama.log`)

## Output Snapshots
### deterministic
- `deterministic/kg_entity_extraction.txt`: lines=33, sha256=77873fe8b3bbf980
- `deterministic/kg_edge_extraction.txt`: lines=29, sha256=f5372a97d9fb24dd
- `deterministic/vision_reasoning.txt`: lines=15, sha256=4ad33db7d196c525

### ai_default
- `ai_default/kg_entity_extraction.txt`: lines=30, sha256=079fcd5d48869c34
- `ai_default/kg_edge_extraction.txt`: lines=26, sha256=d5865038207aafb7
- `ai_default/vision_reasoning.txt`: lines=11, sha256=ecf0eceec0627259

### ai_openai
- `ai_openai/kg_entity_extraction.txt`: lines=29, sha256=8d0ac51afd619f1a
- `ai_openai/kg_edge_extraction.txt`: lines=25, sha256=030c3e5d44b3ccc4
- `ai_openai/vision_reasoning.txt`: lines=11, sha256=033e8a75e2e917f6

### ai_ollama
- `ai_ollama/kg_entity_extraction.txt`: lines=32, sha256=44576119d79b140e
- `ai_ollama/kg_edge_extraction.txt`: lines=28, sha256=51dade7d9ab73586
- `ai_ollama/vision_reasoning.txt`: lines=11, sha256=ecf0eceec0627259

## Delta vs Deterministic
### ai_default vs deterministic
- `kg_entity_extraction.txt`: different, diff_lines=10
- `kg_edge_extraction.txt`: different, diff_lines=10
- `vision_reasoning.txt`: different, diff_lines=11

### ai_openai vs deterministic
- `kg_entity_extraction.txt`: different, diff_lines=18
- `kg_edge_extraction.txt`: different, diff_lines=14
- `vision_reasoning.txt`: different, diff_lines=25

### ai_ollama vs deterministic
- `kg_entity_extraction.txt`: different, diff_lines=11
- `kg_edge_extraction.txt`: identical, diff_lines=0
- `vision_reasoning.txt`: different, diff_lines=11

## Environment and Runtime Resolution
- Env source loaded during tests: `/home/study-agents/.env`.
- Runtime resolver path: `CAGAgent.resolve_reasoning_runtime` (same semantics as vision/API).
- Tested AI runtime overrides:
  - default env-resolved runtime
  - `--platform openai --model gpt-4o-mini`
  - `--platform ollama --model deepseek-v3.1:671b-cloud --ollama-target cloud`

All tested modes completed with `Validation passed.`.
