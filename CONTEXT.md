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

**Event**:
A single real-world happening (e.g. a lithium mine strike) that may affect one or more Holdings, however many news reports describe it.
_Avoid_: Disruption, alert, story, news item

### What the swarm produces

**Thesis**:
The Analyst's cited judgement on one Event, covering every Holding it affects, with a Stance, a recommended action and the expected impact and direction.
_Avoid_: Alert, report, signal, recommendation

**Stance**:
The direction a Thesis takes: Bull, Bear or Neutral.
_Avoid_: Sentiment, rating

**Receipt**:
Evidence for a claim: a source URL, the quoted passage that supports the claim, and when it was retrieved.
_Avoid_: Citation, link, source

### The agents

**Scout**:
The agent that continuously searches news for the Watch Topics and flags relevant items to the swarm.

**Researcher**:
An agent assigned to one Event that investigates its second-order effects and gathers Receipts. Several can run at once, one per Event.

**Analyst**:
The agent that turns a Researcher's findings into a Thesis.
