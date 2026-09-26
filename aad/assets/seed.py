"""Standalone AHC001 submission, standard library only."""
import math
import random
import sys

# AAD-BEGIN parameters
ROUNDS = 12
STEP = 48
RESTARTS = 1
MOVES = 0
TEMPERATURE = 0.003
SEED = 17
# AAD-END parameters


def satisfaction(area, target):
    ratio = min(area, target) / max(area, target)
    return 1 - (1 - ratio) ** 2


def valid(rect, i, rectangles, points):
    a, b, c, d = rect
    x, y, _ = points[i]
    if not (0 <= a <= x < c <= 10000 and 0 <= b <= y < d <= 10000):
        return False
    for j, (e, f, g, h) in enumerate(rectangles):
        if i != j and a < g and e < c and b < h and f < d:
            return False
    return True


def expand(rect, i, direction, step, rectangles, points):
    a, b, c, d = rect
    target = points[i][2]
    width, height = c - a, d - b
    side = height if direction % 2 == 0 else width
    available = (target - width * height) // side
    amount = min(step, available)
    if amount <= 0:
        return rect
    amount = min(amount, (a, b, 10000 - c, 10000 - d)[direction])
    for j, (e, f, g, h) in enumerate(rectangles):
        if j == i:
            continue
        if direction == 0 and b < h and f < d and g <= a:
            amount = min(amount, a - g)
        elif direction == 2 and b < h and f < d and c <= e:
            amount = min(amount, e - c)
        elif direction == 1 and a < g and e < c and h <= b:
            amount = min(amount, b - h)
        elif direction == 3 and a < g and e < c and d <= f:
            amount = min(amount, f - d)
    proposal = list(rect)
    proposal[direction] += amount * (-1 if direction < 2 else 1)
    return proposal


# AAD-BEGIN priority
def ordering(points, rectangles, rng):
    order = list(range(len(points)))
    rng.shuffle(order)
    return order
# AAD-END priority


# AAD-BEGIN search
def solve(points):
    rng = random.Random(SEED)
    rectangles = [[x, y, x + 1, y + 1] for x, y, _ in points]
    for sweep in range(ROUNDS):
        for i in ordering(points, rectangles, rng):
            directions = list(range(4))
            rng.shuffle(directions)
            for direction in directions:
                rectangles[i] = expand(rectangles[i], i, direction, STEP, rectangles, points)
    return rectangles
# AAD-END search


def main():
    data = list(map(int, sys.stdin.buffer.read().split()))
    n = data[0]
    points = [tuple(data[1 + 3 * i:4 + 3 * i]) for i in range(n)]
    rectangles = solve(points)
    sys.stdout.write("\n".join(" ".join(map(str, r)) for r in rectangles) + "\n")


if __name__ == "__main__":
    main()
