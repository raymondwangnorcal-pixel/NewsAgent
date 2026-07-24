# NewsAgent: Folk-Style Messaging and Assistant Pivot

## Objective

Evolve NewsAgent from a one-way daily news text into a low-cost, conversational personal news assistant. It should send one curated briefing each morning through messaging or push channels and let each subscriber reply in the same channel for one-to-one explanations, deeper context, preference changes, and limited live-news questions.

The product should feel like a familiar contact on a user's phone rather than a generic broadcast feed. The separate personalized email-newsletter product is documented in [`mobile-split-email.md`](mobile-split-email.md) and is not a delivery channel for this Folk-style product.

## Original Message Format and SMS Economics

The initial proposed format was:

- Five sections: Business + Tech, U.S. News, Global News, Culture + Media, Finance.
- Three blurbs per section.
- Roughly 35 words per blurb.
- 15 blurbs / roughly 525 words per edition.
- One shared edition generated each day and sent to 100 users.

For U.S. SMS, the AI generation cost is small because the edition is generated once. The material cost is SMS delivery: providers bill by message segment and carriers charge additional pass-through fees.

### SMS segmentation

- GSM-7 SMS: 160 characters for one segment; 153 characters per segment when concatenated.
- Any emoji, smart quote, em dash, or other non-GSM character can force UCS-2 encoding: 70 characters for one segment and 67 per concatenated segment.
- Removing emojis and enforcing GSM-safe punctuation matters substantially for SMS cost.
- A 35-word blurb is typically around 180-230 characters, so it usually becomes two GSM-7 segments.

### Original 15-blurb estimate with Twilio

- Approximately 30 segments per user/day.
- 90,000 segments/month for 100 users.
- Estimated total: about $1,060-$1,200/month, before meaningful AI/search/hosting overhead.

This is not viable at a $1.50/month price point.

## Telnyx Recommendation

For reliable U.S. SMS, prefer Telnyx over Twilio for NewsAgent's current scale.

### Pricing position

- Telnyx outbound 10DLC SMS base rate: $0.004 per segment, plus carrier fees.
- Typical carrier fee: roughly $0.0035-$0.005 per segment.
- Typical combined rate: roughly $0.0075-$0.009 per segment.
- Telnyx local/toll-free number: around $1/month, plus a small SMS/MMS capability charge.
- 10DLC requires brand/campaign registration and ongoing campaign costs; build these into the operating budget.

### Practical budget at 100 users

| Daily allowance | Approx. monthly Telnyx cost | Product implication |
|---|---:|---|
| 30 segments/user/day | $680-$820 | Original 525-word SMS edition; not viable |
| 5 segments/user/day | $120-$150 | Technically close to $1.50/user, but leaves little/no room for overhead |
| 4 segments/user/day | $95-$120 | Recommended paid-SMS ceiling; leaves room for AI/hosting |
| 1 segment/user/day | $23-$27 | Best SMS teaser/link model |

At four segments, target roughly 610 GSM-safe characters / 85-100 words total. At five segments, target roughly 750 characters / 105-125 words total.

### Telnyx versus Twilio

Telnyx retains the features NewsAgent needs:

- SMS/MMS sending and inbound replies.
- Delivery-status webhooks.
- Standard and configurable STOP/START/HELP handling.
- 10DLC registration.
- Branded link shortening.
- Messaging profiles, number pooling, smart encoding, and spend limits.

Twilio is better mainly for polished dashboards, delivery/click analytics, broad integrations, scheduled-message convenience, and a more mature omnichannel ecosystem. NewsAgent can schedule messages itself with its existing job scheduler, so that difference is not decisive.

## Recommended Product Model

Do not attempt to put the whole edition into SMS. Generate the full edition once, publish it to a private daily webpage, and use each channel as an entry point to the full briefing.

### Default delivery tiers

| Channel | Delivery format | Marginal cost | Role |
|---|---|---:|---|
| Telegram | Full edition in chat | Near $0 | Best initial chat-native channel |
| Mobile/web push | Short alert + deep link | Near $0 | Best iMessage-like app experience |
| WhatsApp Business | Teaser/template + link | Low but not zero | Optional channel; needs opt-in/template compliance |
| Telnyx SMS | Short teaser + link | About $0.23-$0.27/user/month for one segment daily | Paid fallback/add-on |

Suggested onboarding prompt:

> Where should NewsAgent meet you each morning? Telegram, NewsAgent app notifications, or Text message (+$1.50/month).

### Recommended SMS

Use one concise GSM-safe message plus a branded link:

> NewsAgent: Markets rose after earnings, AI spending accelerated, and ceasefire talks resumed. Your full 5-minute briefing: na.example/today

Full stories live on the private briefing page and in free/near-free messaging channels.

## Folk-Style Conversational Assistant

NewsAgent should work as both a scheduled briefing and a one-to-one news assistant.

### User interactions

Users should be able to reply naturally in the same channel:

- "Why did lower inflation help stocks?"
- "Tell me more about the China AI story."
- "What changed since yesterday?"
- "Only send me tech and finance tomorrow."
- "Send my briefing at 8 AM."
- "Pause Culture this week."

### Architecture

1. Morning pipeline generates one structured edition, not only formatted text.
2. Store each story with: title, section, concise blurb, detailed context, source links, source metadata, tags, date, and edition ID.
3. Publish a daily briefing page using the edition ID.
4. Distribution worker sends a channel-appropriate message to each opted-in user.
5. Inbound message webhook normalizes replies from each channel.
6. Conversation router identifies the NewsAgent user and their delivery preferences.
7. Retrieval layer finds the relevant story/context from the current edition and recent editions.
8. Assistant responds in the same conversation.
9. If the request is outside the curated edition, optionally invoke live research under a usage limit.

### Data model sketch

```text
daily_editions
  id, date, published_url, generated_at

stories
  id, edition_id, section, title, blurb, context, source_urls, tags

users
  id, timezone, preferred_delivery_time, plan

delivery_channels
  id, user_id, channel, address_or_chat_id, opted_in, verified, enabled

preferences
  user_id, enabled_sections, verbosity, topics, exclusions

conversations
  id, user_id, channel, external_thread_id, last_edition_id

messages
  id, conversation_id, direction, content, created_at
```

### Cost controls

- Generate the daily edition once for everyone.
- Make questions about the current/recent curated edition included.
- Limit or meter expensive live-web-research questions.
- Keep SMS replies concise or route detailed answers to the briefing link.
- Offer Telegram and push/web briefing access as the included full-content experience.
- Consider annual billing for SMS users to reduce fixed payment-processing fees.

## iMessage and Folk

Folk publicly markets a personal AI that works in iMessage, Telegram, and WhatsApp after a user texts or supplies a phone number. It demonstrates that the desired experience exists in the market.

However, ordinary programmatic iMessage sending is not a standard, broadly available Apple server API for daily broadcast products. A Folk-style implementation likely depends on an iMessage-enabled Apple account/device environment or a specialized bridge. The exact implementation is not publicly documented.

Implications for NewsAgent:

- iMessage can be explored as an opt-in, invite-only beta channel.
- The transport must support both sending and receiving replies for assistant behavior.
- Do not promise iMessage delivery to paying users until the provider/bridge has been tested for reliability and permitted use.
- Daily distribution to many subscribers carries a greater risk of throttling/blocking than a personal conversational assistant.
- Obtain explicit user consent and maintain opt-out/suppression handling regardless of the channel.

The reliable version of the product does not depend on iMessage: Telegram, push, WhatsApp, and Telnyx SMS all use the same conversation/assistant backend.

## Subscriber Website

The Folk-style product includes an authenticated website that acts as a control
center and the destination for full daily briefings. It is not an email
newsletter surface.

The website should let subscribers:

- Enable or disable Telegram, SMS, push, and future supported messaging channels.
- Set a timezone, delivery days, and preferred delivery time.
- Choose enabled sections, topics, exclusions, and message verbosity.
- Pause and resume delivery, manage consent, and view channel opt-in status.
- Open private current and recent briefing pages from a message link.

The website and inbound conversational commands must update the same
preferences. For example, "Only send tech and finance tomorrow" in Telegram
should change the same settings a subscriber can edit on the website.

## Build Order

1. Refactor the existing daily generation pipeline to persist structured editions and sources.
2. Add a private daily briefing page and stable daily URL.
3. Add the subscriber website and shared delivery-preference controls.
4. Add Telegram bot delivery and inbound-reply handling.
5. Add the one-to-one retrieval assistant grounded in saved editions.
6. Add Telnyx 10DLC as a paid SMS teaser/link channel.
7. Add iOS/PWA push notifications.
8. Consider WhatsApp and experimental iMessage only after the core system is stable.

## Sources Consulted

- OpenAI API pricing: https://developers.openai.com/api/docs/pricing
- Twilio U.S. SMS pricing: https://www.twilio.com/en-us/sms/pricing/us
- Twilio SMS character limits: https://www.twilio.com/docs/glossary/what-sms-character-limit
- Telnyx Messaging pricing: https://telnyx.com/pricing/messaging
- Telnyx number pricing: https://telnyx.com/pricing/numbers
- Telnyx opt-in/out management: https://developers.telnyx.com/docs/messaging/messages/advanced-opt-in-out
- Telnyx messaging profiles: https://developers.telnyx.com/docs/messaging/messages/messaging-profiles-overview
- Firebase Cloud Messaging: https://firebase.google.com/products/cloud-messaging
- WhatsApp Business pricing: https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing
- Folk: https://www.folk.com/
