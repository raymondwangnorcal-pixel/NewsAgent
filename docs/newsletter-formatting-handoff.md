# Newsletter Formatting: Handoff Document

## Problem Statement

The Morning Briefing newsletter email needs improved readability. The original design had weak visual hierarchy — headlines and body text were nearly the same size and weight, section markers were too small, and the new "In This Briefing" headline index had spacing and alignment issues. After multiple rounds of fixes, two categories of problems remain: issues intrinsic to email-client rendering that require HTML workarounds, and index-section layout problems that haven't been fully resolved.

## Current State of the Code

All changes live on `main`, pushed to origin. The relevant file is `src/news_agent/mailer/render.py`. Six commits were made across two sessions (oldest first):

| Commit | Description |
|--------|-------------|
| `6d29662` | Added `_build_headline_index()` — the "In This Briefing" table-of-contents block. Strengthened section heading accent bars (3px/18px to 4px/20px), bumped label font from 11.5px to 12.5px, changed label color from `_SECONDARY` (#536471) to `_INK` (#0F1419). |
| `4ac8f1a` | Headline font 15px/600 to 19px/700. Body font 14.5px/1.55 to 14px/1.6. Headline-to-body margin 6px to 14px. Story card padding 16px to 22px. Section border under heading changed from 1px `_DIVIDER` to 1px accent color. |
| `b21b42b` | Added `scripts/rerender_send.py` — a tool to re-render stored newsletter content with current formatting code and send test emails. |
| `99025b7` | Fixed `rerender_send.py`: added `load_dotenv()` call so SMTP credentials load from `.env`. |
| `780aeb8` | Minor format fix. |
| `eef1422` | Wrapped headlines in `<b>` tags (the key fix for email-client bold rendering). Increased index accent dots from 4px to 6px. Changed index dot-to-label gap from 6px to 10px. Added `font-weight:600` to index headline spans. |

## What Worked

**Bold headlines via `<b>` tags.** Gmail and Apple Mail ignore inline `font-weight` CSS. The only reliable way to get bold text in email HTML is the `<b>` element. After adding `<b>` tags around both story headlines and index headlines in commit `eef1422`, bold rendering started working correctly in the user's email client. This is the single most impactful fix.

**Headline/body size contrast.** Going from 15px headlines / 14.5px body (~3% difference) to 19px / 14px (~36% difference) created clear visual separation between the two. The 14px margin between headline and body also helps.

**Section heading treatment.** The accent-colored underline on section headings (replacing the neutral divider) and slightly larger label text improved scannability.

## What Still Doesn't Work

### 1. Index accent dots may not render reliably

The "In This Briefing" index uses a nested-table approach for each row: a tiny `<td>` (6x6px, `border-radius:50%`, `background:{accent}`) renders a colored dot, with `font-size:0; line-height:0` and an `&nbsp;` to prevent cell collapse. This is fragile in email clients:

- Some clients collapse cells with `font-size:0` despite the `&nbsp;`.
- The first row ("Business + Tech") uses indigo `#4F46E5`, which may lack contrast against the `#F5F6F8` background — though the other dots (blue, teal, amber, purple) seem to render.
- The nested table (table-inside-td-inside-tr-inside-table) adds complexity that different email engines handle inconsistently.

**Hypothesis:** The dot in the first row may be rendering but invisible, or the first-row-of-table edge case may cause some clients to treat it differently. This needs testing across multiple email clients.

### 2. Index row spacing and alignment is uneven

The index layout uses a two-column table: column 1 holds a nested table (dot + uppercase category label), column 2 holds the headline text. The visual alignment between these columns looks uneven:

- `vertical-align:baseline` on both cells should align the text baselines, but the nested table in column 1 disrupts this — the baseline of a table is the baseline of its first row, which may not match the text baseline of the adjacent cell.
- The `width:1px; white-space:nowrap` on the label column forces it to shrink-wrap, but inconsistent label widths ("Business + Tech" vs "U.S. News") create a ragged left edge for the headline column.
- Row padding is `4px 0`, which may be too tight — the earlier "After" preview used `6px 0` and looked better.

### 3. `font-weight` in inline CSS is unreliable in email

This is a known constraint, not a bug: Gmail strips or ignores `font-weight` in inline styles. The `<b>` tag fix works for headlines, but any future element that needs bold (subheads, labels, etc.) must also use `<b>` or `<strong>` rather than relying on `font-weight` alone. The uppercase category labels in the index currently use `font-weight:700` without a `<b>` wrapper — they may appear un-bolded in some clients.

## Technical Constraints

**Email HTML is not web HTML.** The rendering engine is the email client (Gmail web, Gmail app, Apple Mail, Outlook, etc.), not a browser. Key constraints:

- No CSS variables, no flexbox, no grid — tables only for layout.
- Inline styles only; `<style>` blocks are stripped by most clients.
- `font-weight` in inline CSS is unreliable — use `<b>` tags.
- `border-radius` doesn't work in Outlook (dots will render as squares).
- `font-size:0` is a common hack to hide content in spacer cells, but some clients enforce a minimum font size (especially Outlook and some Android clients), which can cause hidden content to become visible or cells to expand unexpectedly.
- Gmail clips emails longer than ~102KB of HTML.

**Testing is slow.** Each test cycle requires: edit `render.py` on the Mac, run `python3 scripts/rerender_send.py` from the project root, wait for the email to arrive, and inspect it in the actual email client. The `--preview` flag writes HTML to a local file for browser inspection, but browser rendering differs significantly from email-client rendering, so it only catches structural issues.

**The local database is stale.** The SQLite DB at `data/email_state.db` on the dev machine only has editions through early August (edition 22). The production DB lives in the GitHub Actions environment. The rerender script works with whatever edition is available locally, which is fine for format testing but means the content is outdated.

## File Map

| File | Purpose |
|------|---------|
| `src/news_agent/mailer/render.py` | All newsletter HTML rendering. Contains `render_minimal_newsletter()`, `_build_headline_index()`, `_render_section()`, `_render_story_card()`, and helper functions. |
| `src/news_agent/formatting.py` | `FormattedMessage` dataclass and `CATEGORY_HEADERS` dict. |
| `src/news_agent/mailer/settings.py` | SMTP configuration from environment variables. |
| `src/news_agent/mailer/state.py` | SQLite state store — `EmailStateStore` class with `latest_editions()` and `edition()` methods. |
| `src/news_agent/env.py` | Custom dotenv loader. |
| `scripts/rerender_send.py` | Test tool: re-renders stored edition with current code and sends email. Flags: `--preview [FILE]`, `--edition N`, `--count N`, `--db PATH`. |

## Recommended Next Steps

### 1. Simplify the index dot rendering

Replace the nested-table dot approach with something more email-safe. Two options:

**Option A — Border-left on the label cell:**
```python
f'<td style="padding:4px 0; border-left:3px solid {accent}; padding-left:8px; ...'
f'{label}</td>'
```
This eliminates the inner table entirely. The colored left border acts as the category marker. More robust across email clients and simpler to maintain.

**Option B — Inline image dot:**
```python
f'<td style="width:8px; padding:4px 0; vertical-align:middle;">'
f'<div style="width:8px; height:8px; background:{accent}; border-radius:50%;'
f' mso-hide:all;"></div>'
f'<!--[if mso]><v:oval style="width:8px;height:8px" fillcolor="{accent}"'
f' stroked="f"/><![endif]--></td>'
```
This uses a `<div>` for modern clients and VML for Outlook. More complex but preserves the dot aesthetic.

Option A is recommended for its simplicity.

### 2. Fix index alignment

- Remove the nested table from column 1 entirely (see Option A above).
- Give the label column a fixed `width` (e.g., `width:140px`) instead of `width:1px; white-space:nowrap` so that all headline texts start at the same horizontal position regardless of label length.
- Consider increasing row padding back to `6px 0` for more breathing room.

### 3. Wrap category labels in `<b>` tags

The uppercase labels in both the index and section headings use `font-weight:700` inline — add `<b>` tags around them as well to ensure they render bold in Gmail/Apple Mail.

### 4. Test systematically

Use the rerender script to send a test after each change:
```bash
cd /path/to/NewsAgent
python3 scripts/rerender_send.py          # send to configured recipients
python3 scripts/rerender_send.py --preview # local HTML preview only
```

Check the result in Gmail (web and mobile) and Apple Mail at minimum. Consider also testing in Outlook if recipients use it.

### 5. Consider a simpler index layout

If alignment issues persist, consider replacing the two-column table index with a simpler single-column list where each row is:
```
BUSINESS + TECH — John Ternus succeeds Tim Cook as Apple's CEO...
```
Category label inline (bold, colored) followed by an em dash and the headline (regular weight). This avoids multi-column alignment entirely and is trivially robust across email clients.
