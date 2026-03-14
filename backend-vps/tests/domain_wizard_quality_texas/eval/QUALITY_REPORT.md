# Texas Domain Prompt Quality Evaluation

- Model used for evaluation: `gpt-4o-mini`
- Scenario: Texas adjuster / TWIA / HO-3 / windstorm context

## Scores
- `manual_baseline`: overall=92.7 | entity=100 | edge=100 | vision=78
- `deterministic`: overall=92.7 | entity=100 | edge=100 | vision=78
- `ai_openai`: overall=94.7 | entity=100 | edge=100 | vision=84
- `ai_ollama`: overall=94.7 | entity=100 | edge=100 | vision=84

## Notes
- `manual_baseline`: no structural quality flags
- `deterministic`: no structural quality flags
- `ai_openai`: no structural quality flags
- `ai_ollama`: no structural quality flags

## Raw Outputs
- `manual_baseline` entity: `manual_baseline_entity_output.txt`
- `manual_baseline` edge: `manual_baseline_edge_output.txt`
- `manual_baseline` vision: `manual_baseline_vision_output.txt`
- `deterministic` entity: `deterministic_entity_output.txt`
- `deterministic` edge: `deterministic_edge_output.txt`
- `deterministic` vision: `deterministic_vision_output.txt`
- `ai_openai` entity: `ai_openai_entity_output.txt`
- `ai_openai` edge: `ai_openai_edge_output.txt`
- `ai_openai` vision: `ai_openai_vision_output.txt`
- `ai_ollama` entity: `ai_ollama_entity_output.txt`
- `ai_ollama` edge: `ai_ollama_edge_output.txt`
- `ai_ollama` vision: `ai_ollama_vision_output.txt`
