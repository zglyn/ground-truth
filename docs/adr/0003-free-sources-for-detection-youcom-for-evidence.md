# Free news sources find Events; You.com supplies the evidence

The original plan had the Scout polling the You.com News API for every Watch Topic. At about $5 per 1,000 searches, that alone would cost $65 to $540 a month, depending on interval and grouping, which is more than everything else combined. Instead, the Scout checks free sources every 5 minutes: news RSS feeds, Google News RSS per Watch Topic, and SEC EDGAR filings. You.com is used where its verifiable sources matter: one search by the Supervisor to corroborate each new Event before a Researcher starts, and the Research API for the Researcher's investigation and Receipts.

## Consequences

- The pitch changes from "You.com is the eyes" to "You.com is the source of verified evidence".
- The Scout's 5-minute interval plus about 4 minutes of research and analysis gives roughly 7 minutes on average, and under 10 minutes worst case, from a source publishing to a Thesis on the dashboard.
- Free feeds can be slower or patchier than a paid news index for some topics. If coverage gaps appear, a You.com search on specific high-weight Watch Topics can be added back.
- Running cost drops to roughly $10 to $25 a month (LLM sessions and research calls), with the whole stack running on the user's laptop under Docker.
