---
name: human-readme
description: Creates new README.md files or simplifies existing ones so they are short, scannable and written for humans.
---

# Human README

Write READMEs that a **busy, easily distracted developer** can use.

**The goal:** in **~30 seconds** of skimming, the reader knows *what this is*, *why they'd care* and *how to start*. A full read takes **~2 minutes**, not twenty.

Think of the reader as a smart developer with **mild ADHD**: they're happy to read, but a wall of text makes them bounce. Short blocks, white space and bold words are what keep them on the page.

---

## 1. Pick the mode

- **README exists** → *simplify* it (section 4).
- **No README** → *create* one (section 3).

Either way, first **look at the project** for facts:

- Manifests: `package.json`, `composer.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `Gemfile`…
- Scripts / tasks: `Makefile`, `scripts` in the manifest, `justfile`, CI config
- Entry points, `.env.example`, `LICENSE`, `docs/`

**Never invent commands, flags, URLs or package names.** A README with one wrong install command is worse than a shorter one. If something is unknown, leave it out (or ask the user, if it's essential, e.g. the install command).

---

## 2. Default structure

Use only the sections that apply, in this order. Headings may be reworded to fit the project.

~~~markdown
# Project name

One line: **what it does**, for whom.

*(optional)* 1–3 badges, only if the README already had them.

## Why
- 1–3 bullets: the problem it solves / what makes it useful

## Quick start
```sh
install command
first run command
```

## Usage
1–2 short, real examples (code block + one sentence each)

## Configuration      ← only the 3–5 options people actually change
## Development        ← run tests / build, as commands
## License            ← one line
~~~

Keep **Quick start** copy-pasteable: the reader should get something running by pasting the blocks top to bottom.

---

## 3. Creating a new README

1. Collect facts from the project (section 1).
2. Fill the structure (section 2) with **only what you verified**.
3. Apply the style rules (section 5).
4. Write it to `README.md` at the project root.

---

## 4. Simplifying an existing README

Simplifying means **cutting**, not just reformatting. Removed content is **deleted** (git history keeps it) — don't move it to other files unless the user asks.

**Keep** anything a newcomer needs:

- what it is, install, first run, the main usage, required config
- license, security notes, breaking-change warnings
- links to existing external docs (keep the link, drop the duplicated text)

**Cut**:

- long intros, history, marketing filler (*"blazingly fast, revolutionary…"*)
- the same info said twice
- changelogs / release notes (link to `CHANGELOG.md` or releases if one exists)
- exhaustive option/API tables → keep the few common ones
- FAQs → keep only the 1–2 answers people really hit, as a short tip or warning
- outdated or broken badges, long badge rows
- TOCs for short READMEs (keep only if it's still long)

**Don't change meaning:** commands, versions, URLs and code stay exactly as they were. You rewrite the prose around them.

**Length target:** if the original is bloated, aim for **half the length or less**. If it's already short but dense, length may stay similar — the win is readability.

---

## 5. Style rules (summary)

Full rules with a bad-vs-good example: **`references/style.md`** — read it before writing.

- **Short paragraphs:** 1–3 sentences. One idea each.
- **Blank lines** between every paragraph, list, code block and heading.
- **Bullets** as soon as there are 3+ items.
- **Bold** the key term, command, number or warning — 1 to 3 per paragraph. Skimming only the bold should give the gist.
- *Italics* for nuance, examples and asides.
- **Links instead of explanations** for deep topics.
- **Descriptive headings** (*"Run it with Docker"*, not *"Misc"*).
- GitHub alerts (`> [!NOTE]`, `> [!WARNING]`) for the 1–2 things that would bite someone. Not more.
- Plain, friendly tone. No filler (*"It is worth noting that…"*). No emojis unless the README already uses them.

---

## 6. Report back

After saving, tell the user in **3–5 bullets** what you did, for example:

- **Cut:** changelog, 40-row option table, FAQ
- **Kept:** install, usage, Docker notes, license
- **Length:** 312 → 94 lines
- **Unverified:** anything you couldn't confirm from the code
