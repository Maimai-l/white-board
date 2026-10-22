# Changelog style guide

This guide applies to `CHANGELOG.md`. It does not cover commit messages or the design documents under `docs/`.

## 1. Purpose and readers

The changelog tells a user what changed between two releases and whether they need to act. It is not a development log. Readers are:

- users deciding whether to update, who read the in-app update dialog;
- developers who embed inksync and need to know about interface changes.

An entry describes an effect a user can observe or an action a user must take. Root causes, investigation steps and implementation details belong in commit messages and design documents.

## 2. Language

- `CHANGELOG.md` is written in English only. It is the single source for the GitHub release notes and the in-app update dialog (`packaging/release_notes.py`). There is no translated changelog.
- Use plain, formal English. Write complete sentences with a subject and a verb.
- Do not use Chinese anywhere in the file, including interface labels. Refer to a control by its English name from the glossary in section 7. If a control has no entry yet, add one before using it.
- Do not use contractions (`doesn't`, `can't`), exclamation marks, emoji, rhetorical questions, or first and second person (`we`, `our`, `you`). Describe the product, not the people who use it.
- Do not use informal words or vague intensifiers. The table below lists common cases.

| Avoid | Use instead |
| --- | --- |
| just, simply, basically, actually, really | (omit) |
| now works, works properly | state the behavior: `exports the erased area as empty` |
| broken, weird, messed up | state the symptom: `appeared inverted`, `was not saved` |
| a bunch of, lots of | a number, or `several`, `many` |
| kicks in, shows up, pops up | `takes effect`, `appears`, `opens` |
| super, very | (omit, or give a measured value) |
| stuff, things | name the object |
| hopefully, should now | (omit; state what the release does) |

## 3. Version sections

```
## 1.2.0 (2026-10-22)

### Upgrade notes
### Added
### Changed
### Fixed
### Security
### Removed
```

- Heading: `## <version> (<YYYY-MM-DD>)`. The date is the release date in UTC. A version that has not been released yet uses `## <version> (unreleased)`.
- Use only the subsections listed above, in that order, and omit empty ones.
  - **Upgrade notes**: anything the user must do or know before updating (storage migration, no downgrade, an interface that is not backward compatible). Every incompatible change appears here, even if it is also listed elsewhere.
  - **Added**: new features.
  - **Changed**: changes to existing behavior, including performance improvements.
  - **Fixed**: defects that are corrected.
  - **Security**: vulnerabilities that are corrected. A security fix is listed here and not under Fixed.
  - **Removed**: features or options that no longer exist.
- An optional summary of one or two sentences may follow the heading, before the first subsection. Use it only for a release with a central theme (for example 1.1.0).
- Prereleases (`1.2.0-rc.1`) have no section of their own; they use the section of the release.

## 4. Entries

### 4.1 Form

- Each entry is one list item on a single line. The update dialog displays the text line by line and does not wrap list items into paragraphs.
- An entry is one or two sentences, at most 200 characters including the area prefix (section 4.2). Split a longer entry into several entries or move the detail to a document and link it.
- Every sentence ends with a period.
- Write in the present tense and describe the behavior of the new version.

### 4.2 Area prefix

Start every entry with the area it affects, followed by a colon. Use one of:

`iPad`, `Mac`, `Boards`, `Eraser`, `Tool picker`, `Documents`, `Export`, `Sketch`, `Sync`, `Storage`, `Settings`, `Updates`, `Extension`, `Embedding`, `inksync`.

Add an area to this list before using a new one. Do not combine two areas in one prefix.

### 4.3 Wording by subsection

- **Added**: name the feature and what it lets the user do.
  - `Sketch: Write on the iPad and copy the result on the Mac as a PNG with a transparent background.`
- **Changed**: state the new behavior; mention the old behavior only when the user would otherwise not notice the difference.
  - `Eraser: The pixel eraser width follows the Pencil angle during a stroke, as it did before 0.9.32.`
- **Fixed**: state the symptom the user saw, in the past tense, then nothing else. The fix itself is implied.
  - `Export: Pixel-eraser masks on the second and later pages of a PDF appeared inverted.`
  - Not: `Fixed a bug where ...`, `Now correctly ...`, or a description of the cause.
- **Security**: state who could do what before the fix, then the protection in place. Do not include details that would help reproduce the attack.
  - `Sync: Web pages open in a browser on the Mac could connect to the board server. The server now rejects requests from other origins.`
- **Upgrade notes**: state the required action or consequence as an instruction or a fact.
  - `Storage: The storage directory is converted on first start and cannot be used by 1.0.x afterwards.`

### 4.4 Content rules

- One change per entry. If one fix resolves two symptoms, write two entries or one entry naming both symptoms.
- Do not mention internal names (CSS classes, function names, test names, commit hashes) unless they are part of a public interface. Public interfaces (inksync functions, configuration keys, HTTP endpoints, file names) are written in backticks: `mount`, `allowed_hosts`, `POST /api/sketch`, `index.sqlite`.
- When a defect was present in earlier releases, add `Present since <version>.` as the last sentence only if it helps users judge whether their older exports or data are affected.
- Refer to documentation by relative path: `See docs/sketch.md.`
- Numbers: use digits for measured values and units with a space (`0.5 s`, `4096 px`, `20,000 boards`).
- Product names: `iPad`, `Apple Pencil` (first mention in a section), `Pencil` (afterwards), `Mac`, `Edge`, `Chrome`, `PDF`, `PNG`, `inksync`.

## 5. Order within a subsection

1. Changes that affect every user, before those that affect a single feature.
2. Within the same reach, larger effects first.
3. `inksync` entries last, since they concern developers rather than users of the app.

## 6. Review checklist

`tests/test_release_notes.py` checks the items marked (automatic) on every CI run. Before a release, check each entry against all items:

- [ ] Area prefix from the list in section 4.2. (automatic)
- [ ] One line, at most 200 characters, ends with a period. (automatic)
- [ ] No Chinese characters (`grep -nP '[\x{4E00}-\x{9FFF}]' CHANGELOG.md` returns nothing). (automatic)
- [ ] No word from the table in section 2.
- [ ] Fixed entries describe the symptom, not the cause or the code.
- [ ] Incompatible changes are repeated under Upgrade notes.
- [ ] Version headings and subsection names follow section 3. (automatic)

## 7. Glossary of interface labels

The app interface is in Chinese. Entries refer to controls by these English names.

| Interface label | English name |
| --- | --- |
| 白板 | Boards |
| 管理白板 | Manage boards (permission) |
| 存储目录 | Storage directory |
| 笔具盘 | Tool picker |
| 更多 | More |
| 自动最小化 | Auto-minimize |
| 用手指绘图 | Draw with finger |
| 对象橡皮擦 | Object eraser |
| 像素橡皮擦 | Pixel eraser |
| 速写 | Sketch |
| 清空 | Clear |
| 复制 | Copy |
| iPad 首页 | iPad home app |
| 来源 | Source (iPad shell setting) |
| 仍然编辑 | Edit anyway |
| 打开方式 (Finder) | Open With |

## 8. Example

The sections of `CHANGELOG.md` from 1.0.0 onwards follow this guide; the 1.2.0 section shows every subsection in use except Removed.
