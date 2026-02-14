# Local LLM Research for Job-CV Matching (February 2026)

## Task Description
Compare a job description (~2-3K tokens) against a CV (~2K tokens) and output a match score (0-100%) with brief reasoning. This is a text classification/ranking task with ~4-5K total tokens per comparison.

---

## 1. Model Comparison for MacBook Pro 32GB RAM

### Models That Fit in 32GB RAM

| Model | Params | Quantization | RAM Needed | Speed (tok/s) | Fits 32GB? |
|-------|--------|-------------|-----------|---------------|------------|
| **Llama 3.2 3B** | 3B | Q4_K_M | ~3 GB | 40-60 | Yes (easily) |
| **Llama 3.1 8B** | 8B | Q4_K_M | ~6 GB | 20-30 | Yes |
| **Phi-4** | 14B | Q4_K_M | ~10 GB | 15-22 | Yes |
| **Qwen 2.5 7B** | 7B | Q4_K_M | ~6 GB | 20-28 | Yes |
| **Qwen 2.5 14B** | 14B | Q4_K_M | ~10 GB | 15-22 | Yes |
| **Mistral 7B** | 7B | Q4_K_M | ~6 GB | 25-35 | Yes |
| **Gemma 2 9B** | 9B | Q4_K_M | ~7 GB | 18-25 | Yes |
| **DeepSeek-R1-Distill 14B** | 14B | Q4_K_M | ~10 GB | 12-18 | Yes |
| **Llama 3.3 70B** | 70B | Q2_K | ~28 GB | 2-5 | Barely (poor quality at Q2) |
| **Qwen 2.5 32B** | 32B | Q3_K_M | ~22 GB | 5-10 | Yes (tight) |
| **Mixtral 8x7B** | 46B (12B active) | Q4_K_M | ~26 GB | 8-15 | Yes (tight) |

### Speed Notes (MacBook Pro M3/M4 Pro, 32GB)
- Memory bandwidth is the bottleneck: M3 Pro = 150 GB/s, M4 Pro = ~273 GB/s
- 7-8B models: 20-30 tok/s via Ollama (faster with MLX: +20-30%)
- 14B models: 15-22 tok/s
- 32B models: 5-10 tok/s (tight on memory)
- 70B models: 2-5 tok/s at Q2 (not recommended on 32GB)

---

## 2. Quality vs Claude 3.5 Sonnet

### Estimated Quality for Job-CV Matching (relative to Claude Sonnet 4.5)

| Model | Quality Estimate | Notes |
|-------|-----------------|-------|
| **Claude Sonnet 4.5** | 100% (baseline) | Best reasoning, nuanced analysis, excellent instruction following |
| **Qwen 2.5 32B** | ~80-85% | Strong multilingual, good reasoning, fits 32GB at Q3 |
| **Phi-4 14B** | ~75-80% | Punches above its weight on reasoning tasks |
| **Qwen 2.5 14B** | ~75-80% | Strong coding/reasoning, good at structured output |
| **Llama 3.1 8B** | ~65-70% | Solid baseline, good instruction following |
| **Qwen 2.5 7B** | ~68-72% | Better than Llama 3.1 8B on most benchmarks |
| **Mistral 7B** | ~60-65% | Fast but less nuanced |
| **Llama 3.2 3B** | ~50-55% | Good for binary pass/fail, not nuanced scoring |
| **Gemma 2 9B** | ~65-70% | Competitive with Llama 8B |
| **DeepSeek-R1-Distill 14B** | ~75-80% | Good reasoning chains, can be verbose |

**Key insight**: For a structured task like job-CV matching with a scoring rubric, even smaller models can achieve acceptable accuracy (within 10-15% of Claude) when given a well-crafted prompt with clear scoring criteria. The gap widens for edge cases and nuanced reasoning.

---

## 3. Recommended Hybrid Architecture

### Two-Stage Pipeline

```
Stage 1: LOCAL MODEL (Fast Filter)          Stage 2: CLAUDE API (Detailed Analysis)
+----------------------------------+        +----------------------------------+
| Ollama: Phi-4 14B or Qwen 2.5 7B|        | Claude Haiku 4.5 or Sonnet 4.5  |
|                                  |        |                                  |
| Input: Job desc + CV             |   -->  | Input: Job desc + CV + Stage 1   |
| Output: Score 0-100, 1-2 lines   |  Top   | Output: Detailed match report     |
| Speed: ~15-25 tok/s              |  20%   | with skill gap analysis, fit      |
| Cost: $0 (local)                 |        | reasoning, recommendations        |
| Latency: 3-8 seconds             |        | Cost: $0.001-0.015 per match      |
+----------------------------------+        +----------------------------------+
```

### How It Works
1. **Stage 1 (Local)**: Run ALL candidates through the local model
   - Binary classification: "Strong match" / "Possible match" / "Poor match"
   - Or numeric score: 0-100 with 1-2 sentence reasoning
   - Takes 3-8 seconds per candidate
   - Cost: $0

2. **Stage 2 (Cloud API)**: Send only top 15-25% to Claude
   - Detailed analysis with skill gap identification
   - Structured JSON output with multiple scoring dimensions
   - Personalized recommendations
   - Takes 2-5 seconds per candidate (API latency)
   - Cost: ~$0.001 (Haiku) to ~$0.015 (Sonnet) per match

### Cost Analysis

**Scenario: 100 job applications to evaluate**

| Approach | Cost per match | Total (100 apps) | Monthly (1000 apps) |
|----------|---------------|-------------------|---------------------|
| All via Sonnet 4.5 | ~$0.03 | $3.00 | $30.00 |
| All via Haiku 4.5 | ~$0.006 | $0.60 | $6.00 |
| Hybrid: Local + Sonnet (top 20%) | ~$0.006 | $0.60 | $6.00 |
| Hybrid: Local + Haiku (top 25%) | ~$0.0015 | $0.15 | $1.50 |
| All local (Phi-4) | $0 | $0 | $0 |

**Token math per comparison:**
- Input: ~5K tokens (job desc + CV + system prompt)
- Output: ~200 tokens (score + reasoning) for filtering, ~800 tokens for detailed
- Sonnet 4.5: (5K * $3 + 0.8K * $15) / 1M = $0.027 per detailed match
- Haiku 4.5: (5K * $1 + 0.8K * $5) / 1M = $0.009 per detailed match

**Hybrid savings: 75-95% compared to "all Sonnet" approach**

---

## 4. Model Recommendations by Strategy

### Strategy A: "All Local" (Zero Cost, Acceptable Quality)
- **Best model**: Phi-4 14B or Qwen 2.5 14B
- **Quality**: ~75-80% of Claude Sonnet
- **Speed**: ~15-22 tok/s, ~5-8s per match
- **Cost**: $0
- **Best for**: Personal projects, privacy-sensitive, offline use

### Strategy B: "Hybrid" (Best Cost/Quality Balance) -- RECOMMENDED
- **Local filter**: Qwen 2.5 7B (fast, 25+ tok/s) or Phi-4 14B (better quality)
- **Cloud detail**: Haiku 4.5 for good-enough detail, Sonnet 4.5 for best quality
- **Quality**: ~90-95% of all-Sonnet approach
- **Cost**: ~$1.50-$6.00 per 1000 matches
- **Best for**: Production apps with moderate volume

### Strategy C: "All Cloud" (Best Quality, Simplest)
- **Model**: Haiku 4.5 for all matches (best value)
- **Quality**: ~85% of Sonnet (Haiku), 100% (Sonnet)
- **Cost**: $6-30 per 1000 matches
- **Best for**: Low volume, quality-critical applications

---

## 5. Fine-Tuning Options

### Can These Models Be Fine-Tuned?

| Model | Fine-tunable? | Method | Hardware Needed |
|-------|-------------|--------|-----------------|
| **Llama 3.1/3.2** | Yes | LoRA/QLoRA | 16-24GB GPU (or Mac with 32GB via MLX) |
| **Qwen 2.5** | Yes | LoRA/QLoRA | 16-24GB GPU |
| **Phi-4** | Yes | LoRA/QLoRA | 12-16GB GPU |
| **Mistral** | Yes | LoRA/QLoRA | 16-24GB GPU |
| **Gemma 2** | Yes | LoRA/QLoRA | 16-24GB GPU |
| **DeepSeek-R1-Distill** | Yes | LoRA/QLoRA | 16-24GB GPU |

### Fine-Tuning Workflow for Job Matching

```
1. Collect Training Data
   - Use Claude Sonnet to score 200-500 job-CV pairs with detailed reasoning
   - This creates high-quality labeled data at ~$6-15 total cost

2. Format as Training Set
   - Input: System prompt + Job description + CV
   - Output: Score (0-100) + reasoning (1-3 sentences)

3. Fine-Tune with QLoRA (on Mac)
   - Tool: Unsloth (2x faster, 80% less VRAM)
   - Or: MLX-LM fine-tuning (native Apple Silicon)
   - Base model: Qwen 2.5 7B or Phi-4 14B
   - Training time: 1-4 hours on Mac with 32GB
   - QLoRA reduces memory from ~28GB to ~9GB for a 7B model

4. Convert & Deploy
   - Export to GGUF format
   - Load in Ollama with custom Modelfile
   - LoRA adapter sits on top of base model

5. Expected Improvement
   - Fine-tuned 7B model can approach or match un-tuned 14B model quality
   - Estimated quality boost: +10-15% on your specific matching task
   - A fine-tuned Qwen 2.5 7B could reach ~80-85% of Claude Sonnet quality
```

### Key Fine-Tuning Tips
- 100 excellent training examples beat 1,000 mediocre ones
- Target all linear layers, not just attention (better results)
- Use validation set (20% of data) to monitor overfitting
- QLoRA is ~30% slower to train than LoRA but uses 35-40% less memory
- Start with LoRA rank r=16, alpha=32 as a good default

---

## 6. Claude API: Haiku vs Sonnet Cost Comparison

### Pricing (February 2026)

| Model | Input (per 1M tokens) | Output (per 1M tokens) | With Batch API (50% off) | With Caching (90% off input) |
|-------|----------------------|------------------------|--------------------------|------------------------------|
| **Haiku 3.5** (legacy) | $0.80 | $4.00 | $0.40 / $2.00 | $0.08 / $4.00 |
| **Haiku 4.5** | $1.00 | $5.00 | $0.50 / $2.50 | $0.10 / $5.00 |
| **Sonnet 4.5** | $3.00 | $15.00 | $1.50 / $7.50 | $0.30 / $15.00 |
| **Opus 4.5** | $5.00 | $25.00 | $2.50 / $12.50 | $0.50 / $25.00 |

### Cost Per Job-CV Match (~5K input, ~500 output tokens)

| Model | Standard | With Batch API | With Prompt Caching* |
|-------|---------|---------------|---------------------|
| **Haiku 4.5** | $0.0075 | $0.00375 | ~$0.003 |
| **Sonnet 4.5** | $0.0225 | $0.01125 | ~$0.009 |

*Prompt caching works well here: system prompt + scoring rubric can be cached across all comparisons.

### Recommendation: Use Haiku 4.5 + Batch API
- For 1,000 matches/month: ~$3.75 with batch API
- Quality is within 5 percentage points of Sonnet on classification tasks
- 3x cheaper than Sonnet
- Combined with prompt caching: ~$2-3/month for 1,000 matches

---

## 7. Implementation Recommendation

### For the Job Tracker App: Start Simple, Scale Smart

**Phase 1: MVP (Now)**
- Use Haiku 4.5 API for all matching
- Simple, no infrastructure to maintain
- Cost: ~$4-8/month for moderate usage
- Quality: Very good for scoring/classification

**Phase 2: Add Local Filtering (When Volume Grows)**
- Install Ollama + Qwen 2.5 7B for local pre-filtering
- Only send top 20-25% to Haiku/Sonnet API
- Cost drops to ~$1-2/month
- Speed: Instant local scoring, no API latency for most candidates

**Phase 3: Fine-Tune (When You Have Data)**
- After 3-6 months, you'll have 500+ scored matches from Claude
- Fine-tune Qwen 2.5 7B or Phi-4 on your actual data
- Fine-tuned local model could handle 80%+ of matches alone
- Reserve Claude for edge cases and detailed reports

### Quick Start: Ollama Setup

```bash
# Install Ollama
brew install ollama

# Pull recommended models
ollama pull qwen2.5:7b          # Best balance of speed/quality
ollama pull phi4:14b             # Best quality for 32GB
ollama pull llama3.1:8b          # Good baseline alternative

# Test job matching
ollama run qwen2.5:7b "Score this job-CV match from 0-100..."
```

### Quick Start: Ollama API Usage (Python)

```python
import requests

def local_match_score(job_desc: str, cv_text: str) -> dict:
    prompt = f"""Score how well this CV matches the job description.
    Return JSON: {{"score": 0-100, "reasoning": "1-2 sentences"}}

    JOB DESCRIPTION:
    {job_desc}

    CV:
    {cv_text}"""

    response = requests.post("http://localhost:11434/api/generate", json={
        "model": "qwen2.5:7b",
        "prompt": prompt,
        "stream": False,
        "format": "json"
    })
    return response.json()
```

---

## Summary Table

| Criterion | All Local | Hybrid (Recommended) | All Cloud (Haiku) | All Cloud (Sonnet) |
|-----------|-----------|---------------------|-------------------|-------------------|
| **Quality** | 75-80% | 90-95% | ~85% | 100% |
| **Cost/1000 matches** | $0 | $1.50-6.00 | $4-8 | $20-30 |
| **Latency** | 3-8s | 3-8s local, 2-5s API | 1-3s | 2-5s |
| **Privacy** | Full | Partial | None | None |
| **Setup complexity** | Medium | High | Low | Low |
| **Fine-tunable** | Yes | Yes (local part) | No | No |
| **Offline capable** | Yes | Partially | No | No |
