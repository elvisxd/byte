# Elvis R. Pino Bolívar

**Senior Full-Stack Engineer · AI Product Engineering**

10 years building full-stack software. Recent work: integrating large language
models and predictive systems into products running in production.

- Contacto: ver `build/cv-{en,es}.html` — el correo y el teléfono no se
  versionan acá para que este archivo pueda vivir en un repo público
- linkedin.com/in/elvis-pino · github.com/elvisxd · my-porfolio-next.vercel.app
- Orlando, Florida · Open to relocation & remote work · Authorized to work in the US

> **Fuente única.** Los PDF de `portfolio/public/`, los de Drive y los de
> `~/Downloads/cv-elvis/` salen de este archivo. Editar un PDF suelto es lo que
> dejó seis copias de distinto tamaño sin saber cuál se mandó.
>
> Los números que envejecen —tests, líneas, repos— se sacan con
> `uv run python perfil/actualizar.py`, no se escriben a mano: el PDF decía
> "183 tests" cuando ya eran 459.

## Profile

Full-stack engineer with 10 years of experience across TypeScript, React/Next.js,
.NET/C# and NestJS, and founder of a technical consultancy. I design and operate
systems where AI is a production component, not a demo: LLM integration via REST
API, prompts with empirically validated anti-hallucination constraints, and
statistical predictive models with out-of-sample validation.

**Design principle: the model explains, deterministic code decides.**

## Technical skills

| | |
|---|---|
| **AI & Data** | RAG (embeddings, pgvector, chunking, hybrid search, citations) · AI agents (tool calling, LangGraph state graphs, human-in-the-loop, sandboxed execution) · Evals and prompt evaluation with empirical measurement · LLM integration (Google Gemini API, Ollama) · LLM security (indirect prompt injection, untrusted content, OWASP LLM) · Statistical predictive modeling (ensembles, backtesting, out-of-sample validation) |
| **Frontend** | React, Next.js, TypeScript, JavaScript, React Native, Expo, Redux, Tailwind, Vue |
| **Backend** | NestJS, .NET/C#, FastAPI, Node.js, Express, GraphQL, REST, SSE/streaming, microservices, Python, PHP (Laravel), Java |
| **Data** | PostgreSQL, Redis, SQL Server, MySQL, MongoDB, Supabase, Azure SQL |
| **Cloud & DevOps** | Vercel, Railway, Azure/Azure DevOps, AWS, Docker, Git, CI/CD, Agile/Scrum, Jest |
| **Automation** | UiPath RPA, business process automation, third-party API integration |
| **Languages** | Spanish (native) · English (professional) |

## Experience

### Founder & Full-Stack Developer — Nesty C.A.
*2023 — Present · Remote · NestJS · Next.js · React Native · TypeScript · .NET/C#*

- Founded and lead a technical consultancy focused on automation, web/mobile
  development and API integration, owning the full cycle: client acquisition,
  requirements, architecture, delivery and support.
- Design automation solutions using AI models and microservices architecture.
- Architect scalable systems, setting maintainable code standards and review
  practices for AI-generated code.

### Internal Applications Developer — Walmart Inc.
*2022 — Present · Cocoa, FL · PHP · MySQL*

- Build and maintain internal web applications used by store staff.
- Streamline internal processes, reducing manual steps in recurring tasks.
- Provide technical support and continuous improvement for tools in active use.

### Full-Stack Developer — Freelance
*2022 — Present · Remote · React · Next.js · TypeScript · NestJS · PostgreSQL*

- Delivered 9+ web and mobile applications to production, including an app
  published on the Apple App Store.
- Built REST APIs with NestJS, PostgreSQL and Swagger, deployed to Railway and
  Vercel with CI/CD.
- Implemented authentication and authorization (Firebase, Supabase, JWT).

### Backend Developer — Freelance
*2020 — 2022 · Remote · .NET · C# · SQL Server · Entity Framework*

- Developed and maintained 15+ backend services and APIs.
- Designed database schemas prioritizing integrity and query performance.

### Software Engineer — IT Driver C.A.
*2017 — 2020 · Venezuela · PHP (Laravel) · MySQL · C# · Java · UiPath*

- Automated 20+ business processes with UiPath RPA.
- Built complete web applications plus desktop applications in C# and Java.

## Flagship project — AI-powered algorithmic trading platform

~283k lines of TypeScript · 380 files · 5 independent signal engines ·
+1.29% model edge out-of-sample · 59.4% directional accuracy (50.1% base rate)

- **Production LLM integration**: custom Google Gemini client over REST with
  timeout control and token budgeting; 5 specialized prompts across 3 services,
  with graceful degradation.
- **Data-driven prompt engineering**: measured whether each prompt criterion
  carries real predictive information and removed those that did not.
- **Custom predictive model**: weighted voting ensemble validated with
  permutation testing, Bonferroni correction and out-of-sample splits.
- **Quantitative rigor**: reverted my own best-performing signal (+1.76%) after
  auditing it against a 9,478-sample bull regime where it produced negative edge.

## Selected projects

### Byte — self-hosted AI agent · *in progress*
<!-- NÚMEROS: perfil/actualizar.py -->
A private agent running an open-source model locally (Ollama) that writes and
executes code in a WASM sandbox, searches the web, navigates a codebase, and
answers over my own documents with hybrid RAG on pgvector. Built as a LangGraph
state graph checkpointed in Postgres. Third-party content enters the prompt
tagged untrusted and, if code execution is then requested, the run halts for
human approval — the exact path an indirect prompt injection would take.
Connects to external tools over **MCP** and navigates a codebase (list, read,
grep) confined to one root that symlinks cannot escape.

It also runs two systems where the boundary between model and code is the whole
point. A **paper-trading agent** takes positions against five competing
hypotheses, writing its reasoning *before* the outcome is known and sealing it
with a hash of the context — measured, because an 8B model self-reported 1.43R
where the real figure was 17.35R, so the model chooses when and why, and the
code computes what happened. And a **job hunter** that scores openings from
official feeds with a rubric kept in version control, not in a prompt, so every
change to the criteria shows up in a diff. **822 tests** and an eval suite that
measures models against the tasks the agent actually performs.

`Python · FastAPI · LangGraph · Ollama · PostgreSQL + pgvector · Pyodide/WASM · MCP · SSE · Docker`

### Go190 Store · *App Store*
Full-stack mobile application published on the Apple App Store.
`React Native · Expo · NestJS · Firebase · Supabase`

### Sports & Gaming Prediction System · *in progress*
`TypeScript · Predictive modeling · Real-time data ingestion`

### Modern E-Commerce Store
`Next.js · TypeScript · Supabase · Tailwind`

## Education

- **B.Sc. Systems Engineering** — Universidad de Margarita (Unimar), 2012–2017
- **Computer Science Diploma** — María Auxiliadora II, 2008–2012

## Certifications

Python TOTAL with AI (Udemy, 2026) · Claude Academy: Claude 101 (Anthropic, 2026)
· Vibe Coding: Responsible AI-Assisted Development (DevTalles, 2026) · .NET
Backend (DevTalles, 2025) · NestJS (DevTalles, 2025) · React Native Expo
(DevTalles, 2025) · Meta Advanced React / React Basics / Back-End Development
(Coursera, 2022) · UiPath RPA Developer · freeCodeCamp
