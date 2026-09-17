# Website subscribers

The public signup form lives at `https://gaplesslabs.com/newsagent`. Its Vercel endpoint writes to Supabase. NewsAgent uses the same project to read active subscriptions and sends through the existing Gmail SMTP implementation. No subscriber database contents or secret keys belong in Git.

## Configuration

Set `SUPABASE_URL` and `SUPABASE_SECRET_KEY` as GitHub Actions secrets. Use a separate Supabase secret key from the website's key. `NEWSAGENT_SUBSCRIBERS_ENABLED=true` is a repository **variable**, not a secret. The default is false, preserving the existing EMAIL_TO recipient behavior until rollout.

When enabled, production editions use only active Supabase recipients. Database failure stops the run rather than falling back to stale EMAIL_TO. An empty list exits before generating a briefing. Each outgoing production email receives its own unsubscribe link; the stored edition is unchanged. Active membership/token is checked again immediately before delivery; an already in-flight SMTP transaction cannot be recalled. Successful production delivery refreshes a readiness timestamp that lets the website accept signups for the following 48 hours. If sends stop succeeding, signup eventually closes until the problem is fixed.

Test revisions, gate alerts, and the manual rerender script go only to `EMAIL_ADMIN_TO`, or `EMAIL_FROM` when no administrator list is configured. EMAIL_TO remains required by the legacy SMTP settings loader during this transition. Do not delete it yet.

## Rollout (no immediate mailing)

1. Apply both Gapless Labs Supabase migrations. The user reports both succeeded on 2026-09-17.
2. Deploy the Gapless Labs website with its new endpoint and unsubscribe UI. Signup remains closed because sender readiness starts empty. This must happen before enabling the sender, so new outgoing unsubscribe links have a working destination.
3. Merge/deploy this NewsAgent code with the subscriber variable still false. In Actions run **Manage subscribers (no email)** with `operation=check` to verify credentials and schema.
4. Run the same workflow with `operation=import-existing`. It imports the existing EMAIL_TO list, skips duplicate addresses, preserves existing opt-outs, and prints counts only. It does not send mail or set readiness.
5. Confirm the active count and set `NEWSAGENT_SUBSCRIBERS_ENABLED` to `true` in repository Actions variables. Optionally set `EMAIL_ADMIN_TO` as an Actions secret; otherwise the sender receives administrative/test messages.
6. Run the management workflow with `operation=mark-ready`. It checks the active list and opens signup without generating or sending mail. Normal daily production runs refresh readiness.
7. Verify a controlled signup appears active, duplicate signup creates no second row, and the unsubscribe link marks that controlled address inactive. Do not run the normal Morning briefing workflow just to test signup: it sends to subscribers.

The management command is also available as `python -m news_agent.mailer.subscribers` with `--import-existing` or `--mark-ready`. It reads environment variables directly; it does not load a local .env or print addresses/tokens. A manual check can validate an empty list before import.

## Failure and rollback

To pause delivery, disable the Morning briefing workflow. After adopting the database, do not switch back to EMAIL_TO unless it has been reconciled with all opt-outs; the original list can contain people who unsubscribed. Close signup immediately by setting `public.newsagent_sender_status.checked_at` to null in Supabase. Fix the connection, then use the no-send check and readiness workflow to restore operation.

## Verification

Run `python -m pytest tests/test_subscribers.py tests/test_rerender_send.py -q`. No test sends real email. At implementation, these 15 checks passed. The complete suite had 470 passing tests and seven pre-existing HTML-rendering assertion failures, identical to the unchanged baseline (458 passing, same seven failures). Rendering code was not changed by this feature.
