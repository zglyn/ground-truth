# Market Impact Swarm

An autonomous team of agents that watches a user's holdings, reacts to breaking news, investigates its downstream market impact, and delivers a cited trading thesis.

## Language

### The user's exposure

**Portfolio**:
The set of Holdings belonging to one user.
_Avoid_: Watchlist, book

**Holding**:
A ticker the user owns, together with its weight in the Portfolio.
_Avoid_: Position, stock, watchlist item

**Watch Topic**:
A free-text theme the Scout searches news for (e.g. "EV Supply Chain"), linked to the Holdings it can affect.
_Avoid_: Customized field, keyword, interest

### What happens in the world

**Sighting**:
One relevant news item the Scout has posted to the swarm, not yet confirmed as part of an Event. The Supervisor attaches it to an Event or dismisses it with a reason.
_Avoid_: Flag, alert, hit

**Event**:
A single real-world happening (e.g. a lithium mine strike) that may affect one or more Holdings, however many Sightings describe it. It closes after 72 hours without a new Sighting; a later mention opens a new, related Event.
_Avoid_: Disruption, alert, story, news item

### What the swarm produces

**Thesis**:
The Analyst's cited judgement on one Event, made up of a Holding Impact for every Holding the Event affects. A follow-up on the same Event produces a revised Thesis linked to the earlier one.
_Avoid_: Alert, report, signal, recommendation

**Holding Impact**:
The part of a Thesis about one Holding: its Stance, recommended Action, expected impact and direction, how the market has already reacted, and confidence.
_Avoid_: Verdict, call, rating

**Stance**:
The direction a Holding Impact takes: Bull, Bear or Neutral.
_Avoid_: Sentiment, rating

**Action**:
What a Holding Impact recommends the user do: Add, Hold, Trim, Exit or Hedge. Advice only, never executed.
_Avoid_: Trade, order, signal

**Dossier**:
A Researcher's complete record of findings and Receipts for one Event, which the Analyst reads to write the Thesis.
_Avoid_: Findings, research notes, summary

**Receipt**:
Evidence for a claim: a source URL, the quoted passage that supports the claim (confirmed word-for-word against the page), and when it was retrieved.
_Avoid_: Citation, link, source

**Track Record**:
The running grade of past Holding Impacts, comparing each Stance with the Holding's actual price move 1, 5 and 20 trading days later.
_Avoid_: Backtest, accuracy, score

**Inference**:
A claim in a Thesis that has no Receipt and is labelled as the Analyst's own reasoning.
_Avoid_: Assumption, opinion

### The agents

**Supervisor**:
The coordinating process that keeps the Scout running, matches Sightings to Events, and starts a Researcher and then the Analyst for each Event. It is the only thing that creates Events.
_Avoid_: Orchestrator, dispatcher, manager

**Scout**:
The agent that continuously checks free news sources (news feeds and regulatory filings) for the Watch Topics and posts Sightings.

**Researcher**:
An agent assigned to one Event by the Supervisor, which investigates the Event's second-order effects, gathers Receipts and writes a Dossier. Several can run at once, one per Event.

**Analyst**:
The agent that turns a Researcher's Dossier into a Thesis.
