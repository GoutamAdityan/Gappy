# Butterfly Effect — Full Technical Build Specification
## For Claude Code + Lemma CLI Session

> **Read this entire document before writing a single line of code.**
> Every architectural decision in here was made deliberately. Do not improvise alternatives unless explicitly told to. If something is unclear, stop and ask.

---

## 0. What This Project Is

**Butterfly Effect** is a news cascade reasoning engine built on the Lemma platform.

A user submits a breaking news headline. The system traces that event through exactly four consequence layers — Macro → Micro → Professional → Personal — producing a connected cascade graph where each node is validated against real search results before the next node is generated.

The output is a live-updating visual graph where the audience watches the AI validate (or reject and replace) each step in real time.

**Tagline:** One piece of news. Every subsequent consequence.

**Example cascade:**
```
Apple acquires Company X
    ↓ [Macro]
Competitor stock likely drops
    ↓ [Micro]
Supplier contracts renegotiate
    ↓ [Professional]
Open-source library your team uses loses funding
    ↓ [Personal]
Your interview next week will likely include questions about this
```

---

## 1. Absolute Constraints — Do Not Violate These

1. **No Supabase.** Lemma is the only data layer. Do not introduce any secondary database.
2. **No LinkedIn.** Onboarding uses GitHub REST API (unauthenticated) and PDF resume upload only.
3. **No live TTS/audio pipeline.** Audio is a mock button labeled `"Generate Morning Audio Commute Brief (v2)"`. Do not build real audio.
4. **No cascade_graphs table.** Cascade state lives in memory during execution. It is gone when the session ends. This is intentional.
5. **LLM URLs are banned.** The Auditor Agent never outputs URL strings. It outputs integer IDs only. The backend maps IDs to URLs. See Section 6.
6. **Solo build.** No assumptions about a second developer.

---

## 2. Tech Stack

| Component | Choice | Reason |
|---|---|---|
| Platform | Lemma (via CLI) | Entire orchestration layer |
| LLM | NVIDIA NIM — `meta/llama-3.1-70b-instruct` | Best free reasoning model on NIM |
| Search API | Brave Search API | Free tier, 2,000 queries/month, clean REST |
| Frontend | Lemma App (single HTML file with vanilla JS) | No build step, deployable via `lemma apps deploy` |
| Onboarding input 1 | GitHub REST API (unauthenticated) | Free, reliable, structured |
| Onboarding input 2 | PDF resume upload → Lemma Docs | Native Lemma primitive |
| State during cascade | In-memory JSON object | No persistence needed for MVP |

**NVIDIA NIM base URL:** `https://integrate.api.nvidia.com/v1`
**NIM model string:** `meta/llama-3.1-70b-instruct`

**Brave Search endpoint:** `https://api.search.brave.com/res/v1/web/search`
**Brave Search header:** `X-Subscription-Token: YOUR_BRAVE_API_KEY`

---

## 3. Lemma Pod Structure

### 3.1 Initialize the Pod

```bash
lemma pod create butterfly-effect
cd butterfly-effect
```

### 3.2 Pod Directory Layout (target state after full build)

```
butterfly-effect/
├── agents/
│   ├── root-analyzer.yaml        # Generates cascade nodes layer by layer
│   └── auditor.yaml              # Validates nodes via Brave Search
├── workflows/
│   └── cascade-loop.yaml         # Orchestrates the full 4-layer loop
├── functions/
│   ├── github-extractor.js       # Calls GitHub REST API, returns structured profile
│   ├── resume-parser.js          # Reads uploaded PDF from Lemma Docs, extracts text
│   ├── brave-search.js           # Calls Brave Search API, returns results array
│   └── url-mapper.js             # Maps integer IDs from Auditor back to real URLs
├── tables/
│   └── user_profiles.yaml        # Single table: user context vectors
├── apps/
│   └── index.html                # Live cascade ticker UI
└── docs/
    └── resumes/                  # Uploaded PDF resumes stored here
```

---

## 4. Data Layer — user_profiles Table

### 4.1 Create the Table

```bash
lemma table init user_profiles
```

### 4.2 Schema

```yaml
name: user_profiles
description: Stores extracted user context vectors used by the Professional and Personal layers of the cascade.
columns:
  - name: id
    type: text
    primary: true
    description: User identifier (GitHub username)

  - name: github_username
    type: text
    description: Raw GitHub username provided during onboarding

  - name: tech_stack
    type: text
    description: Comma-separated list of primary languages and frameworks extracted from GitHub repos and resume. Example: "Python, PyTorch, Next.js, FastAPI"

  - name: top_repos
    type: text
    description: JSON array string of top 5 pinned/starred repo names and descriptions

  - name: job_target
    type: text
    description: Target role extracted from resume. Example: "AI Product Engineer" or "ML Infrastructure"

  - name: current_projects
    type: text
    description: Active project names and one-line descriptions extracted from resume or GitHub

  - name: upcoming_events
    type: text
    description: Any time-sensitive events extracted from resume text. Example: "Interview with Sarvam AI next week"

  - name: company_context
    type: text
    description: Current employer or target companies mentioned in resume

  - name: resume_doc_id
    type: text
    description: Lemma Docs reference ID for the uploaded PDF resume

  - name: created_at
    type: datetime
    default: now
```

---

## 5. Onboarding Workflow

**Purpose:** User provides GitHub username + uploads resume PDF. System extracts structured context and writes it to `user_profiles` table. This runs once per user before any cascade.

### 5.1 GitHub Extraction Function

**File:** `functions/github-extractor.js`

```javascript
// Input: { github_username: string }
// Output: { tech_stack: string, top_repos: string, error?: string }

async function run(input) {
  const username = input.github_username;
  const headers = { 'User-Agent': 'butterfly-effect-app' };

  // Fetch pinned repos via GraphQL (unauthenticated public data)
  // Fallback: use REST API for public repos
  const reposResponse = await fetch(
    `https://api.github.com/users/${username}/repos?sort=updated&per_page=10`,
    { headers }
  );

  if (!reposResponse.ok) {
    return { error: `GitHub user not found: ${username}` };
  }

  const repos = await reposResponse.json();

  // Extract languages from top repos
  const languageCounts = {};
  for (const repo of repos.slice(0, 6)) {
    if (repo.language) {
      languageCounts[repo.language] = (languageCounts[repo.language] || 0) + 1;
    }
  }

  const tech_stack = Object.entries(languageCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([lang]) => lang)
    .join(', ');

  const top_repos = JSON.stringify(
    repos.slice(0, 5).map(r => ({
      name: r.name,
      description: r.description || '',
      language: r.language || 'unknown',
      stars: r.stargazers_count
    }))
  );

  return { tech_stack, top_repos };
}
```

### 5.2 Resume Parser Function

**File:** `functions/resume-parser.js`

```javascript
// Input: { doc_id: string }
// Output: { job_target: string, current_projects: string, upcoming_events: string, company_context: string }
// This function reads the resume text from Lemma Docs and passes it to the LLM for structured extraction

async function run(input, context) {
  // Read resume text from Lemma document store
  const resumeText = await context.docs.read(input.doc_id);

  // Call NIM LLM to extract structured fields
  const response = await fetch('https://integrate.api.nvidia.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${process.env.NIM_API_KEY}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      model: 'meta/llama-3.1-70b-instruct',
      messages: [
        {
          role: 'system',
          content: `You extract structured information from resumes. Return ONLY valid JSON with no markdown formatting or backticks. Extract exactly these fields:
{
  "job_target": "current role or most recent target role, one phrase",
  "current_projects": "comma-separated list of active or recent projects with one-word descriptions",
  "upcoming_events": "any time-sensitive items mentioned like interviews, deadlines, or launches. Empty string if none found.",
  "company_context": "current employer or target companies mentioned"
}`
        },
        {
          role: 'user',
          content: `Extract structured information from this resume:\n\n${resumeText}`
        }
      ],
      max_tokens: 500,
      temperature: 0.1
    })
  });

  const data = await response.json();
  const rawContent = data.choices[0].message.content.trim();

  try {
    return JSON.parse(rawContent);
  } catch (e) {
    // If JSON parse fails, return safe defaults
    return {
      job_target: '',
      current_projects: '',
      upcoming_events: '',
      company_context: ''
    };
  }
}
```

### 5.3 Onboarding Workflow Definition

**File:** `workflows/onboarding.yaml`

```yaml
name: onboarding
description: Extracts user context from GitHub and resume, writes to user_profiles table
trigger:
  type: manual
  input_schema:
    github_username:
      type: string
      required: true
    resume_doc_id:
      type: string
      required: true

steps:
  - id: extract_github
    type: function
    function: github-extractor
    input:
      github_username: "{{trigger.github_username}}"

  - id: extract_resume
    type: function
    function: resume-parser
    input:
      doc_id: "{{trigger.resume_doc_id}}"

  - id: write_profile
    type: function
    function: write-user-profile
    input:
      id: "{{trigger.github_username}}"
      github_username: "{{trigger.github_username}}"
      tech_stack: "{{steps.extract_github.tech_stack}}"
      top_repos: "{{steps.extract_github.top_repos}}"
      job_target: "{{steps.extract_resume.job_target}}"
      current_projects: "{{steps.extract_resume.current_projects}}"
      upcoming_events: "{{steps.extract_resume.upcoming_events}}"
      company_context: "{{steps.extract_resume.company_context}}"
      resume_doc_id: "{{trigger.resume_doc_id}}"
```

---

## 6. The Core Cascade Architecture

### 6.1 The Two Agents

**Critical design rule:** These two agents alternate. The Root Analyzer generates one node. The Auditor validates it. If validated, Root Analyzer generates the next node. If rejected, Root Analyzer is called again for the same layer with the failure context included so it branches to a different claim. This continues until all 4 layers have a validated node.

---

### 6.2 Root Analyzer Agent

**File:** `agents/root-analyzer.yaml`

```yaml
name: root-analyzer
description: Generates a single downstream consequence node for the given layer of the cascade. Takes the current cascade state and produces the next claim to be validated.
model:
  provider: openai-compatible
  base_url: https://integrate.api.nvidia.com/v1
  model: meta/llama-3.1-70b-instruct
  api_key_env: NIM_API_KEY
```

**Root Analyzer System Prompt** (embed in agent definition):

```
You are the Root Analyzer for the Butterfly Effect cascade engine. 

Your job is to generate ONE downstream consequence for the given cascade layer. You receive:
- The original news event
- The current cascade chain built so far (previous validated nodes)
- The target layer you must generate for (Macro, Micro, Professional, or Personal)
- The user's professional context (tech stack, role, projects, upcoming events)
- Any previously REJECTED claims for this layer (so you do not repeat them)

LAYER DEFINITIONS:
- Macro: Broad market or industry-level consequence (stock movements, regulatory shifts, industry consolidation)
- Micro: Supply chain, organizational, or direct ecosystem ripple (supplier contracts, team restructuring, tool deprecation)
- Professional: Direct impact on the user's specific tech stack, company, or project (based on user profile)
- Personal: The single most actionable consequence for THIS user today (upcoming interview angle, dependency warning, career pivot signal)

STRICT OUTPUT FORMAT — return ONLY this JSON, no markdown, no explanation:
{
  "layer": "Macro|Micro|Professional|Personal",
  "claim": "One specific, falsifiable claim about what happens next. Maximum 2 sentences. Name specific entities where possible.",
  "search_query": "The optimal 6-10 word search query to verify this claim using a web search engine",
  "reasoning": "One sentence explaining the logical connection from the previous node to this claim"
}

RULES:
1. Claims must be specific and falsifiable. "Markets may react" is forbidden. "NVIDIA stock is likely to drop as institutional investors price in reduced data center demand" is acceptable.
2. The Personal layer MUST reference the user's actual context. If their resume mentions an interview, reference it. If their tech stack includes PyTorch, reference it.
3. Do not repeat any claim listed in rejected_claims for this layer.
4. If generating a Professional or Personal node, you MUST incorporate the user_profile data provided.
5. The logical chain must be unbroken. Each claim must follow causally from the previous node.
```

---

### 6.3 Auditor Agent

**File:** `agents/auditor.yaml`

```yaml
name: auditor
description: Validates a cascade node claim against real Brave Search results. Outputs confidence score and verified source IDs (integers, never URLs).
model:
  provider: openai-compatible
  base_url: https://integrate.api.nvidia.com/v1
  model: meta/llama-3.1-70b-instruct
  api_key_env: NIM_API_KEY
```

**Auditor System Prompt** (embed in agent definition):

```
You are an absolute skeptic. Your job is to validate claims using ONLY the provided search result snippets.

You will receive:
- A claim generated by the Root Analyzer
- An array of search results, each with an integer index (0-4), a title, and a snippet

CONFIDENCE RUBRIC — you must follow this exactly:
- Score 5: A snippet explicitly names the entities in the claim AND confirms the exact action or outcome described. Direct match.
- Score 4: A snippet confirms the general situation and makes the claim highly plausible, with at least one entity match.
- Score 3: Snippets confirm a related trend but do not name the specific entities or exact action. Circumstantial support.
- Score 2: Snippets are tangentially related but do not support the claim. Weak connection.
- Score 1: Snippets contradict the claim, or no snippet has any relevant content.

VALIDATION RULE:
- If confidence_score <= 2, set "validated": false
- If confidence_score >= 3, set "validated": true

CRITICAL URL RULE:
You are BANNED from writing any URL string in your output.
You may ONLY output the integer index of the search result you used.
The system will map your integer to the real URL. If you write a URL, the system will crash.

OUTPUT FORMAT — return ONLY this JSON, no markdown, no explanation:
{
  "node_id": "{{node_id}}",
  "validated": true|false,
  "confidence_score": 1|2|3|4|5,
  "evidence_extracted": "One sentence describing what the search results confirm or deny about this claim.",
  "verified_source_ids": [0, 2]
}

If no search result supports the claim, return verified_source_ids as an empty array [].
```

---

### 6.4 Brave Search Function

**File:** `functions/brave-search.js`

```javascript
// Input: { query: string }
// Output: { results: Array<{index: number, title: string, snippet: string, url: string}> }
// The URL is stored here in the backend only. It is NEVER passed to the LLM.

async function run(input) {
  const response = await fetch(
    `https://api.search.brave.com/res/v1/web/search?q=${encodeURIComponent(input.query)}&count=5`,
    {
      headers: {
        'Accept': 'application/json',
        'X-Subscription-Token': process.env.BRAVE_API_KEY
      }
    }
  );

  if (!response.ok) {
    return { results: [], error: `Brave Search failed: ${response.status}` };
  }

  const data = await response.json();
  const webResults = data.web?.results || [];

  // Structure results with integer indices
  // URLs are stored here and mapped by url-mapper.js — never passed to LLM
  const results = webResults.slice(0, 5).map((r, index) => ({
    index,
    title: r.title || '',
    snippet: r.description || '',
    url: r.url || ''   // stored in backend, not passed to LLM prompt
  }));

  return { results };
}
```

### 6.5 URL Mapper Function

**File:** `functions/url-mapper.js`

```javascript
// Input: { verified_source_ids: number[], search_results: Array<{index, title, snippet, url}> }
// Output: { verified_sources: Array<{title: string, url: string}> }
// This is where integer IDs from the Auditor get resolved to real URLs

async function run(input) {
  const { verified_source_ids, search_results } = input;

  if (!verified_source_ids || verified_source_ids.length === 0) {
    return { verified_sources: [] };
  }

  const verified_sources = verified_source_ids
    .map(id => search_results.find(r => r.index === id))
    .filter(Boolean)
    .map(r => ({ title: r.title, url: r.url }));

  return { verified_sources };
}
```

---

## 7. The Cascade Loop Workflow

### 7.1 Critical Implementation Decision — Test This First

**Before building anything else, run this test in your Lemma CLI session:**

```bash
lemma workflow init cascade-loop
```

Then check whether Lemma's workflow engine supports **conditional steps** — specifically:
- Can a workflow step's execution depend on the output of a previous step?
- Can a workflow loop back to a previous step based on a condition (e.g., `validated: false` triggers a retry)?

**Based on the answer, use one of these implementation paths:**

---

**PATH A — If Lemma supports conditional loops (preferred):**

Build the cascade as a loop workflow where each iteration:
1. Root Analyzer generates a node for the current layer
2. Brave Search fetches results
3. Auditor validates
4. If `validated: false` → loop back to step 1 with rejected_claims updated, max 2 retries per layer
5. If `validated: true` → advance layer counter, proceed to next iteration
6. If all 4 layers validated → emit final cascade JSON to frontend

---

**PATH B — If Lemma does NOT support conditional loops:**

Build the cascade as a **single orchestrator agent** that manages the loop internally. This agent:
- Is given the news event and user profile at the start
- Internally calls Brave Search as a tool
- Runs the Root Analyzer logic and Auditor logic as sequential tool calls within its own execution context
- Outputs the complete 4-node validated cascade as a single JSON object at the end

In Path B, the loop logic lives inside one LLM's reasoning, not in the Lemma workflow graph. This is less architecturally pure but fully functional and faster to build. Use Path B if Lemma's workflow engine cannot conditionally loop.

**Do not guess which path to use. Test it first.**

---

### 7.2 Cascade State Object (in-memory)

This is the JSON object that gets passed between steps. It is never persisted to a table.

```json
{
  "session_id": "uuid-generated-at-start",
  "news_event": "Apple acquires Company X for $3.2B",
  "user_profile": {
    "github_username": "crusty",
    "tech_stack": "Python, PyTorch, FastAPI",
    "job_target": "AI Product Engineer",
    "current_projects": "LLM fine-tuning pipeline, internal RAG tool",
    "upcoming_events": "Interview with Sarvam AI next week",
    "company_context": "Early-stage AI startup"
  },
  "layers": {
    "Macro": {
      "status": "pending|evaluating|auditing|confirmed|rejected",
      "claim": null,
      "search_query": null,
      "search_results": [],
      "confidence_score": null,
      "verified_sources": [],
      "evidence_extracted": null,
      "rejected_claims": []
    },
    "Micro": { ... },
    "Professional": { ... },
    "Personal": { ... }
  },
  "current_layer": "Macro",
  "completed": false
}
```

---

### 7.3 Layer Execution Sequence

For each layer in order `["Macro", "Micro", "Professional", "Personal"]`:

```
STEP 1 — Root Analyzer call
Input to agent:
  - news_event: string
  - current_layer: string
  - previous_nodes: array of confirmed claims from earlier layers
  - user_profile: full user_profiles record (only injected for Professional and Personal layers)
  - rejected_claims: array of previously rejected claims for this layer (starts empty)

Output from agent:
  - claim: string
  - search_query: string
  - reasoning: string

STEP 2 — Set layer status to "auditing", run Brave Search
Input to brave-search function:
  - query: the search_query from Step 1

Output:
  - results array with indices 0-4, each containing {index, title, snippet, url}

STEP 3 — Auditor call
Input to agent:
  - claim: from Step 1
  - node_id: "layer_{name}_node_{attempt_number}"
  - search_snippets: results array but WITH url FIELD STRIPPED — only pass {index, title, snippet}

Output from agent:
  - validated: boolean
  - confidence_score: 1-5
  - evidence_extracted: string
  - verified_source_ids: integer array

STEP 4 — URL Mapper
Input:
  - verified_source_ids: from Auditor output
  - search_results: the FULL results array (with URLs) from Step 2

Output:
  - verified_sources: [{title, url}] — real URLs, resolved from integer IDs

STEP 5 — Decision
IF validated == true:
  → Update layer status to "confirmed"
  → Store claim, confidence_score, verified_sources, evidence_extracted in layer object
  → Advance to next layer
  → Emit "layer_confirmed" event to frontend with full layer data

IF validated == false AND attempt_count < 3:
  → Add rejected claim to rejected_claims array for this layer
  → Update layer status to "rejected" briefly (for UI to show strikethrough)
  → Emit "layer_rejected" event to frontend with the rejected claim
  → Increment attempt_count
  → Return to STEP 1 for same layer

IF validated == false AND attempt_count >= 3:
  → Accept the highest-confidence attempt as the best available node
  → Mark with confidence_score from that attempt (even if low)
  → Advance to next layer
```

### 7.4 Elastic Layer Compression Rule

Some news events won't have a meaningful Micro or Professional consequence. The Auditor handles this implicitly: if a search query for a specific layer returns no relevant results (all snippets score ≤ 2), the Root Analyzer receives a special instruction:

```
"No supporting evidence found for [layer] layer. 
Either generate a directly verifiable consequence at this layer, 
or output { 'skip_layer': true, 'reason': 'No causal path' } 
to compress this layer and connect directly to the next."
```

If `skip_layer: true` is returned, the layer is visually collapsed in the UI with a dotted connector line and a "No direct causal path — layer compressed" label.

---

## 8. Frontend App — The Live Ticker UI

**File:** `apps/index.html`

**Deploy command:**
```bash
lemma apps deploy butterfly-effect ./apps/index.html
```

### 8.1 UI Layout

```
┌─────────────────────────────────────────────────────┐
│  🦋 BUTTERFLY EFFECT                    [New Event] │
├─────────────────────────────────────────────────────┤
│  News Event: [text input]              [Analyze →]  │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ● MACRO                    [pulsing / confirmed]   │
│  │ Competitor stock drops as market reprices        │
│  │ Confidence: ████░ 4/5  [reuters.com] [bloomberg] │
│  │                                                  │
│  ~~~ REJECTED: "Industry consolidation expected"    │
│  │   (struck through in red, remains visible)       │
│  │                                                  │
│  ↓                                                  │
│  ● MICRO                    [auditing — amber glow] │
│  │ ...validating...                                 │
│  │                                                  │
│  ↓                                                  │
│  ● PROFESSIONAL             [pending — gray]        │
│  ↓                                                  │
│  ● PERSONAL                 [pending — gray]        │
│                                                     │
│              [ Generate Morning Audio Brief v2 ]    │
│              (non-functional mock button)           │
└─────────────────────────────────────────────────────┘
```

### 8.2 Node Visual States

| State | Visual | Trigger |
|---|---|---|
| `pending` | Gray circle, dashed border, no content | Initial state |
| `evaluating` | Gray circle, slow pulse animation | Root Analyzer is generating |
| `auditing` | Amber background glow, scanning line animation | Auditor is running Brave Search |
| `rejected` | Red background, strikethrough text, remains visible | Auditor returned validated: false |
| `confirmed` | Solid border, full opacity, confidence bar shown | Auditor returned validated: true |
| `compressed` | Dotted connector, muted label "layer compressed" | Skip layer triggered |

### 8.3 Confidence Score Visual Encoding

Connector lines between nodes change style based on the downstream node's confidence score:

| Confidence | Line Style |
|---|---|
| 5 | Thick solid line (3px) |
| 4 | Solid line (2px) |
| 3 | Dashed line (2px) |
| 2 | Light dashed line (1px) with warning icon |
| 1 | Not shown (node would be rejected) |

Source citations appear as small linked chips below the confirmed node. Each chip shows the domain name only (e.g., "reuters.com"), not the full URL. Full URL opens on click.

### 8.4 Event Stream from Backend to Frontend

The frontend polls a Lemma App endpoint or listens to a stream for cascade state updates. Each event has this shape:

```json
{
  "event": "layer_evaluating|layer_auditing|layer_rejected|layer_confirmed|layer_compressed|cascade_complete",
  "layer": "Macro|Micro|Professional|Personal",
  "data": {
    "claim": "string",
    "confidence_score": 4,
    "evidence_extracted": "string",
    "verified_sources": [{"title": "Reuters Article", "url": "https://reuters.com/..."}],
    "rejected_claim": "string — only present on layer_rejected events"
  }
}
```

**Implementation note:** Use simple polling (every 1.5 seconds) against a Lemma-stored session state key if true streaming is not available via the Lemma App API. Do not over-engineer this. A polling interval of 1.5 seconds with visible state changes is sufficient for the demo effect.

---

## 9. The "Why Lemma" Defense — Hardcode These Arguments

When writing the submission document and demo script, these are your two architectural pillars:

### Pillar 1: Stateful Memory Isolation

In a standard LLM prompt chain, the model holds the original news event, all previous reasoning steps, the user's career context, and the verification logic simultaneously in one context window. This causes:
- Attention dilution over long chains
- The model conflating macro-level reasoning with personal-level reasoning
- Context pollution where an early hallucination colors all subsequent steps

In this Lemma build, each generation step is isolated. The Root Analyzer generating the Professional layer does not hold the Macro layer's search results in context. It receives exactly: the validated chain so far (claims only), the current layer target, and a fresh query to the user_profiles datastore. The context is surgically injected only when required. The Lemma datastore is the source of truth — it cannot drift or hallucinate user context the way an in-context summary can.

### Pillar 2: Programmatic State Transitions

An LLM prompt chain cannot:
- Pause mid-generation
- Spawn an external process (Brave Search API call)
- Wait for that process to return
- Evaluate the result against a deterministic rubric
- Modify the state machine based on that evaluation
- Resume from a different branch

Lemma's workflow orchestration layer provides the deterministic scaffolding for exactly these conditional state transitions. The loop, the retry logic, the layer advancement condition, and the URL mapping are all enforced programmatically — they cannot be hallucinated away.

---

## 10. Environment Variables Required

Create a `.env` file (or configure via Lemma settings):

```
NIM_API_KEY=your_nvidia_nim_api_key_here
BRAVE_API_KEY=your_brave_search_api_key_here
```

These must be accessible to Lemma functions as `process.env.NIM_API_KEY` and `process.env.BRAVE_API_KEY`.

---

## 11. Build Sequence — Follow This Exactly

### Day 1 — Foundation

1. Initialize Lemma pod: `lemma pod create butterfly-effect`
2. Create `user_profiles` table using schema in Section 4
3. Build and test `github-extractor.js` function in isolation — call it with your own GitHub username and verify the output is a clean tech stack string
4. Upload a sample PDF resume to Lemma Docs and test `resume-parser.js` — verify structured JSON extraction works
5. Build and test `brave-search.js` in isolation — call it with a test query and verify you get 5 results back with indices
6. **Critical test:** Attempt to build a simple two-step conditional workflow in Lemma — one step sets a value, a second step only runs if that value is above a threshold. Verify whether conditional branching works. Record the result. This determines Path A vs Path B for Day 2.
7. End of Day 1 target: `user_profiles` table populated with your own profile via the onboarding workflow

### Day 2 — Single Layer Loop

1. Build Root Analyzer agent with exact system prompt from Section 6.2
2. Build Auditor agent with exact system prompt from Section 6.3
3. Build `url-mapper.js`
4. Wire a single Macro layer cascade end to end:
   - News event input → Root Analyzer → Brave Search → Auditor → URL Mapper → console output
5. Test with 3 different real news headlines
6. Deliberately test a case where the Auditor should reject (ask Root Analyzer to generate a claim that is clearly false) — verify rejection fires correctly
7. End of Day 2 target: One-layer loop working reliably with real rejection and validation

### Day 3 — Full Four Layer Cascade

1. Extend the loop to all 4 layers in sequence
2. Add user_profile injection for Professional and Personal layers
3. Implement retry logic (max 2 retries per layer before accepting best attempt)
4. Implement elastic layer compression (skip_layer handling)
5. Test with real news events end to end
6. Verify Personal layer correctly incorporates user's actual context (upcoming events, tech stack, projects)
7. End of Day 3 target: Full 4-layer validated cascade running in under 90 seconds

### Day 4 — Frontend Connection

1. Build `apps/index.html` with all node states (pending, evaluating, auditing, rejected, confirmed)
2. Implement polling or streaming from cascade state to frontend
3. Implement confidence score visual encoding (line weights)
4. Show rejected nodes with red strikethrough — do not hide them
5. Add source citation chips under confirmed nodes
6. Add "Generate Morning Audio Brief v2" mock button (non-functional, styled as prominent CTA)
7. Deploy: `lemma apps deploy butterfly-effect ./apps/index.html`
8. End of Day 4 target: Full cascade visible in browser, all states rendering correctly

### Day 5 — Polish and Demo

1. Feature freeze — no new functionality
2. Test with 5 different real news events, different domains (tech, finance, geopolitics, open source)
3. Verify demo news event produces an impressive Personal layer
4. Optimize latency where possible (parallel Brave Search calls if Lemma supports async steps)
5. Record submission video:
   - Show the empty pod at start (proves it's live, not pre-built)
   - Input a real breaking news headline
   - Let the cascade run live — do not skip or fast-forward
   - Pause on each rejected node and explain what happened
   - Highlight the Personal layer as the climax
   - Show the source citations as proof of evidence-anchoring
6. Write technical submission document (use Pillars 1 and 2 from Section 9 verbatim)

---

## 12. Error Handling — Handle All Three of These

### 12.1 Brave Search returns no results

```javascript
if (!results || results.length === 0) {
  // Trigger elastic compression for this layer
  return { skip_layer: true, reason: 'No search results returned for query' };
}
```

### 12.2 NIM API rate limit or timeout

```javascript
// Wrap all NIM calls in retry with exponential backoff
async function callNIM(messages, retries = 2) {
  for (let i = 0; i <= retries; i++) {
    try {
      const response = await fetch(NIM_ENDPOINT, { ... });
      if (response.status === 429) {
        await sleep(2000 * (i + 1));
        continue;
      }
      return await response.json();
    } catch (e) {
      if (i === retries) throw e;
      await sleep(1000 * (i + 1));
    }
  }
}
```

### 12.3 JSON parse failure from LLM output

Both agents are prompted to return pure JSON. They will occasionally fail. Wrap all JSON.parse calls:

```javascript
function safeParseJSON(content, fallback) {
  try {
    // Strip any accidental markdown backticks
    const cleaned = content.replace(/```json|```/g, '').trim();
    return JSON.parse(cleaned);
  } catch (e) {
    console.error('JSON parse failed:', content);
    return fallback;
  }
}
```

For Root Analyzer failures: return a generic claim and trigger a retry.
For Auditor failures: return `{ validated: false, confidence_score: 1, verified_source_ids: [] }` and trigger a retry.

---

## 13. What NOT to Build

These are explicitly out of scope. Do not build them, do not start them, do not add placeholders that need wiring:

- Real TTS/audio generation pipeline
- cascade_graphs persistence table
- LinkedIn profile scraping
- Multi-user authentication
- Historical cascade storage or comparison
- Email or Slack surface integration
- Supabase or any external database
- A backend server outside of Lemma (everything runs inside Lemma functions and agents)

---

## 14. Demo Script (For Submission Video)

**Opening line:** "Most AI tools tell you what happened. Butterfly Effect tells you what happens next — and proves it."

**Sequence:**
1. Show empty `user_profiles` table in Lemma Data view — proves fresh state
2. Run onboarding: paste GitHub username, upload resume — show table populating in real time
3. Open the Butterfly Effect app URL
4. Type the news headline (pre-selected for a rich cascade — use an actual breaking story)
5. Hit Analyze — let it run live without narrating over it
6. When first rejection fires, pause and say: "Watch what just happened. The system generated a claim, searched the web, found no supporting evidence, struck it out, and generated a different path. That's not a prompt chain. That's a stateful verification loop."
7. When Personal layer confirms, pause and say: "This is where it gets specific. Not to 'developers' or 'AI companies' — to this user, this tech stack, this interview next week."
8. Show source citation chips — click one to prove the URL is real
9. End on the mock audio button: "The entire cascade can be compiled into a personalized morning commute brief. That's v2."

**Total video length target:** 4-5 minutes maximum.

---

## 15. Submission Write-Up Key Points

Lead with this framing: "This is not a news summarizer. It is a multi-agent causal reasoning engine with programmatic verification gates that cannot be replicated with a single system prompt."

The three things judges will remember:
1. The live node rejection — the AI catching itself in real time
2. The Personal layer — the cascade landing on something specific to the user
3. The stateful architecture — why Lemma is structurally required, not just convenient
