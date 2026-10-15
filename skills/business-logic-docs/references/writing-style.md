# Writing style: easy to read, never condensed

The readers want to understand the business, and some of them have mild ADHD. A wall of text loses them in the first lines. Dense, "efficient" prose that a developer might like is the wrong goal here.

**The aim:** a reader can skim a note in 30 seconds and get the gist, then read it fully in 3 minutes without getting lost.

**Focused, not condensed.** Each note covers **one** topic. Inside that note, explain it fully, with space to breathe. If a note grows too long, split it into two linked notes. Don't squeeze it.

---

## The rules

### 1. Start with a TL;DR

Every note opens with a `> [!tldr] TL;DR` callout: 2–3 short lines saying what this is and why it matters. Someone who reads only this should still learn something true.

### 2. Use line breaks generously

- Put a **blank line** between every paragraph, list and callout.
- **One idea per paragraph.** 1–3 sentences max.
- Prefer a bullet list as soon as there are 3+ items, steps or conditions.
- Keep sentences short. If a sentence has "and… which… so that…", split it.

### 3. Use bold and italics to guide the eye

- **Bold** the key term, the outcome or the number the reader must remember. A reader skimming only the bold words should get the skeleton of the note.
- *Italics* for nuance, examples, asides and "watch out" phrasing.
- Don't bold whole sentences. Bold loses its power when everything is bold — 1 to 3 bold spots per paragraph at most.

### 4. Headings every few lines

Short `##` / `###` sections with descriptive headings (*"What happens when a payment fails"*, not *"Details"*). A reader should be able to jump straight to their question.

### 5. Concrete beats abstract

Always give a real-world example with names and numbers.

> *Example: Ana submits a loan application at 10:02. At 10:07 the same phone number submits again → the second one is **ignored** because it falls inside the **10-minute** window.*

### 6. Explain the *why*

A rule without its reason is hard to remember. When the code, a comment or a commit tells you why, say it: *"…because buyers were being charged twice for the same person."*

### 7. Business words, not code words

Say **"the lead is sold to a buyer"**, not *"`LeadProcessor::dispatch()` sets `status=SOLD`"*. Code names go in the collapsed *Where in the code* section and in the glossary aliases.

When a business term first appears, link it to the glossary: `[[Lead]]`.

### 8. Draw it

Use a mermaid diagram whenever there is:

- a **process** with steps or decisions → `flowchart TD`
- an **interaction** between actors/systems → `sequenceDiagram`
- a **lifecycle** of statuses → `stateDiagram-v2`

Introduce each diagram with one sentence, and keep it to roughly ≤ 12 nodes. Split bigger processes into linked notes.

### 9. Use callouts for things that matter

- `> [!tip]` — useful to know
- `> [!warning]` — surprising behavior, edge cases, things that go wrong
- `> [!example]` — a worked example
- `> [!question] Unclear` — something the code doesn't explain; say what's unknown
- `> [!info]- Where in the code` — collapsed technical details (the `-` collapses it)

### 10. Friendly, direct tone

Write like a helpful colleague explaining at a whiteboard. Use "you" when it helps. No filler like "It is important to note that…".

---

## Bad vs good

### Bad — condensed, code-centric

```markdown
## Duplicates
The ApplicationService checks for duplicate telemarketing applications by phone and email within a configurable window (config telemarketing.duplicate_window, default 600s) and if a match is found with status not rejected the new application is marked ignored and not forwarded to buyers, which avoids double billing; the window can be overridden per campaign.
```

Problems: one long sentence, code names in the text, no example, no emphasis, the reason buried at the end.

### Good — airy, business-first

```markdown
## Duplicate applications

Sometimes **the same person applies twice** in a short time — for example, they press "send" again, or a call center agent re-enters them.

To avoid this, a new application is **ignored** when:

- it has the **same phone or email** as an earlier application, *and*
- the earlier one arrived **less than 10 minutes ago**, *and*
- the earlier one was **not rejected**.

> [!example]
> *Ana applies at 10:02. A second application with her phone arrives at 10:07.*
> → The second one is **ignored** and **never sent to buyers**.

**Why?** Without this, buyers would be **charged twice** for the same person.

> [!tip]
> The 10-minute window is the default. **Each campaign can set its own window.**
```

---

## Quick self-check before saving a note

- [ ] TL;DR at the top?
- [ ] No paragraph longer than ~3 sentences / ~60 words?
- [ ] Blank lines between blocks?
- [ ] Bold on key terms/numbers, italics for examples and nuance?
- [ ] At least one concrete example?
- [ ] Diagram if there's a flow or lifecycle?
- [ ] The *why* stated (or flagged as Unclear)?
- [ ] Code names only in *Where in the code* and glossary aliases?
