# STAMPEDE

Watch wallets move between coins on Robinhood Chain.

STAMPEDE is a read-only map of *observed* wallet rotations between memecoins: a wallet sold token A and
later bought token B inside a chosen time window. Nodes are coins, directed edges are observed
`sell A -> buy B` sequences, and every edge opens to the addresses, timestamps and transaction hashes
behind it.

What an edge is **not**: it is not proof that the money from the sale funded the purchase, not proof
that several addresses share an owner, not a prediction, and not a buy signal.

Work in progress. See `docs/` for the data sources, the algorithm, coverage and verified examples.
