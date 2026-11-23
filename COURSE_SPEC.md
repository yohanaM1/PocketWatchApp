# CMSC389A Modern Software Development with GenAI, Fall 2026, Final Project

---

## 1. Overview

The final project is a team-based software engineering project in which you will design, implement, test, secure, and deploy an AI-powered application. Over roughly nine weeks you will take a system from a written proposal to a running, publicly reachable deployment, and then defend it in a live demo.

The project should demonstrate that you can integrate LLMs and AI agents into a substantial software system — not that you can build a chatbot or call an LLM API.

> **The goal is not to build the most sophisticated AI model. The goal is to engineer a reliable software system that uses AI effectively.**

Everything this course has covered — RAG, tool calling, agentic workflows, MCP, full-stack development, databases, testing, security, Git/GitLab, Docker, and CI/CD — exists in this project for a reason. You are being graded on engineering, not on how impressive your idea sounds.

### What counts as an acceptable project

- AI is **one component** of a larger software system that also has a real frontend, backend, database, and deployment story.
- The AI does something the rest of your system can act on: retrieve, plan, call tools, produce structured output.
- Your system can tell when the AI is wrong, and does something about it.
- You can show, with numbers, that it works.

### What is **not** sufficient

- A prompt box that forwards text to an LLM and prints the reply.
- A RAG chatbot over a folder of PDFs, with nothing built on top of the answers.
- A project where the "database" is a JSON file.
- A project that only runs on your laptop.
- A polished UI wrapped around an AI component that was never evaluated.

---

## 2. Learning Objectives

By the end of this project you should be able to:

- Design a nontrivial software system and justify its architecture.
- Build and deploy a full-stack application.
- Design and use a relational database.
- Integrate LLM APIs into application code.
- Implement RAG and/or tool calling.
- Build an agentic workflow that plans, acts, and verifies.
- Evaluate AI-generated results quantitatively.
- Write automated tests at the unit, API, and behavioral level.
- Identify and mitigate security threats, including prompt injection.
- Containerize and deploy an application with CI/CD.
- Use Git/GitLab as a collaborating team, not as a backup drive.
- Document architectural and engineering decisions for another engineer.

---

## 3. Team Requirements

- **Teams of 2 students are recommended.**
- **Teams of 3 are allowed only for projects that are genuinely large and challenging**, and only with instructor approval at the proposal stage. A 3-person team is held to a visibly larger scope than a 2-person team.
- Solo projects are not the default. Talk to the instructor before assuming one.

Every student must have **identifiable ownership** of part of the system. A typical split:

| Student | Primary responsibility |
| ------- | ---------------------- |
| A       | Frontend + UX          |
| B       | Backend + database     |
| C       | AI/agent + evaluation  |

These roles are a starting point, not a wall. The actual requirement is:

> Every student must contribute substantial code and participate in design, testing, and documentation.

Your Git history must show that participation. Commit count alone is **not** the measure — a hundred one-line commits is not contribution, and one well-structured merge request can be. Each student also submits an individual contribution statement with the final deliverables.

---

## 4. Timeline and Milestones

| Milestone | What is due | Date |
| --- | --- | --- |
| **M1 — Proposal** | Finalized written proposal, approved by the instructor | **Friday, October 2, 2026** |
| **M2 — Vertical prototype** | End-to-end system running, however thin | **Thursday, October  29, 2026** |
| **M3 — Feature complete** | Core functionality done; testing/evaluation/security underway | **Thursday, November 19, 2026** |
| **Final submission** | Deployed system, repository, documentation, evaluation, report | **Tuesday, December 1, 2026** |
| **Demo** | Live presentation to the class | Final week — schedule announced in class |

Your proposal must be **finalized before October 2**, which means drafts and revisions happen during the last week of September. Start early; a proposal that arrives on October 2 with no prior discussion will usually need changes.

### M2 — Vertical prototype (October 29)

By this date the system must already work end-to-end:

```text
UI → Backend → Database → AI → Response
```

Functionality can be very limited. A single page, a single endpoint, a single table, and one real LLM call is enough — as long as a request genuinely travels the whole path.

This milestone exists to stop teams from spending a month building infrastructure with nothing running. If you are still "setting things up" on October 15, you are behind.

### M3 — Feature complete (November 19)

Core functionality is done. The remaining three weeks go to:

- testing
- AI evaluation
- security analysis and mitigation
- reliability and error handling
- UI polish
- deployment

### Final (December 1)

Deployed system, GitLab repository, documentation, evaluation results, final report, and demo materials. Note that **Thanksgiving break falls in the week before the deadline (November 25–29)** — do not plan to do real work that week.

**Milestone policy:** M1–M3 are required and graded satisfactory/unsatisfactory. Each missed or unsatisfactory milestone costs 5 points from your final project grade. They exist so you get feedback while feedback is still useful.

---

## 5. What Counts as an AI Project?

The distinction that matters:

> **AI is part of a software system, not the entire software system.**

| Too simple | Good project |
| --- | --- |
| Chatbot that answers questions about a PDF | AI incident-response system that investigates logs, Git history, and deployment data and produces a verified diagnosis |
| RAG chatbot over a collection of PDFs | Research assistant that retrieves papers, compares claims, and verifies citations against the source text |
| Upload a CSV, ask ChatGPT questions about it | AI data analyst that generates SQL, executes it, validates the results, and produces visualizations |
| "Summarize my meeting notes" | Meeting system that extracts decisions and action items, stores them, and creates tracked tasks you can query months later |

See **[project-ideas.md](project-ideas.md)** for worked examples. You are not restricted to that list — original ideas are encouraged, and the list is there to calibrate scope and ambition.

---

## 6. Project Requirements

### 6.1 Frontend

- React with TypeScript.
- A real interface with multiple views, not a single textarea.
- Handles loading, error, and partial-result states — AI calls are slow and sometimes fail, and your UI has to say so.

### 6.2 Backend

- FastAPI (Python), or an equivalent API framework approved at the proposal stage.
- A REST API or equivalent, with a documented set of endpoints.
- Application logic lives here. The frontend must not call the LLM provider directly.

### 6.3 Database

- PostgreSQL or MySQL.
- Real schema design: tables, keys, relationships, indexes where they matter, and migrations.
- Storing application state in JSON files instead of the database does not satisfy this requirement.

Document your schema in your README.

### 6.4 AI / LLM

The project must meaningfully use one or more of:

- an LLM
- RAG
- tool calling
- an AI agent
- structured generation
- embeddings / vector search

But:

> **A simple "prompt → LLM → response" application is insufficient.**

### 6.5 Agents and Tools

Your AI system must have at least **3 meaningful tools** that let it interact with data or external systems. Examples:

```text
search_database()
get_user_information()
run_analysis()
search_documents()
create_ticket()
run_tests()
inspect_repository()
get_deployment_history()
send_notification()
```

A tool is **meaningful** when:

> It performs an operation that cannot be replaced by simply including the same information in the prompt.

A "tool" that returns a hardcoded string, or that returns data you already pasted into the system prompt, does not count. Tools that query your database, hit an external API, run a computation, or change system state do.

Your proposal must list your tools and what each one does.

### 6.6 AI Reliability — *"What happens when the AI is wrong?"*

This is the requirement we care about most, and the one most projects underestimate.

Every project must implement **at least one mechanism for detecting or mitigating incorrect AI output**, and must be able to demonstrate it working. Acceptable mechanisms include:

- schema validation on structured output
- citation verification against source documents
- test execution against generated code
- human approval before a consequential action
- confidence thresholds with escalation
- tool-result validation
- consistency checking across multiple generations
- rule-based verification
- retrieval quality evaluation

Build this:

```text
LLM
 ↓
Generate SQL
 ↓
Validate SQL
 ↓
Execute
 ↓
Check result
 ↓
Return answer
```

Not this:

```text
LLM
 ↓
Trust answer
```

A good heuristic while you design: **your project should contain something that fails if the AI is wrong.** If a wrong answer from the model produces no visible consequence anywhere in your system, you have not built a reliability mechanism — you have built a text box.

### 6.7 Evaluation

You must define **at least one quantitative evaluation metric** and report results on a test dataset or a set of test scenarios. Candidates:

- accuracy
- hallucination rate
- retrieval precision / recall
- task completion rate
- tool-selection accuracy
- code-generation success rate
- classification accuracy
- response time
- cost per request
- human satisfaction ratings

**This does not need to be a research project.** 20–50 carefully constructed test cases is a reasonable target. What matters is that the cases are real, that you built them deliberately, and that the number you report means something.

Report your results in your README and in your final report, and explain what the number does *not* tell you.

### 6.8 Testing

Automated tests are required at three levels:

**Backend**
- unit tests for business logic
- API tests for endpoints
- database tests

**Frontend**
- at least some component or integration tests

**AI**
- evaluation/test cases for AI behavior (these overlap with §6.7)

You must also be able to answer, in your documentation:

> What kinds of failures are caught by your tests?

Tests must run in CI.

### 6.9 Security

Identify the threats relevant to *your* application and address them. Common ones:

- authentication and authorization
- SQL injection
- XSS
- CSRF
- **prompt injection**
- sensitive information disclosure
- insecure tool execution
- excessive agent permissions
- API key protection
- malicious uploaded documents

**Prompt injection analysis is mandatory for any project involving agents or RAG** — which means nearly all of them. You must demonstrate **at least one concrete attack against your own system and your mitigation for it.** Show the attack input, what it did before the mitigation, and what it does after.

Authentication and authorization are required. An agent with tools must not be able to reach data the current user is not allowed to see.

### 6.10 Architecture

Produce an architecture diagram, for example:

```text
                  ┌───────────────┐
                  │ React/TS UI   │
                  └───────┬───────┘
                          │
                          ▼
                  ┌───────────────┐
                  │    FastAPI    │
                  └───────┬───────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
          PostgreSQL     Agent      External API
                          │
                  ┌───────┼───────┐
                  ▼       ▼       ▼
                Tool 1  Tool 2  Tool 3
```

As a rule of thumb: if component A sends messages to component B, draw an arrow from A to B.

Alongside the diagram, explain your components, data flow, AI architecture, database architecture, external services, authentication, and deployment.

### 6.11 Docker

```text
docker compose up
```

must start your frontend, backend, and database. Document any setup steps (environment variables, seed data, migrations) in your README.

This is not optional — it is how your project gets graded without a two-hour setup session.

### 6.12 CI/CD

Configure GitLab CI to run on push:

```text
git push
   ↓
CI
   ├── lint
   ├── tests
   ├── build
   └── security checks
```

Automated deployment is encouraged but not required. A reproducible build is.

### 6.13 Deployment

A deployed, reachable version of your application is required. You choose the infrastructure:

- UMD VM
- a cloud VM
- Render, Railway, Fly.io
- AWS, Azure, GCP
- any other platform approved at the proposal stage

The requirement is simply:

> The instructor and TAs must be able to access and evaluate the application without reproducing your entire development environment.

Keep costs in mind and use free tiers where you can. If your deployment needs credentials to log in, provide a test account.

---

## 7. Project Proposal

**Due: Friday, October 2, 2026. Maximum 3 pages.**

Do not spend four weeks building the wrong thing. The proposal is where scope gets fixed, and it is where the instructor can still save you.

### Audience

Write for a technical reader who may not know your domain. If you are building a security tool, assume your reader can code but has never written a static analyzer. As you write each section, ask what questions a reader would have — and answer them there, rather than leaving them to ask you later. Too much detail is better than too little, but do not explain what an API is.

### Required contents

1. **Project name**
2. **Team members** — and whether you are a team of 2 or 3
3. **Problem statement** — what problem, for whom, and why it is worth solving
4. **Target users**
5. **Pitch** — 1–2 clear sentences on what the system does and who benefits. No implementation details, no programming languages. This is your first impression; write it last and rewrite it twice.
6. **Why AI is necessary or useful here** — what breaks if you remove the AI?
7. **Main features** — 5–10 specific actions a user can complete. Each should stand alone and say *what* the user can do, not *how*. Write "Users can compare two research papers," not "Users can click the compare button to open a side-by-side view." Scope this list against the nine weeks you actually have.
8. **AI architecture** — retrieval, prompting, agent loop, structured output, and where verification happens
9. **Tools** — your 3+ tools, what each does, and why each is meaningful under §6.5
10. **Database design** — tables and relationships
11. **Technology stack** — and, for each major component, why that choice given your team's skills
12. **Reliability plan** — what happens when the AI is wrong (§6.6)
13. **Evaluation plan** — your metric, how you will build test cases, and what result would count as success
14. **Security considerations** — including your prompt injection threat model
15. **Risks** — at least three things that could go wrong, each with a plan: how you would resolve it, the expected schedule impact, and how you would adjust. "Ask the instructor for help" is a legitimate plan for one risk, not for all three.
16. **Teamwork** — one concrete way you will reduce friction across your development environments (and why it fits your team), plus how you will divide the work. Assign pieces of the project, not individual methods.
17. **Timeline** — your own task breakdown against the four milestones in §4, with at least two tasks per week

Scheduling is genuinely hard and you will not stick to your schedule perfectly. That is expected and is not graded as a failure. What is graded is whether the schedule is balanced and whether you adjusted it honestly as things changed.

---

## 8. Git and GitLab Requirements

- A GitLab repository, instructor created and shares it with you and TAs
- Meaningful commit messages and history
- Feature branches (or an equivalent documented workflow)
- Merge requests — **at least one MR reviewed by another team member for each major feature**
- Issue tracking used for real work
- `README.md` (see §9)
- `.gitignore`
- `.env.example` documenting every environment variable
- **No API keys, credentials, or secrets committed — ever.** If you commit one, rotate it immediately and say so in your final report.

Merge request review is where the collaborative engineering actually happens. Reviewing your teammate's code is part of your grade, not a favor you do them.

---

## 9. Documentation

Your repository README must follow this structure:

```text
# Project Name

## Team Members

## Problem

## Features

## Architecture

## Technology Stack

## AI Architecture

## Tools

## Database

## Installation

## Running Locally

## Deployment

## Testing

## Evaluation

## Security

## Limitations

## Future Work
```

**Limitations** is the section that matters most for an AI project, and it is the one most teams write badly. Answer directly:

> Where does your system fail?

Name the inputs that break it, the cases your evaluation does not cover, and the failure modes you know about and did not fix. An honest limitations section raises your grade. A limitations section that says "the system works well in all tested scenarios" lowers it.

---

## 10. AI Usage Policy

**You are expected to use AI coding and development tools on this project.** That is the subject of the course. Using Claude Code, Copilot, Cursor, or anything comparable is encouraged.

However:

> You are responsible for all code you submit and must be able to explain how your system works.

Submit an `AI-USAGE.md` documenting:

- which AI tools you used
- how you used them
- where AI-generated code was incorporated
- how you verified AI-generated code before merging it
- **significant AI failures you encountered** — bugs it introduced, confident wrong answers, code that passed review and broke later

That last item is graded, and graded generously. It is the point of the course.

---

## 11. Final Deliverables

Due **Tuesday, December 1, 2026**:

- [ ] GitLab repository (instructor and TAs have access)
- [ ] Deployed, reachable application (with a test account if login is required)
- [ ] Docker configuration — `docker compose up` works from a clean clone
- [ ] README following the §9 structure
- [ ] Architecture diagram
- [ ] Automated tests, running in CI
- [ ] AI evaluation with reported results
- [ ] Security analysis, including a demonstrated prompt injection attack and mitigation
- [ ] `AI-USAGE.md`
- [ ] Final report
- [ ] Presentation / demo materials
- [ ] Individual contribution statement from each team member

---

## 12. Final Presentation and Demo

**10–15 minutes per team**, structured:

| Time | Section | Content |
| --- | --- | --- |
| 2 min | **Problem** | What problem are you solving, and for whom? |
| 2 min | **Architecture** | How does the system work? |
| 5 min | **Live demo** | An actual workflow, running against your deployment |
| 2 min | **AI** | Your AI/agent architecture and tools |
| 2 min | **Evaluation & security** | How you tested it, your metric and result, and what happens when the AI fails |

Followed by questions from the instructor and the class.

The live demo is the centerpiece — this is a demo, not a slide deck. **Show your system failing safely at least once**: trigger your reliability mechanism, or run your prompt injection attack against your mitigation. A demo that only shows the happy path tells us nothing about your engineering.

Record a backup video. Live demos break.

---

## 13. Grading Rubric

| Category | Points |
| ------------------------------------ | ------: |
| Functionality & completeness         |      20 |
| Software architecture                |      15 |
| AI/agent integration                 |      15 |
| AI reliability & evaluation          |      10 |
| Testing                              |      10 |
| Security                             |      10 |
| Code quality & engineering practices |      10 |
| Deployment & DevOps                  |       5 |
| Documentation                        |       5 |
| **Total**                            | **100** |

Missed or unsatisfactory milestones (§4): **−5 points each.**

Two consequences of this rubric, stated plainly:

> A technically ambitious idea that does not work reliably will not receive a high score simply because the idea is ambitious.

> A smaller project with excellent engineering, testing, evaluation, and reliability can outperform a much larger project that is incomplete.

Choose your scope accordingly.

---

## 14. What Makes a Project Ambitious?

Use these five dimensions when you are deciding whether your idea is big enough — and when the instructor is deciding whether to approve it:

**Scale** — Does the system have multiple meaningful components?

**Intelligence** — Does the AI perform reasoning, retrieval, planning, or tool use?

**Engineering** — Is there a real backend, database, API, test suite, and deployment?

**Reliability** — Does the system detect and handle AI errors?

**Evaluation** — Can you demonstrate objectively that it works?

> You do not need to maximize every dimension. Your project should be substantial in several of them.

A project that scores low on *all five* will not be approved. A project that scores very high on reliability and evaluation but is modest in scale is a perfectly good CMSC389A project.

---

## 15. FAQ

**Can I work alone?**
Not by default. Talk to the instructor first.

**Can we be a team of 3?**
Yes, if your project is large and challenging enough to justify it, and the instructor approves it at the proposal stage. Your scope expectations go up accordingly.

**Can we use a stack other than React/FastAPI/PostgreSQL?**
Propose it in §7.11 with a justification. Reasonable alternatives are usually approved. Decide before October 2, not in November.

**Can we use a framework like LangChain, LlamaIndex, or an MCP server?**
Yes. You still have to explain what it is doing and remain responsible for the behavior of your system.

**Which LLM provider should we use?**
Use the LLM API key we provided or any other LLM provided you have access to. Budget for API costs, cache aggressively during development, and never commit your keys. If cost is a problem, raise it early rather than quietly scaling down your evaluation.

**Does a vector database count as our database requirement?**
No. You need a relational database (§6.3). A vector store is in addition to it, not instead of it.

**Our evaluation numbers came out bad. Should we hide that?**
No. Report them, explain why, and describe what you would change. Honest negative results score well. Fabricated or unexamined positive results score very badly — and are easy to spot at the demo.

**How much should we build ourselves versus generate with AI?**
Generate as much as you want. Understand all of it. See §10.

**Something is going wrong and we are behind.**
Tell the instructor at the next milestone, not on December 1. Milestones exist precisely so that a struggling project can be rescoped while there is still time.
