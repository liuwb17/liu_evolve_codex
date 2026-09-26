"""Small independent islands, behavior niches and periodic elite migration."""

import math


class Archive:
    def __init__(self, islands=3, cells=None):
        self.islands = islands
        self.cells = cells if cells is not None else [{} for _ in range(islands)]

    @staticmethod
    def niche(evaluation):
        # Environment-observed behavior, never a self-reported tag from the LLM.
        coverage = min(4, int(evaluation["coverage"] * 5))
        weak = min(4, int(evaluation["weak_fraction"] * 5))
        return f"{coverage}:{weak}"

    def add(self, candidate, records, island):
        if not candidate["train"]["valid"]:
            return False
        niche = self.niche(candidate["train"])
        previous = self.cells[island].get(niche)
        if previous is None or candidate["train"]["selection_score"] > records[previous]["train"]["selection_score"]:
            self.cells[island][niche] = candidate["id"]
            return True
        return False

    def select(self, records, island, rng):
        ids = sorted(set(self.cells[island].values()))
        if not ids:
            ids = sorted({i for cells in self.cells for i in cells.values()})
        if rng.random() < 0.25:
            return rng.choice(ids)
        contenders = rng.sample(ids, min(3, len(ids)))
        return max(contenders, key=lambda i: records[i]["train"]["selection_score"])

    def migrate(self, records):
        elites = [max(set(cells.values()), key=lambda i: records[i]["train"]["selection_score"])
                  for cells in self.cells if cells]
        for island, elite in enumerate(elites):
            self.add(records[elite], records, (island + 1) % self.islands)


class OperatorBandit:
    """UCB1 over successful proposals; failures consume a pull with reward zero."""

    names = ("parameters", "priority", "search", "crossover", "rewrite")

    def __init__(self, state=None):
        self.state = state or {name: [0, 0.0] for name in self.names}

    def choose(self, rng):
        unseen = [k for k, v in self.state.items() if v[0] == 0]
        if unseen:
            return rng.choice(unseen)
        total = sum(v[0] for v in self.state.values())
        return max(self.names, key=lambda k: self.state[k][1] / self.state[k][0]
                   + math.sqrt(2 * math.log(total) / self.state[k][0]))

    def update(self, name, reward):
        self.state[name][0] += 1
        self.state[name][1] += max(0.0, min(1.0, reward))
