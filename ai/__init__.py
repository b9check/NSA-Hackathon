"""AI controllers for the wargame.

Per-side controllers that produce orders given a GameState. A controller is
either MANUAL (sentinel — humans submit via UI) or one of the implemented
strategies (random, heuristic, llm). The server picks orders from the active
controller when the side's turn comes due.
"""
