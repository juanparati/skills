# Style: short, airy, easy to scan

The reader is a developer with **mild ADHD**. They're smart and willing, but a wall of text loses them in the first lines.

**The aim:** skim in **30 seconds** and get the gist. Read fully in **~2 minutes** without getting lost.

**Short, not cryptic.** Cut words and sections, but keep each sentence complete and clear. Telegraphic fragments are as hard to read as walls of text.

---

## The rules

### 1. Lead with the point

The first line under the title says **what it is** and **who it's for**. No history, no "Welcome to…".

### 2. Use line breaks generously

- A **blank line** between every paragraph, list, code block and heading.
- **One idea per paragraph**, 1–3 sentences.
- **Bullets** as soon as there are 3+ items, steps or options.
- Short sentences. If a sentence has "and… which… so that…", split it.

### 3. Bold and italics guide the eye

- **Bold** the key term, the command, the number or the warning. Someone reading *only the bold* should get the skeleton.
- *Italics* for nuance, examples and asides.
- Never bold whole sentences. **1–3 bold spots per paragraph**, max.

### 4. Headings every few lines

Short `##` / `###` sections with **descriptive** headings: *"Run it with Docker"*, not *"Details"*. The reader should jump straight to their question.

### 5. Show, don't describe

A code block beats a paragraph. Prefer:

```sh
npm install -g mytool
mytool init
```

over *"To install, use npm to install the package globally, then run the init command."*

### 6. Link, don't explain

For deep topics (full API, architecture, every config option) **link** to the docs. The README is the front door, not the whole house.

### 7. Alerts only for what bites

Use GitHub alerts for the 1–2 things that would really hurt someone:

```markdown
> [!WARNING]
> **Back up your database** before running `migrate --fresh`.
```

More than two alerts and they become noise.

### 8. Friendly, direct tone

Write like a colleague at a whiteboard. Use "you". No filler (*"It is important to note that…"*, *"Simply…"*), no marketing (*"blazingly fast"*).

---

## Bad vs good

### Bad — dense and long

```markdown
## Installation
In order to get started with DataPipe you will first need to make sure that you have Python 3.10 or higher installed on your system, after which you can install the package using pip by running pip install datapipe, and if you want to use the optional PostgreSQL connector you should also install the extras with pip install datapipe[postgres], keeping in mind that you will need libpq on your system for this to work.
```

*Problems:* one giant sentence, commands buried in prose, the requirement and the warning hidden in the middle.

### Good — airy and scannable

````markdown
## Quick start

Requires **Python 3.10+**.

```sh
pip install datapipe
```

Using **PostgreSQL**? Install the extra:

```sh
pip install "datapipe[postgres]"
```

> [!NOTE]
> The Postgres extra needs **libpq** installed on your system.
````

---

## Self-check before saving

- [ ] First line says **what it is**?
- [ ] Quick start is **copy-pasteable** top to bottom?
- [ ] No paragraph over ~3 sentences / ~60 words?
- [ ] Blank lines between all blocks?
- [ ] Bold on key terms / commands / numbers — but not everywhere?
- [ ] Every command, URL and version **verified** (or kept exactly from the original)?
- [ ] Deep details **linked or cut**, not explained in full?
