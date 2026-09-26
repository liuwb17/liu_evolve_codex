"""Handwritten mutation grammar for offline integration tests, NOT an LLM."""

from .blocks import apply_blocks, extract

PRIORITIES = [
    'def ordering(points, rectangles, rng):\n    return sorted(range(len(points)), key=lambda i: points[i][2])\n',
    'def ordering(points, rectangles, rng):\n    return sorted(range(len(points)), key=lambda i: -points[i][2])\n',
    'def ordering(points, rectangles, rng):\n    order = list(range(len(points)))\n    rng.shuffle(order)\n    return order\n',
    'def ordering(points, rectangles, rng):\n    return sorted(range(len(points)), key=lambda i: (rectangles[i][2]-rectangles[i][0])*(rectangles[i][3]-rectangles[i][1])/points[i][2])\n',
]

SEARCH = '''def solve(points):
    rng = random.Random(SEED)
    best, best_score = None, -1.0
    for restart in range(RESTARTS):
        rectangles = [[x, y, x+1, y+1] for x, y, _ in points]
        for sweep in range(ROUNDS):
            for i in ordering(points, rectangles, rng):
                directions = list(range(4))
                rng.shuffle(directions)
                for direction in directions:
                    rectangles[i] = expand(rectangles[i], i, direction, STEP, rectangles, points)
        values = [satisfaction((c-a)*(d-b), points[i][2]) for i,(a,b,c,d) in enumerate(rectangles)]
        total = sum(values)
        if total > best_score:
            best, best_score = [r[:] for r in rectangles], total
        for move in range(MOVES):
            i = rng.randrange(len(points))
            old = rectangles[i]
            proposal = old[:]
            direction = rng.randrange(4)
            span = max(1, int(STEP * (1 - move / max(1, MOVES))))
            delta = rng.randint(1, span) * rng.choice([-1, 1])
            proposal[direction] += delta
            if rng.random() < 0.25:
                proposal[(direction+2)%4] += delta
            if not valid(proposal, i, rectangles, points):
                continue
            a,b,c,d = proposal
            value = satisfaction((c-a)*(d-b), points[i][2])
            gain = value - values[i]
            temp = max(1e-9, TEMPERATURE * (1-move/max(1, MOVES))**2)
            if gain >= 0 or rng.random() < math.exp(max(-700, gain/temp)):
                rectangles[i] = proposal
                values[i] = value
                total += gain
                if total > best_score:
                    best, best_score = [r[:] for r in rectangles], total
    return best
'''


class OfflineProposer:
    def propose(self, parent, donor, operator, feedback, rng, remaining_requests=None):
        parts = extract(parent)
        if operator == "crossover":
            name = rng.choice(list(parts))
            replacements = {name: extract(donor)[name]}
            hypothesis = f"Inherit {name} block from another archive member"
        elif operator == "priority":
            replacements = {"priority": rng.choice(PRIORITIES)}
            hypothesis = "Change expansion order to reduce competition for space"
        elif operator == "search":
            replacements = {"search": SEARCH}
            hypothesis = "Introduce multi-start construction and annealed rectangle moves"
        else:
            # This grammar changes complete named code blocks. It is deliberately
            # bounded: offline success is not evidence of open-ended LLM discovery.
            rounds = rng.choice([8, 16, 24, 32])
            step = rng.choice([24, 48, 96, 160, 256])
            replacements = {"parameters": f"ROUNDS = {rounds}\nSTEP = {step}\nRESTARTS = {rng.choice([1, 2, 3])}\nMOVES = {rng.choice([0, 1500, 6000])}\nTEMPERATURE = {rng.choice([0.001, 0.005, 0.015])}\nSEED = {rng.randrange(1, 10000)}\n"}
            hypothesis = "Explore construction scale, restart count and search schedule"
            if operator == "rewrite":
                replacements.update(search=SEARCH, priority=rng.choice(PRIORITIES))
                hypothesis = "Combine a new ordering policy with multi-start annealing"
        return {"code": apply_blocks(parent, replacements), "hypothesis": hypothesis,
                "blocks": list(replacements), "requests": 0, "usage": {}, "provider": "offline-grammar"}
