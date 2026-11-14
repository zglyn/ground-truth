# The Supervisor is the only thing that creates Events

The original plan had Researchers watch the board and claim Sightings themselves. The OpenCrawl board delivers messages at least once, in batches, and has no locking. Two Researchers could therefore claim Sightings of the same happening at once and open duplicate Events. Instead, the Supervisor reads the board, matches each Sighting to an existing Event (or dismisses it with a reason), creates new Events, and starts one Researcher per Event. Researchers never claim work themselves.

## Consequences

- The Supervisor is a single point of failure for the pipeline, so it must restart cleanly and rebuild its view from the board.
- "Never repeat the same You.com query" is handled by a separate query log that Researchers check before each call, not by OpenCrawl search. Keyword-only search and the delay before new sessions become searchable make OpenCrawl unreliable for this.
