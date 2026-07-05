"""The fenced-off layers: anything online or executing repository code.

Nothing in this package is reachable from the default ``adduce check``.
Online resolution calls only public metadata endpoints from the user's
machine; ``reproduce`` executes repository code and says so loudly before
doing it.
"""
