# Category Guidelines

Authoritative inclusion and exclusion policy for briefing category placement. This document is the
single source of truth for category assignment — it is loaded at runtime by the classification
stage (`src/news_agent/classify.py`) and must never be duplicated or reimplemented as a separate
keyword list.

Transcribed from `docs/Category Guidelines.pdf`.

---

## 1. Business + Tech

Use this for stories about **companies, industries, startups, technology, and how businesses
operate**.

Include:

- Major company announcements, acquisitions, layoffs, bankruptcies, and leadership changes
- New products, platforms, devices, and software
- AI, semiconductors, cybersecurity, cloud computing, robotics, biotech, and space technology
- Startup funding and venture capital
- Antitrust cases and regulations primarily affecting companies or technology
- Major labor developments involving specific companies or industries
- Business-model changes, such as subscription pricing or advertising strategies
- Corporate scandals with significant operational or industry consequences

Examples:

- OpenAI releases a new model
- Apple acquires an AI startup
- Amazon cuts 15,000 jobs
- The EU introduces new rules for social-media platforms
- Natural energy companies have their federal tax rate cut 3% by new regulation
- A cyberattack disrupts Microsoft services
- Tesla opens a new factory

Do **not** place a story here solely because it mentions a public company. A story about Tesla
stock falling 15% belongs primarily in **Finance** unless the important development is the
business event that caused the decline.

---

## 2. U.S. News

Use this for stories primarily concerning **American government, politics, law, public safety,
society, infrastructure, or national policy**.

Include:

- The president, Congress, federal agencies, and the Supreme Court
- Elections, campaigns, polling, and major political controversies
- Federal and state legislation
- Immigration, education, healthcare, housing, and criminal-justice policy
- Major crimes, terrorism cases, investigations, and national-security threats inside the U.S.
- Natural disasters and emergencies affecting the United States
- Major protests, strikes, and social movements
- Infrastructure failures and transportation disruptions
- U.S. military actions when the main focus is the American government's decision

Examples:

- Congress passes an immigration bill
- The Supreme Court issues a major ruling
- The FBI stops an alleged domestic terror plot
- A hurricane causes widespread damage in Florida
- The president authorizes strikes against Iran
- A major teachers' strike begins in California

A U.S. company story should still go in **Business + Tech** unless its main importance is
governmental or societal.

---

## 3. Global News

Use this for **international politics, wars, diplomacy, humanitarian crises, and important
developments outside the United States**.

Include:

- Wars, military operations, ceasefires, and territorial changes
- Elections and leadership changes outside the U.S.
- International diplomacy, treaties, sanctions, and alliances
- Political unrest, coups, and major protests
- Humanitarian crises, migration, famine, and disease outbreaks
- Natural disasters outside the U.S.
- International organizations such as the UN, NATO, EU, and WHO
- Major foreign policy developments involving multiple countries
- Foreign government actions with broad geopolitical consequences

Examples:

- Iran strikes U.S. bases in the Gulf
- Ukraine attacks a Russian oil refinery
- EU countries advance Ukraine's membership talks
- A new prime minister is elected in the United Kingdom
- China and Taiwan conduct military exercises
- An earthquake causes widespread destruction in Japan

For a conflict involving the United States, classify it according to the main focus:

- **U.S. decision or domestic political consequences:** U.S. News
- **Battlefield developments or international consequences:** Global News

---

## 4. Culture + Media

Use this for stories about **entertainment, sports, creators, social trends, public attention, and
how information or culture spreads**.

Include:

- Film, television, music, books, gaming, and celebrities
- Major sports results and controversies
- Social-media trends, viral stories, and internet culture
- Streaming services and entertainment platforms
- Journalism, newspapers, television networks, and media organizations
- Awards, festivals, major releases, and cultural events
- Creator-economy developments
- Public controversies primarily centered on reputation, speech, or cultural influence
- Changes in consumer behavior, language, fashion, or popular culture

Examples:

- A major film breaks box-office records
- Netflix cancels or renews a high-profile show
- A celebrity faces a major public controversy
- The World Cup final is decided
- TikTok introduces a new creator-payment system
- A media company changes its editorial leadership

A Netflix acquisition belongs in **Business + Tech** if the central issue is corporate strategy. A
hit Netflix show belongs in **Culture + Media**.

---

## 5. Finance

Use this for stories whose main significance is **markets, asset prices, monetary policy,
banking, investment, or the economy's measurable financial performance**.

Include:

- Large moves in stocks, indexes, bonds, currencies, commodities, or crypto
- Federal Reserve and other central-bank decisions
- Inflation, employment, GDP, retail sales, and other major economic data
- Banking crises and financial-system risks
- Earnings reports when they materially move markets
- IPOs, major investment flows, and market valuations
- Interest rates, mortgage rates, and bond yields
- Oil and commodity-price changes
- Hedge funds, private equity, asset managers, and major investors
- Market reactions to political or geopolitical events

Examples:

- The S&P 500 falls 3%
- Nvidia shares rise 12% after earnings
- The Federal Reserve cuts interest rates
- Bitcoin crosses a major price level
- Oil prices surge after attacks in the Gulf
- U.S. inflation comes in above expectations

The Finance section should explain:

1. What moved
2. How much it moved
3. Why it moved
4. Why the movement matters
